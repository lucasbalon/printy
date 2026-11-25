<script setup lang="ts">
import { AlertTriangle, Camera } from "lucide-vue-next";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, urls } from "../api";
import { duration, fromInputs, grams, kwh, time, toInputs, when } from "../format";
import { attempt, loadSpools, spoolById, state, toast } from "../store";
import type { Job, Spool, SpoolList } from "../types";
import SpoolPicker from "./SpoolPicker.vue";
import AppSheet from "./AppSheet.vue";
import ModelThumb from "./ModelThumb.vue";
import StatusTag from "./StatusTag.vue";

const props = defineProps<{ open: boolean; jobId: number | null }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const job = ref<Job | null>(null);
const busy = ref(false);
const date = ref("");
const hour = ref("");
const photoKey = ref(0);
const photoFailed = ref(false);
let timer: number | undefined;

const MOVABLE = ["scheduled", "needs_attention", "awaiting_confirmation", "powering_on"];
const CLOSED = ["finished", "failed", "cancelled"];
// nothing sent to the printer: such a print is simply deleted
const NOT_STARTED = ["scheduled", "powering_on", "awaiting_confirmation", "ready", "needs_attention"];

async function load() {
  if (!props.jobId) return;
  const fresh = await api.get<Job>(`/jobs/${props.jobId}`);
  const moved = !job.value || job.value.start_at !== fresh.start_at;
  job.value = fresh;
  if (moved) {
    const i = toInputs(new Date(fresh.start_at));
    date.value = i.date;
    hour.value = i.time;
  }
}

watch(
  () => [props.open, props.jobId],
  async ([open]) => {
    window.clearInterval(timer);
    if (!open) return;
    job.value = null;
    photoFailed.value = false;
    await Promise.all([attempt(() => loadSpools()), attempt(load)]);
    loadRanked();
    timer = window.setInterval(() => load().catch(() => undefined), 4000);
  },
  { immediate: true },
);
onBeforeUnmount(() => window.clearInterval(timer));

// spools ranked for this job's colour
const ranked = ref<Spool[]>([]);
const spoolId = ref<number | null>(null);
async function loadRanked() {
  if (!job.value || !state.spools.enabled) return;
  spoolId.value = job.value.spool_id;
  const params = new URLSearchParams({ printer_id: String(job.value.printer_id) });
  if (job.value.filament_g) params.set("need_g", String(job.value.filament_g));
  if (job.value.color) params.set("color", job.value.color);
  if (job.value.material) params.set("material", job.value.material);
  const res = await attempt(() => api.get<SpoolList>(`/spools?${params}`));
  ranked.value = res?.spools ?? [];
}
const changeSpool = (id: number | null) =>
  act(() => api.patch<Job>(`/jobs/${job.value!.id}`, id === null ? { clear_spool: true } : { spool_id: id }), "Bobine mise à jour.");
const spoolSwapped = () => act(() => api.post<Job>(`/jobs/${job.value!.id}/spool-swapped`), "Bobine chargée mise à jour.");
const jobSpool = computed(() => spoolById(job.value?.spool_id));
const previousSpool = computed(() => spoolById(job.value?.previous_spool_id));

const printer = computed(() => state.printers.find((p) => p.id === job.value?.printer_id) ?? null);
const needsUser = computed(() => job.value?.status === "awaiting_confirmation" || job.value?.status === "needs_attention");
const movable = computed(() => !!job.value && MOVABLE.includes(job.value.status));
const closed = computed(() => !!job.value && CLOSED.includes(job.value.status));
const running = computed(() => job.value?.status === "printing" || job.value?.status === "paused");
// what the printer itself reports: a job stuck in Printy can always be cancelled when nothing runs
const printerActive = computed(() => ["printing", "paused"].includes(printer.value?.live?.state ?? ""));
const notStarted = computed(() => !!job.value && NOT_STARTED.includes(job.value.status));
// sent to the printer: cancelled (kept in the history); a print the printer really runs
// is stopped with "Arrêter l'impression" instead
const cancellable = computed(
  () => !!job.value && (job.value.status === "starting" || running.value) && !printerActive.value,
);
const photoUrl = computed(() => {
  void photoKey.value;
  if (!job.value) return "";
  return job.value.status === "awaiting_confirmation" ? urls.jobSnapshot(job.value.id) : printer.value ? urls.printerSnapshot(printer.value.id) : "";
});
const dateChanged = computed(() => {
  if (!job.value || !date.value || !hour.value) return false;
  return fromInputs(date.value, hour.value).getTime() !== new Date(job.value.start_at).getTime();
});

async function act(fn: () => Promise<Job | void>, success?: string) {
  busy.value = true;
  const res = await attempt(fn, success);
  busy.value = false;
  if (res) job.value = res;
  if (res !== undefined) emit("changed");
}

const startNow = () => act(() => api.post<Job>(`/jobs/${job.value!.id}/start-now`), "Plateau confirmé, lancement en cours.");
const confirmBed = () => act(() => api.post<Job>(`/jobs/${job.value!.id}/confirm`), "Plateau confirmé.");
const snoozeMin = computed(() => state.settings?.snooze_min ?? 60);
const snoozeLabel = computed(() => {
  const h = Math.floor(snoozeMin.value / 60);
  const m = snoozeMin.value % 60;
  return h ? (m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`) : `${m} min`;
});
const snooze = () => act(() => api.post<Job>(`/jobs/${job.value!.id}/snooze`), `Reportée de ${snoozeLabel.value}.`);
const move = () =>
  act(
    () => api.patch<Job>(`/jobs/${job.value!.id}`, { start_at: fromInputs(date.value, hour.value).toISOString() }),
    "Impression déplacée.",
  );
const toggleConfirm = (v: boolean) => act(() => api.patch<Job>(`/jobs/${job.value!.id}`, { confirm_required: v }));

async function cancelJob() {
  if (!window.confirm(`Annuler « ${job.value!.model_name} » ?`)) return;
  await act(() => api.post<Job>(`/jobs/${job.value!.id}/cancel`), "Impression annulée.");
}

async function remove() {
  const name = job.value!.model_name;
  if (notStarted.value && !window.confirm(`Supprimer « ${name} » du planning ?`)) return;
  busy.value = true;
  const ok = await attempt(() => api.del(`/jobs/${job.value!.id}`));
  busy.value = false;
  if (ok !== undefined) {
    toast(notStarted.value ? "Impression supprimée." : "Retirée de l’historique.");
    emit("changed");
    emit("close");
  }
}

async function printerAction(action: "pause" | "resume" | "cancel") {
  if (action === "cancel" && !window.confirm("Arrêter l’impression en cours ? Elle ne pourra pas être reprise.")) return;
  busy.value = true;
  await attempt(() => api.post(`/printers/${job.value!.printer_id}/action`, { action }));
  busy.value = false;
  await attempt(load);
  emit("changed");
}

const eventLabel = (iso: string) => when(iso);

async function markDefect() {
  const n = job.value?.stock_added ?? 0;
  const msg = n
    ? `Marquer cette impression comme ratée ? ${n} pièce${n > 1 ? "s" : ""} ser${n > 1 ? "ont" : "a"} retirée${n > 1 ? "s" : ""} du stock.`
    : "Marquer cette impression comme ratée ?";
  if (!window.confirm(msg)) return;
  await act(() => api.post<Job>(`/jobs/${job.value!.id}/defect`), "Impression marquée comme ratée.");
}

</script>

<template>
  <AppSheet :open="open" :title="job?.model_name ?? 'Impression'" @close="emit('close')">
    <div v-if="!job" class="loading">
      <div class="skeleton" style="height: 96px" />
      <div class="skeleton" style="height: 220px" />
    </div>

    <div v-else class="job">
      <header class="job__head">
        <ModelThumb :sliced-id="job.has_thumbnail ? job.sliced_file_id : null" size="sm" class="job__thumb" />
        <div class="job__meta">
          <StatusTag :job="job.status" />
          <p>
            {{ printer?.name ?? "Imprimante" }}, {{ when(job.start_at) }}
          </p>
          <p class="muted small">
            <span class="mono">{{ duration(job.duration_s) }}</span>
            <template v-if="job.filament_g">, <span class="mono">{{ grams(job.filament_g) }}</span></template>
            <template v-if="job.material">, {{ job.material }}</template>
            <span v-if="job.color" class="swatch" :style="{ background: job.color, marginLeft: '6px' }" />
          </p>
        </div>
      </header>

      <p v-if="job.waits_for_print" class="notice">
        <AlertTriangle aria-hidden="true" />
        <span>L’imprimante sera encore occupée à cette heure : l’impression démarrera dès qu’elle aura fini.</span>
      </p>
      <p v-if="job.message" class="notice">
        <AlertTriangle aria-hidden="true" />
        <span>{{ job.message }}</span>
      </p>

      <!-- Spool -->
      <section v-if="state.spools.enabled && (job.spool_id || !closed)" class="block">
        <h3>Bobine</h3>
        <template v-if="closed">
          <p class="spoolline">
            <span class="swatch" :style="{ background: jobSpool?.color ?? 'transparent' }" />
            {{ jobSpool?.label ?? `Bobine n° ${job.spool_id}` }}
            <span v-if="job.filament_used_g" class="muted">, <span class="mono">{{ grams(job.filament_used_g) }}</span> consommés</span>
          </p>
        </template>
        <template v-else>
          <SpoolPicker
            v-model="spoolId"
            :spools="ranked"
            label="Bobine"
            none-label="Ne pas suivre la bobine"
            :disabled="busy || running"
            @change="changeSpool"
          />
          <p v-if="job.spool_change" class="notice">
            <AlertTriangle aria-hidden="true" />
            <span>Changement de bobine à prévoir : {{ previousSpool?.label ?? "une autre bobine" }} sera encore chargée.</span>
          </p>
          <div v-if="job.spool_change && job.spool_id" class="row">
            <button type="button" class="btn btn--secondary btn--sm" :disabled="busy" @click="spoolSwapped">
              J’ai déjà mis {{ jobSpool?.label ?? "cette bobine" }}
            </button>
          </div>
          <p v-if="job.low_filament" class="notice notice--error">
            <AlertTriangle aria-hidden="true" />
            <span>
              Filament insuffisant : il restera {{ grams(job.spool_remaining_g) }} sur la bobine, l’impression en demande
              encore {{ grams(job.filament_needed_g ?? job.filament_g) }}.
            </span>
          </p>
        </template>
      </section>

      <!-- Bed check -->
      <section v-if="needsUser" class="block">
        <h3>Le plateau est-il vide ?</h3>
        <div class="photo">
          <img
            v-if="photoUrl && !photoFailed"
            :key="photoKey"
            :src="photoUrl"
            alt="Photo du plateau"
            width="640"
            height="480"
            @error="photoFailed = true"
          />
          <div v-else class="photo__empty">
            <Camera aria-hidden="true" />
            <span>Pas de photo disponible. Vérifie le plateau sur place.</span>
          </div>
        </div>
        <div class="row">
          <button type="button" class="btn btn--primary" :disabled="busy" @click="job.status === 'awaiting_confirmation' && new Date(job.start_at) > new Date() ? confirmBed() : startNow()">
            {{ job.status === "awaiting_confirmation" && new Date(job.start_at) > new Date() ? `Plateau vide, lancer à ${time(job.start_at)}` : "Plateau vide, lancer maintenant" }}
          </button>
          <button type="button" class="btn btn--secondary" :disabled="busy" @click="snooze">Reporter de {{ snoozeLabel }}</button>
          <button type="button" class="btn btn--ghost" :disabled="busy" @click="photoFailed = false; photoKey++">Nouvelle photo</button>
        </div>
      </section>

      <!-- Printing -->
      <section v-if="running" class="block">
        <h3>Progression</h3>
        <div class="progress" role="progressbar" :aria-valuenow="Math.round((job.progress ?? 0) * 100)" aria-valuemin="0" aria-valuemax="100">
          <div class="progress__bar" :style="{ transform: `scaleX(${job.progress ?? 0})` }" />
        </div>
        <p class="small">
          <span class="mono">{{ Math.round((job.progress ?? 0) * 100) }} %</span>
          <template v-if="job.remaining_s != null">, encore <span class="mono">{{ duration(job.remaining_s) }}</span></template>
        </p>
        <div class="row">
          <button v-if="job.status === 'printing'" type="button" class="btn btn--secondary" :disabled="busy" @click="printerAction('pause')">Pause</button>
          <button v-else type="button" class="btn btn--secondary" :disabled="busy" @click="printerAction('resume')">Reprendre</button>
          <button type="button" class="btn btn--danger" :disabled="busy" @click="printerAction('cancel')">Arrêter l’impression</button>
        </div>
      </section>

      <!-- Reschedule -->
      <section v-if="movable" class="block">
        <h3>Horaire</h3>
        <div class="form-row">
          <div class="field">
            <label for="job-date">Jour</label>
            <input id="job-date" v-model="date" class="input" type="date" />
          </div>
          <div class="field">
            <label for="job-time">Début</label>
            <input id="job-time" v-model="hour" class="input" type="time" step="60" />
          </div>
        </div>
        <div class="row">
          <button type="button" class="btn btn--secondary btn--sm" :disabled="!dateChanged || busy" @click="move">Déplacer</button>
          <button v-if="job.status === 'needs_attention'" type="button" class="btn btn--ghost btn--sm" :disabled="busy" @click="startNow">
            Relancer maintenant
          </button>
        </div>
        <label class="switch">
          <input type="checkbox" :checked="job.confirm_required" :disabled="busy" @change="toggleConfirm(($event.target as HTMLInputElement).checked)" />
          <span class="switch__track" aria-hidden="true" />
          <span class="switch__text">
            <span>Confirmer que le plateau est vide</span>
            <small v-if="job.confirm_required">Une notification avec photo sera envoyée avant le lancement.</small>
            <small v-else>Attention : lancement sans vérification du plateau.</small>
          </span>
        </label>
      </section>

      <!-- Result -->
      <section v-if="closed && (job.filament_used_g != null || job.energy_wh != null || job.stock_added)" class="block">
        <h3>Bilan</h3>
        <dl class="result">
          <div v-if="job.filament_used_g != null">
            <dt>Filament</dt>
            <dd class="mono">{{ grams(job.filament_used_g) }}</dd>
          </div>
          <div v-if="job.energy_wh != null">
            <dt>Électricité</dt>
            <dd class="mono">{{ kwh(job.energy_wh / 1000) }}</dd>
          </div>
          <div v-if="job.stock_added">
            <dt>Stock</dt>
            <dd>+{{ job.stock_added }} pièce{{ job.stock_added > 1 ? "s" : "" }}</dd>
          </div>
        </dl>
        <div v-if="job.status === 'finished'" class="row">
          <button type="button" class="btn btn--ghost btn--sm" :disabled="busy" @click="markDefect">Impression ratée</button>
        </div>
      </section>

      <!-- History -->
      <section v-if="job.events?.length" class="block">
        <h3>Historique</h3>
        <ol class="timeline">
          <li v-for="(e, i) in [...job.events].reverse()" :key="i">
            <time class="mono small muted" :datetime="e.at">{{ eventLabel(e.at) }}</time>
            <span>{{ e.message || e.kind }}</span>
          </li>
        </ol>
      </section>
    </div>

    <template v-if="job" #foot>
      <button v-if="notStarted" type="button" class="btn btn--danger" :disabled="busy" @click="remove">Supprimer</button>
      <button v-else-if="closed" type="button" class="btn btn--ghost" :disabled="busy" @click="remove">Retirer de l’historique</button>
      <button v-else-if="cancellable" type="button" class="btn btn--danger" :disabled="busy" @click="cancelJob">
        {{ job.status === "starting" ? "Annuler le lancement" : "Annuler l’impression" }}
      </button>
      <button type="button" class="btn btn--secondary" @click="emit('close')">Fermer</button>
    </template>
  </AppSheet>
</template>

<style scoped>
.loading { display: grid; gap: var(--space-md); }
.job { display: grid; gap: var(--space-lg); }
.job__head { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: var(--space-md); align-items: center; }
.job__thumb { width: 88px; border: var(--rule-hair) solid var(--color-rule); }
.job__meta { display: grid; gap: var(--space-2xs); justify-items: start; }
.block { display: grid; gap: var(--space-sm); padding-top: var(--space-md); border-top: var(--rule-bar) solid var(--color-ink); }
.row { display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.photo { background: var(--color-media); aspect-ratio: 4 / 3; display: grid; place-items: center; overflow: hidden; }
.photo img { width: 100%; height: 100%; object-fit: cover; }
.photo__empty { display: grid; justify-items: center; gap: var(--space-xs); padding: var(--space-lg); color: var(--color-media-ink); text-align: center; font-size: var(--text-sm); }
.photo__empty svg { width: 28px; height: 28px; }
.result { display: flex; flex-wrap: wrap; gap: var(--space-sm) var(--space-xl); margin: 0; }
.result div { display: grid; gap: var(--space-3xs); }
.result dt { font-size: var(--text-sm); color: var(--color-muted); }
.result dd { margin: 0; }
.spoolline { display: flex; align-items: center; gap: var(--space-xs); }
.notice--error { background: var(--color-error-soft); color: var(--color-error); }
.timeline { display: grid; gap: var(--space-xs); margin: 0; padding: 0; list-style: none; }
.timeline li { display: grid; grid-template-columns: 11rem minmax(0, 1fr); gap: var(--space-sm); font-size: var(--text-sm); padding-bottom: var(--space-xs); border-bottom: var(--rule-hair) solid var(--color-rule); }
@media (max-width: 40rem) { .timeline li { grid-template-columns: minmax(0, 1fr); gap: var(--space-3xs); } }
</style>
