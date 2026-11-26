<script setup lang="ts">
import { AlertTriangle, ArrowLeftRight, ChevronLeft, ChevronRight, Clock, Plus } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import JobSheet from "../components/JobSheet.vue";
import ModelThumb from "../components/ModelThumb.vue";
import ScheduleSheet, { type SchedulePreset } from "../components/ScheduleSheet.vue";
import {
  addDays,
  duration,
  offPeakSegments,
  sameDay,
  shortDay,
  startOfDay,
  startOfWeek,
  time,
  toInputs,
  when,
} from "../format";
import { attempt, loadAttention, loadPrinters, loadSettings, loadSpools, spoolById, state, toast, usePolling } from "../store";
import type { Job, Printer } from "../types";

const HOUR_PX = 44;
const SNAP_MIN = 15;
const MOVABLE = ["scheduled", "needs_attention", "awaiting_confirmation", "powering_on"];

const route = useRoute();
const router = useRouter();

// ---- week in the URL (?semaine=2026-11-23) -----------------------------------
const weekStart = computed(() => {
  const q = route.query.semaine;
  const d = typeof q === "string" ? new Date(`${q}T00:00:00`) : new Date();
  return startOfWeek(isNaN(d.getTime()) ? new Date() : d);
});
const days = computed(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart.value, i)));
const isCurrentWeek = computed(() => sameDay(weekStart.value, startOfWeek(new Date())));
function goWeek(delta: number) {
  const target = delta === 0 ? startOfWeek(new Date()) : addDays(weekStart.value, 7 * delta);
  router.replace({ query: { ...route.query, semaine: delta === 0 ? undefined : toInputs(target).date } });
}
const weekLabel = computed(() => {
  const fmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long" });
  return `Semaine du ${fmt.format(weekStart.value)}`;
});

// single-day view on narrow screens
const narrow = ref(false);
const dayIndex = ref((new Date().getDay() + 6) % 7);
const mq = window.matchMedia("(max-width: 60rem)");
const syncNarrow = () => (narrow.value = mq.matches);
onMounted(() => {
  syncNarrow();
  mq.addEventListener("change", syncNarrow);
});
onBeforeUnmount(() => mq.removeEventListener("change", syncNarrow));
const visibleDays = computed(() => (narrow.value ? [days.value[dayIndex.value]] : days.value));

// ---- data ----------------------------------------------------------------------
const jobs = ref<Job[]>([]);
const loaded = ref(false);
const failed = ref(false);

async function loadJobs() {
  const from = addDays(weekStart.value, -1).toISOString();
  const to = addDays(weekStart.value, 8).toISOString();
  try {
    jobs.value = await api.get<Job[]>(`/jobs?start=${encodeURIComponent(from)}&end=${encodeURIComponent(to)}`);
    failed.value = false;
  } catch {
    failed.value = true;
  }
  loaded.value = true;
}
async function refresh() {
  await Promise.all([loadJobs(), loadPrinters(), loadAttention(), loadSpools().catch(() => undefined)]);
}
usePolling(refresh, 5000);
onMounted(() => {
  if (!state.settings) attempt(loadSettings);
});
watch(weekStart, () => {
  loaded.value = false;
  loadJobs();
});

const printers = computed(() => state.printers);
const offPeak = computed(() => offPeakSegments(state.settings?.off_peak ?? []));

// ---- layout --------------------------------------------------------------------
interface Segment {
  job: Job;
  top: number;
  height: number;
  startsHere: boolean;
}

// a print stopped along the way stays visible (how far it went); one cancelled before
// printing anything leaves the planning
function onPlanning(job: Job): boolean {
  return job.status !== "cancelled" || (job.progress_end ?? 0) > 0;
}

// "arrêtée à 83 %" for a print that ended before its end
function stoppedLabel(job: Job): string {
  if (job.progress_end == null || job.progress_end >= 1) return "";
  const pct = `${Math.round(job.progress_end * 100)} %`;
  if (job.status === "cancelled") return `arrêtée à ${pct}`;
  if (job.status === "failed") return `échec à ${pct}`;
  return "";
}

function segmentsFor(day: Date, printerId: number): Segment[] {
  const dayStart = startOfDay(day).getTime();
  const dayEnd = addDays(day, 1).getTime();
  const out: Segment[] = [];
  for (const job of jobs.value) {
    if (job.printer_id !== printerId || !onPlanning(job)) continue;
    const s = new Date(job.start_at).getTime();
    const e = Math.max(new Date(job.end_at).getTime(), s + 15 * 60_000);
    if (e <= dayStart || s >= dayEnd) continue;
    const from = Math.max(s, dayStart);
    const to = Math.min(e, dayEnd);
    out.push({
      job,
      top: ((from - dayStart) / 3_600_000) * HOUR_PX,
      height: Math.max(((to - from) / 3_600_000) * HOUR_PX, 22),
      startsHere: s >= dayStart,
    });
  }
  return out;
}

// ---- prints running now (Printy's or started from the slicer / screen) ----------
interface LivePrint {
  printer: Printer;
  job: Job | null;
  name: string;
  start: Date;
  end: Date | null; // unknown while the printer heats and calibrates (outside Printy)
  progress: number;
  remaining: number | null;
  paused: boolean;
}
const livePrints = computed<LivePrint[]>(() =>
  printers.value
    .filter((p) => p.live.state === "printing" || p.live.state === "paused")
    .map((p) => {
      const job = jobs.value.find((j) => j.id === p.live.job_id) ?? null;
      const file = (p.live.file ?? "Impression")
        .split("/")
        .pop()!
        .replace(/\.gcode(\.3mf)?$/i, "")
        .replace(/^printy-\d+-/, "");
      return {
        printer: p,
        job,
        name: job?.model_name ?? file,
        start: new Date(p.live.print_started_at ?? Date.now()),
        // the printer's estimate; while it has none, the planned end of Printy's print
        end: p.live.print_ends_at ? new Date(p.live.print_ends_at) : job ? new Date(job.end_at) : null,
        progress: p.live.progress ?? 0,
        remaining: p.live.remaining_s,
        paused: p.live.state === "paused",
      };
    }),
);
/** Prints started outside Printy: drawn in the grid from the printer's own figures. */
function externalFor(day: Date, printerId: number) {
  const lp = livePrints.value.find((l) => l.printer.id === printerId && !l.job);
  if (!lp) return null;
  const dayStart = startOfDay(day).getTime();
  const dayEnd = addDays(day, 1).getTime();
  const s = lp.start.getTime();
  const e = Math.max(lp.end?.getTime() ?? 0, Date.now() + 5 * 60_000);
  if (e <= dayStart || s >= dayEnd) return null;
  const from = Math.max(s, dayStart);
  const to = Math.min(e, dayEnd);
  return {
    lp,
    top: ((from - dayStart) / 3_600_000) * HOUR_PX,
    height: Math.max(((to - from) / 3_600_000) * HOUR_PX, 22),
    startsHere: s >= dayStart,
  };
}
function openLive(lp: LivePrint) {
  if (lp.job) jobId.value = lp.job.id;
  else router.push("/imprimantes");
}

const now = ref(new Date());
const clock = window.setInterval(() => (now.value = new Date()), 30_000);
onBeforeUnmount(() => window.clearInterval(clock));
const nowTop = computed(() => ((now.value.getHours() * 60 + now.value.getMinutes()) / 60) * HOUR_PX);

// The page itself scrolls through the hours. On opening, jump close to the current
// hour, unless something above the board (to check, printing now) should be seen first.
const scroller = ref<HTMLElement | null>(null);
let jumped = false;
watch([scroller, loaded], async ([el, ready]) => {
  if (jumped || !el || !ready) return;
  jumped = true;
  await nextTick();
  if (state.attention.length || livePrints.value.length || !scroller.value) return;
  const hour = isCurrentWeek.value ? Math.max(0, now.value.getHours() - 2) : 7;
  const sticky = scroller.value.previousElementSibling?.getBoundingClientRect().height ?? 0;
  const topbar = document.querySelector(".topbar")?.getBoundingClientRect().height ?? 0;
  const y = scroller.value.getBoundingClientRect().top + window.scrollY + hour * HOUR_PX - sticky - topbar;
  if (y > 0) window.scrollTo({ top: y });
});

function blockClass(job: Job) {
  return {
    "block--attention": job.status === "awaiting_confirmation" || job.status === "needs_attention",
    "block--active": ["printing", "paused", "starting", "powering_on", "ready"].includes(job.status),
    "block--done": job.status === "finished",
    "block--failed": job.status === "failed",
    "block--stopped": job.status === "cancelled",
    "block--movable": MOVABLE.includes(job.status),
  };
}

// ---- sheets --------------------------------------------------------------------
const scheduleOpen = ref(false);
const preset = ref<SchedulePreset>({});
const jobId = ref<number | null>(null);

function openSchedule(p: SchedulePreset = {}) {
  preset.value = p;
  scheduleOpen.value = true;
}
function onCreated(job: Job) {
  scheduleOpen.value = false;
  const soon = new Date(job.start_at).getTime() < Date.now() + 2 * 60_000;
  toast(soon ? `« ${job.model_name} » part à l’impression.` : `« ${job.model_name} » planifiée ${when(job.start_at)}.`, "info", {
    label: "Voir",
    run: () => (jobId.value = job.id),
  });
  const ws = startOfWeek(new Date(job.start_at));
  if (!sameDay(ws, weekStart.value)) router.replace({ query: { semaine: toInputs(ws).date } });
  refresh();
}

// Deep links from the library: ?planifier=<modelId>&version=<slicedId>
watch(
  () => route.query.planifier,
  (v) => {
    if (typeof v !== "string") return;
    openSchedule({ modelId: Number(v), slicedId: route.query.version ? Number(route.query.version) : undefined });
    router.replace({ query: { ...route.query, planifier: undefined, version: undefined } });
  },
  { immediate: true },
);
watch(
  () => route.query.impression,
  (v) => {
    if (typeof v === "string") jobId.value = Number(v);
  },
  { immediate: true },
);
function closeJob() {
  jobId.value = null;
  if (route.query.impression) router.replace({ query: { ...route.query, impression: undefined } });
}

// ---- click on an empty slot, drag to move ----------------------------------------
function minutesAt(lane: HTMLElement, clientY: number): number {
  const rect = lane.getBoundingClientRect();
  const raw = ((clientY - rect.top) / HOUR_PX) * 60;
  return Math.max(0, Math.min(1440 - SNAP_MIN, Math.round(raw / SNAP_MIN) * SNAP_MIN));
}

function onLaneClick(e: MouseEvent, day: Date, printerId: number) {
  if (dragJustEnded) return;
  const lane = e.currentTarget as HTMLElement;
  const m = minutesAt(lane, e.clientY);
  const start = startOfDay(day);
  start.setMinutes(Math.floor(m / 60) * 60); // a click picks the hour
  openSchedule({ start, printerId });
}

interface Drag {
  job: Job;
  pointerId: number;
  startX: number;
  startY: number;
  grabMin: number;
  touch: boolean; // finger: the block moves only after a long press, a swipe scrolls the page
  armed: boolean;
  active: boolean;
  target: { day: Date; printerId: number; minutes: number } | null;
}
const drag = ref<Drag | null>(null);
let dragJustEnded = false;
let pressTimer: number | undefined;
const LONG_PRESS_MS = 400;

function onBlockDown(e: PointerEvent, seg: Segment) {
  if (e.button !== 0 || !MOVABLE.includes(seg.job.status) || !seg.startsHere) return;
  const lane = (e.currentTarget as HTMLElement).closest<HTMLElement>("[data-lane]")!;
  const startMin = Math.round(seg.top / HOUR_PX * 60);
  const touch = e.pointerType !== "mouse";
  drag.value = {
    job: seg.job,
    pointerId: e.pointerId,
    startX: e.clientX,
    startY: e.clientY,
    grabMin: ((e.clientY - lane.getBoundingClientRect().top) / HOUR_PX) * 60 - startMin,
    touch,
    armed: !touch,
    active: false,
    target: null,
  };
  if (touch) {
    window.clearTimeout(pressTimer);
    pressTimer = window.setTimeout(() => {
      if (drag.value && !drag.value.active) {
        drag.value.armed = true;
        navigator.vibrate?.(15);
      }
    }, LONG_PRESS_MS);
  }
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", stopDrag);
}

// the page scrolls (or the finger moved before the long press): no move, no opening
function stopDrag() {
  window.clearTimeout(pressTimer);
  window.removeEventListener("pointermove", onMove);
  window.removeEventListener("pointerup", onUp);
  window.removeEventListener("pointercancel", stopDrag);
  drag.value = null;
}

// once a block is held, the finger moves it instead of scrolling the page
function holdScroll(e: TouchEvent) {
  if (drag.value?.armed) e.preventDefault();
}
onMounted(() => window.addEventListener("touchmove", holdScroll, { passive: false }));
onBeforeUnmount(() => {
  window.removeEventListener("touchmove", holdScroll);
  stopDrag();
});

function onMove(e: PointerEvent) {
  const d = drag.value;
  if (!d || e.pointerId !== d.pointerId) return;
  const moved = Math.hypot(e.clientX - d.startX, e.clientY - d.startY);
  if (!d.armed) {
    if (moved > 8) stopDrag(); // a swipe: let the page scroll
    return;
  }
  if (!d.active && moved < 6) return;
  d.active = true;
  const lane = document
    .elementsFromPoint(e.clientX, e.clientY)
    .find((el) => (el as HTMLElement).dataset?.lane) as HTMLElement | undefined;
  if (!lane) return;
  const day = new Date(Number(lane.dataset.day));
  const rect = lane.getBoundingClientRect();
  const raw = ((e.clientY - rect.top) / HOUR_PX) * 60 - d.grabMin;
  const minutes = Math.max(0, Math.min(1440 - SNAP_MIN, Math.round(raw / SNAP_MIN) * SNAP_MIN));
  d.target = { day, printerId: Number(lane.dataset.printer), minutes };
}

async function onUp(e: PointerEvent) {
  const d = drag.value;
  if (!d || e.pointerId !== d.pointerId) return;
  stopDrag();
  if (!d.active) {
    jobId.value = d.job.id;
    return;
  }
  dragJustEnded = true;
  setTimeout(() => (dragJustEnded = false), 50);
  if (!d.target) return;
  const start = startOfDay(d.target.day);
  start.setMinutes(d.target.minutes);
  if (start.getTime() === new Date(d.job.start_at).getTime() && d.target.printerId === d.job.printer_id) return;
  const previous = { start_at: d.job.start_at, printer_id: d.job.printer_id };
  const res = await attempt(() =>
    api.patch<Job>(`/jobs/${d.job.id}`, { start_at: start.toISOString(), printer_id: d.target!.printerId }),
  );
  if (res) {
    toast(`Déplacée ${when(res.start_at)}.`, "info", {
      label: "Annuler",
      run: () => attempt(() => api.patch(`/jobs/${d.job.id}`, previous)).then(refresh),
    });
  }
  refresh();
}

function ghostStyle(day: Date, printerId: number) {
  const t = drag.value?.target;
  if (!drag.value?.active || !t || t.printerId !== printerId || !sameDay(t.day, day)) return null;
  return {
    top: `${(t.minutes / 60) * HOUR_PX}px`,
    height: `${Math.max((drag.value.job.duration_s / 3600) * HOUR_PX, 22)}px`,
  };
}
function onBlockClick(seg: Segment) {
  // Movable blocks open on pointer up (see onUp); the others open on click.
  if (!MOVABLE.includes(seg.job.status) || !seg.startsHere) jobId.value = seg.job.id;
}
function onBlockKey(e: KeyboardEvent, job: Job) {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    jobId.value = job.id;
  }
}

const weekJobs = computed(() =>
  jobs.value.filter((j) => {
    const s = new Date(j.start_at);
    return s >= weekStart.value && s < addDays(weekStart.value, 7) && j.status !== "cancelled";
  }),
);
const weekHours = computed(() => weekJobs.value.reduce((acc, j) => acc + j.duration_s, 0));
</script>

<template>
  <div class="page planning">
    <header class="page-head">
      <div class="page-head__title">
        <h1>{{ weekLabel }}</h1>
        <p class="muted small">
          <template v-if="weekJobs.length">
            <span class="mono">{{ weekJobs.length }}</span> impression{{ weekJobs.length > 1 ? "s" : "" }},
            <span class="mono">{{ duration(weekHours) }}</span> de machine
          </template>
          <template v-else>Rien de planifié pour l’instant.</template>
        </p>
      </div>
      <div class="page-head__actions">
        <div class="weeknav" role="group" aria-label="Changer de semaine">
          <button type="button" class="btn btn--ghost btn--icon" aria-label="Semaine précédente" @click="goWeek(-1)">
            <ChevronLeft aria-hidden="true" />
          </button>
          <button type="button" class="btn btn--ghost" :disabled="isCurrentWeek" @click="goWeek(0)">Aujourd’hui</button>
          <button type="button" class="btn btn--ghost btn--icon" aria-label="Semaine suivante" @click="goWeek(1)">
            <ChevronRight aria-hidden="true" />
          </button>
        </div>
        <button type="button" class="btn btn--primary" :disabled="!printers.length" @click="openSchedule()">
          <Plus aria-hidden="true" />Planifier
        </button>
      </div>
    </header>

    <!-- Waiting for the user -->
    <section v-if="state.attention.length" class="attention" aria-label="À traiter">
      <button
        v-for="j in state.attention"
        :key="j.id"
        type="button"
        class="attention__item"
        @click="jobId = j.id"
      >
        <ModelThumb :sliced-id="j.has_thumbnail ? j.sliced_file_id : null" size="sm" class="attention__thumb" />
        <span class="attention__text">
          <strong>{{ j.status === "awaiting_confirmation" ? "Plateau à confirmer" : "À revoir" }}</strong>
          <span>{{ j.model_name }}, {{ state.printers.find((p) => p.id === j.printer_id)?.name }}, {{ when(j.start_at) }}</span>
          <span v-if="j.message" class="small">{{ j.message }}</span>
        </span>
        <span class="btn btn--sm attention__cta">Vérifier</span>
      </button>
    </section>

    <!-- Running now -->
    <section v-if="livePrints.length" class="running" aria-label="En cours">
      <button v-for="lp in livePrints" :key="lp.printer.id" type="button" class="running__item" @click="openLive(lp)">
        <span class="running__head">
          <strong>{{ lp.printer.name }}</strong>
          <span class="status" :class="lp.paused ? 'status--warn' : 'status--active'">{{ lp.paused ? "En pause" : "En impression" }}</span>
          <span v-if="!lp.job" class="muted small">lancée hors Printy</span>
        </span>
        <span class="running__name">{{ lp.name }}</span>
        <span class="progress running__bar" role="progressbar" :aria-valuenow="Math.round(lp.progress * 100)" aria-valuemin="0" aria-valuemax="100">
          <span class="progress__bar" :style="{ transform: `scaleX(${lp.progress})` }" />
        </span>
        <span class="running__figures small">
          <template v-if="lp.remaining == null && lp.progress < 0.01">
            <span>Préparation (chauffe, calibrage)</span>
          </template>
          <template v-else>
            <span class="mono">{{ Math.round(lp.progress * 100) }} %</span>
            <span v-if="lp.remaining != null">reste <span class="mono">{{ duration(lp.remaining) }}</span></span>
          </template>
          <span v-if="lp.end">fin vers <span class="mono">{{ time(lp.end) }}</span></span>
        </span>
      </button>
    </section>

    <!-- No printer yet -->
    <div v-if="state.printersLoaded && !printers.length" class="empty">
      <h2>Commence par ajouter ton imprimante</h2>
      <p>Le planning s’organise par imprimante. Ajoute ta K2, sa prise connectée et sa caméra, puis reviens planifier ta semaine.</p>
      <RouterLink to="/imprimantes?ajouter=1" class="btn btn--primary">Ajouter une imprimante</RouterLink>
    </div>

    <div v-else-if="failed && !loaded" class="empty empty--error">
      <h2>Planning indisponible</h2>
      <p>Printy ne répond pas. Vérifie que l’add-on tourne, puis réessaie.</p>
      <button type="button" class="btn btn--secondary" @click="refresh">Réessayer</button>
    </div>

    <template v-else>
      <div class="week card" :class="{ 'is-dragging': drag?.active }" :style="{ '--days': visibleDays.length }">
        <!-- stays at the top while the page scrolls through the hours -->
        <div class="week__top">
        <div v-if="narrow" class="daytabs segmented" role="group" aria-label="Jour">
          <button
            v-for="(d, i) in days"
            :key="i"
            type="button"
            :aria-pressed="i === dayIndex"
            @click="dayIndex = i"
          >
            {{ shortDay(d) }}
          </button>
        </div>
        <div class="week__head">
          <div class="week__corner">
            <span v-if="offPeak.length" class="legend"><span class="legend__box" />HC</span>
          </div>
          <div
            v-for="d in visibleDays"
            :key="d.getTime()"
            class="dayhead"
            :class="{ 'is-today': sameDay(d, now) }"
          >
            <span class="dayhead__name">{{ shortDay(d) }}</span>
            <div v-if="printers.length > 1" class="dayhead__lanes">
              <span v-for="p in printers" :key="p.id">{{ p.name }}</span>
            </div>
          </div>
        </div>
        </div>

        <div ref="scroller" class="week__scroll">
          <div class="week__body" :style="{ height: `${24 * HOUR_PX}px` }">
            <div class="hours" aria-hidden="true">
              <span v-for="h in 23" :key="h" :style="{ top: `${h * HOUR_PX}px` }">{{ String(h).padStart(2, "0") }}:00</span>
            </div>

            <div v-for="d in visibleDays" :key="d.getTime()" class="day" :class="{ 'is-today': sameDay(d, now) }">
              <div
                v-for="([s, e], i) in offPeak"
                :key="i"
                class="offpeak"
                :style="{ top: `${(s / 60) * HOUR_PX}px`, height: `${((e - s) / 60) * HOUR_PX}px` }"
                aria-hidden="true"
              />
              <div v-if="sameDay(d, now)" class="now" :style="{ top: `${nowTop}px` }" aria-hidden="true">
                <span class="now__label mono">{{ time(now) }}</span>
              </div>

              <div
                v-for="p in printers"
                :key="p.id"
                class="lane"
                data-lane="1"
                :data-day="d.getTime()"
                :data-printer="p.id"
                :title="`Planifier sur ${p.name}`"
                @click.self="onLaneClick($event, d, p.id)"
              >
                <div
                  v-for="seg in segmentsFor(d, p.id)"
                  :key="`${seg.job.id}-${seg.top}`"
                  class="block"
                  :class="[blockClass(seg.job), { 'is-ghosted': drag?.active && drag.job.id === seg.job.id, 'is-held': drag?.touch && drag.armed && !drag.active && drag.job.id === seg.job.id, 'block--swap': seg.startsHere && seg.job.spool_change }]"
                  :style="{ top: `${seg.top}px`, height: `${seg.height}px`, '--filament': spoolById(seg.job.spool_id)?.color ?? seg.job.color ?? 'var(--color-paper-3)' }"
                  role="button"
                  tabindex="0"
                  :aria-label="`${seg.job.model_name}, ${when(seg.job.start_at)}${seg.job.waits_for_print ? ', attendra la fin de l’impression en cours' : ''}${seg.job.spool_change ? ', changement de bobine' : ''}${seg.job.low_filament ? ', filament insuffisant' : ''}${stoppedLabel(seg.job) ? `, ${stoppedLabel(seg.job)}` : ''}`"
                  @pointerdown="onBlockDown($event, seg)"
                  @click="onBlockClick(seg)"
                  @keydown="onBlockKey($event, seg.job)"
                >
                  <span v-if="seg.startsHere && (seg.job.spool_change || seg.job.low_filament || seg.job.waits_for_print)" class="block__flags">
                    <Clock v-if="seg.job.waits_for_print" aria-hidden="true" />
                    <ArrowLeftRight v-if="seg.job.spool_change" aria-hidden="true" />
                    <AlertTriangle v-if="seg.job.low_filament" aria-hidden="true" />
                    <span class="visually-hidden">
                      {{ seg.job.spool_change ? "Changement de bobine." : "" }} {{ seg.job.low_filament ? "Filament insuffisant." : "" }}
                    </span>
                  </span>
                  <span class="block__time mono">{{ time(seg.job.start_at) }}</span>
                  <span class="block__name">{{ seg.job.model_name }}</span>
                  <span v-if="seg.startsHere && seg.job.spool_change && seg.height > 44" class="block__swap">
                    <span class="swatch" :style="{ background: spoolById(seg.job.spool_id)?.color ?? 'var(--color-paper-3)' }" />
                    Mettre {{ spoolById(seg.job.spool_id)?.label ?? "la bobine prévue" }}
                  </span>
                  <span v-if="seg.height > 64" class="block__meta">
                    {{ stoppedLabel(seg.job)
                      || ((seg.job.status === "printing" || seg.job.status === "paused") && seg.job.remaining_s != null
                        ? `reste ${duration(seg.job.remaining_s)}`
                        : duration(seg.job.duration_s)) }}
                  </span>
                  <span
                    v-if="seg.job.status === 'printing' && seg.job.progress != null"
                    class="block__progress"
                    :style="{ transform: `scaleX(${seg.job.progress})` }"
                  />
                  <span
                    v-else-if="stoppedLabel(seg.job)"
                    class="block__progress block__progress--stopped"
                    :style="{ transform: `scaleX(${seg.job.progress_end})` }"
                  />
                </div>
                <template v-for="ext in [externalFor(d, p.id)]" :key="`ext-${p.id}`">
                  <div
                    v-if="ext"
                    class="block block--live"
                    :style="{ top: `${ext.top}px`, height: `${ext.height}px`, '--filament': 'var(--color-accent)' }"
                    role="button"
                    tabindex="0"
                    :aria-label="`${ext.lp.name}, en cours${ext.lp.end ? `, fin vers ${time(ext.lp.end)}` : ''}`"
                    @click="openLive(ext.lp)"
                    @keydown.enter="openLive(ext.lp)"
                  >
                    <span class="block__time mono">{{ ext.startsHere ? time(ext.lp.start) : "" }}</span>
                    <span class="block__name">{{ ext.lp.name }}</span>
                    <span v-if="ext.height > 64" class="block__meta">
                      hors Printy<template v-if="ext.lp.remaining != null">, reste {{ duration(ext.lp.remaining) }}</template>
                    </span>
                    <span class="block__progress" :style="{ transform: `scaleX(${ext.lp.progress})` }" />
                  </div>
                </template>
                <div v-if="ghostStyle(d, p.id)" class="ghost" :style="ghostStyle(d, p.id)!" aria-hidden="true">
                  <span class="mono">{{ String(Math.floor(drag!.target!.minutes / 60)).padStart(2, "0") }}:{{ String(drag!.target!.minutes % 60).padStart(2, "0") }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <p class="hint muted small">Clique sur un créneau libre pour planifier. Fais glisser une impression pour la déplacer.</p>
      <p class="hint hint--touch muted small">Touche un créneau libre pour planifier. Garde le doigt appuyé sur une impression pour la déplacer.</p>
    </template>

    <ScheduleSheet :open="scheduleOpen" :preset="preset" @close="scheduleOpen = false" @created="onCreated" />
    <JobSheet :open="jobId !== null" :job-id="jobId" @close="closeJob" @changed="refresh" />
  </div>
</template>

<style scoped>
.weeknav { display: flex; align-items: center; }

/* attention band */
.attention { display: grid; gap: var(--space-xs); margin-bottom: var(--space-lg); }
.attention__item {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-md);
  width: 100%;
  padding: var(--space-xs) var(--space-md) var(--space-xs) var(--space-xs);
  border: 0;
  background: var(--color-accent);
  color: var(--color-accent-ink);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.attention__item:hover { background: var(--color-focus); }
.attention__item:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
.attention__thumb { width: 56px; }
.attention__text { display: grid; gap: var(--space-3xs); min-width: 0; }
.attention__cta { background: var(--color-accent-ink); color: var(--color-ink); }

.running { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(360px, 100%), 1fr)); gap: var(--space-sm); margin-bottom: var(--space-lg); }
.running__item {
  display: grid;
  gap: var(--space-2xs);
  padding: var(--space-sm) var(--space-md);
  border: var(--rule-hair) solid var(--color-ink);
  background: var(--color-plate);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.running__item:hover { background: var(--color-paper-2); }
.running__item:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.running__head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); }
.running__name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.running__bar { margin-block: var(--space-2xs); }
.running__figures { display: flex; flex-wrap: wrap; gap: var(--space-2xs) var(--space-md); color: var(--color-ink-2); }

.daytabs { flex-wrap: nowrap; }
.daytabs button { flex: 1; padding-inline: var(--space-2xs); min-width: 0; border-top: 0; }
.daytabs button:first-child { border-left: 0; }
.daytabs button:last-child { border-right: 0; }

/* the week board: the page scrolls through the hours (no scroll area of its own),
   the day tabs and day names stay under the top bar */
.week { --gutter-w: 56px; overflow: clip; }
.week__top { position: sticky; top: calc(var(--topbar-h) + var(--rule-bar)); z-index: var(--z-sticky); background: var(--color-plate); }
.week__head, .week__body { display: grid; grid-template-columns: var(--gutter-w) repeat(var(--days), minmax(0, 1fr)); }
.week__head { border-bottom: var(--rule-bar) solid var(--color-ink); background: var(--color-plate); }
.week__corner { display: flex; align-items: end; padding: var(--space-xs); }
.legend { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-xs); color: var(--color-muted); }
.legend__box { width: 10px; height: 10px; background: var(--color-paper-2); border: 1px solid var(--color-rule); }
.dayhead { display: grid; gap: var(--space-3xs); padding: var(--space-xs) var(--space-xs); border-left: var(--rule-hair) solid var(--color-rule); min-width: 0; }
.dayhead__name { font-weight: 500; text-transform: capitalize; }
.dayhead.is-today .dayhead__name { color: var(--color-accent); }
.dayhead__lanes { display: flex; gap: var(--space-3xs); font-size: var(--text-xs); color: var(--color-muted); }
.dayhead__lanes span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.week__body { position: relative; }
.hours { position: relative; }
.hours span { position: absolute; right: var(--space-xs); transform: translateY(-50%); font-family: var(--font-outlier); font-size: var(--text-xs); color: var(--color-muted); }

.day {
  position: relative;
  display: flex;
  border-left: var(--rule-hair) solid var(--color-rule);
  background-image: linear-gradient(to bottom, var(--color-rule) 1px, transparent 1px);
  background-size: 100% 44px;
}
.day.is-today { background-color: color-mix(in oklab, var(--color-accent-soft) 35%, transparent); }
.offpeak { position: absolute; left: 0; right: 0; background: color-mix(in oklab, var(--color-paper-3) 45%, transparent); pointer-events: none; }
.now { position: absolute; left: 0; right: 0; height: 2px; background: var(--color-accent); z-index: var(--z-raised); pointer-events: none; }
.now__label { position: absolute; right: 2px; top: -18px; font-size: var(--text-xs); color: var(--color-accent); background: var(--color-plate); padding: 0 2px; }

.lane { position: relative; flex: 1; min-width: 0; cursor: cell; }
.lane + .lane { border-left: 1px dashed var(--color-rule); }
.lane:hover { background: color-mix(in oklab, var(--color-paper-2) 40%, transparent); }

.block {
  position: absolute;
  left: 3px;
  right: 3px;
  z-index: var(--z-base);
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: var(--space-2xs) var(--space-xs) var(--space-2xs) var(--space-sm);
  overflow: hidden;
  background: var(--color-plate);
  border: var(--rule-hair) solid var(--color-ink);
  box-shadow: inset 4px 0 0 var(--filament);
  font-size: var(--text-xs);
  line-height: 1.25;
  cursor: pointer;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  touch-action: pan-y;
  transition: box-shadow var(--dur-micro) var(--ease-out), opacity var(--dur-micro) var(--ease-out);
}
.block:hover { box-shadow: inset 4px 0 0 var(--filament), 3px 3px 0 var(--color-ink); }
.block:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 1px; z-index: var(--z-raised); }
.block--movable { cursor: grab; }
.block--active { background: var(--color-accent-soft); }
.block--attention { background: var(--color-accent); color: var(--color-accent-ink); border-color: var(--color-accent); }
.block--done { background: var(--color-paper-2); color: var(--color-muted); border-color: var(--color-rule); }
.block--failed { background: var(--color-error-soft); color: var(--color-error); border-color: var(--color-error); }
.block--stopped { background: var(--color-paper-2); color: var(--color-muted); border-color: var(--color-error); border-style: dashed; }
.block.is-ghosted { opacity: 0.35; }
.block.is-held { box-shadow: inset 4px 0 0 var(--filament), 3px 3px 0 var(--color-ink); transform: translate(-1px, -1px); }
.block--live { background: var(--color-accent-soft); border-style: dashed; cursor: pointer; }
.block__time { font-size: var(--text-xs); }
.block__flags { position: absolute; top: var(--space-2xs); right: var(--space-2xs); display: flex; gap: var(--space-3xs); color: var(--color-accent-strong); }
.block__flags svg { width: 14px; height: 14px; }
.block--attention .block__flags { color: inherit; }
.block__name { font-weight: 500; font-size: var(--text-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.block__meta { color: inherit; opacity: 0.8; }
.block--swap { border-top: 3px solid var(--color-accent-strong); }
.block__swap { display: flex; align-items: center; gap: var(--space-3xs); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.block__swap .swatch { flex: none; }
.block__progress { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--color-accent); transform-origin: left; transition: transform var(--dur-long) var(--ease-out); }
.block__progress--stopped { background: var(--color-error); }

.ghost {
  position: absolute;
  left: 3px;
  right: 3px;
  z-index: var(--z-raised);
  padding: var(--space-2xs) var(--space-xs);
  border: var(--rule-bar) dashed var(--color-ink);
  background: color-mix(in oklab, var(--color-plate) 80%, transparent);
  font-size: var(--text-xs);
  pointer-events: none;
}
.is-dragging, .is-dragging * { cursor: grabbing !important; }

.hint { margin-top: var(--space-md); }
.hint--touch { display: none; }
@media (hover: none) { .hint { display: none; } .hint--touch { display: block; } }
@media (max-width: 60rem) { .week { --gutter-w: 48px; } }
</style>
