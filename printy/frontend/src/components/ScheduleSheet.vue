<script setup lang="ts">
import { AlertTriangle, Search } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { api } from "../api";
import {
  addDays,
  duration,
  fromInputs,
  grams,
  nextOffPeak,
  offPeakShare,
  startOfDay,
  toInputs,
  when,
} from "../format";
import { attempt, loadPrinters, loadSettings, loadSpools, spoolById, state } from "../store";
import type { Job, ModelFull, ModelSummary, Spool, SpoolList } from "../types";
import AppSheet from "./AppSheet.vue";
import ModelThumb from "./ModelThumb.vue";
import SpoolPicker from "./SpoolPicker.vue";

export interface SchedulePreset {
  start?: Date;
  printerId?: number;
  modelId?: number;
  slicedId?: number;
}

const props = defineProps<{ open: boolean; preset: SchedulePreset }>();
const emit = defineEmits<{ close: []; created: [job: Job] }>();

const models = ref<ModelSummary[] | null>(null);
const query = ref("");
const model = ref<ModelFull | null>(null);
const slicedId = ref<number | null>(null);
const printerId = ref<number | null>(null);
const date = ref("");
const hour = ref("");
const confirm = ref(true);
const saving = ref(false);
const error = ref("");
const spoolId = ref<number | null>(null);
const spoolTouched = ref(false);
const ranked = ref<Spool[]>([]);
// "now": the print leaves as soon as the printer is ready, no notification
const now = ref(true);

function inAnHour(): Date {
  const d = new Date(Date.now() + 60 * 60_000);
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  return d;
}

function setStart(d: Date) {
  const i = toInputs(d);
  date.value = i.date;
  hour.value = i.time;
}

async function pickModel(id: number, preferSliced?: number) {
  model.value = await api.get<ModelFull>(`/models/${id}`);
  const versions = model.value.sliced;
  slicedId.value = preferSliced ?? (versions.length === 1 ? versions[0].id : (versions[0]?.id ?? null));
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    error.value = "";
    query.value = "";
    model.value = null;
    slicedId.value = null;
    spoolId.value = null;
    spoolTouched.value = false;
    attempt(() => loadSpools());
    if (!state.settings) await attempt(loadSettings);
    if (!state.printersLoaded) await attempt(loadPrinters);
    confirm.value = state.settings?.confirm_by_default ?? true;
    printerId.value = props.preset.printerId ?? state.printers[0]?.id ?? null;
    const later = !!props.preset.start && props.preset.start.getTime() > Date.now() + 5 * 60_000;
    now.value = !later;
    setStart(later ? props.preset.start! : inAnHour());
    if (props.preset.modelId) await attempt(() => pickModel(props.preset.modelId!, props.preset.slicedId));
    models.value = (await attempt(() => api.get<ModelSummary[]>("/models"))) ?? [];
  },
  { immediate: true },
);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  const list = models.value ?? [];
  return (q ? list.filter((m) => m.name.toLowerCase().includes(q) || m.tags.some((t) => t.includes(q))) : list).filter(
    (m) => m.sliced_count > 0,
  );
});

const version = computed(() => model.value?.sliced.find((s) => s.id === slicedId.value) ?? null);
const start = computed(() => (now.value ? new Date() : date.value && hour.value ? fromInputs(date.value, hour.value) : null));
const end = computed(() =>
  start.value ? new Date(start.value.getTime() + (version.value?.duration_s ?? 3600) * 1000) : null,
);
const offPeak = computed(() => state.settings?.off_peak ?? []);
const share = computed(() => (start.value && end.value ? offPeakShare(start.value, end.value, offPeak.value) : 0));
const inPast = computed(() => !now.value && !!start.value && start.value.getTime() < Date.now() - 60_000);
const printer = computed(() => state.printers.find((p) => p.id === printerId.value) ?? null);
const printerBusy = computed(() => ["printing", "paused"].includes(printer.value?.live?.state ?? ""));

const quickStarts = computed(() => {
  const out: { label: string; at: Date }[] = [];
  const hc = nextOffPeak(new Date(), offPeak.value);
  if (hc) out.push({ label: `Heures creuses, ${when(hc)}`, at: hc });
  const tomorrow = addDays(startOfDay(new Date()), 1);
  tomorrow.setHours(8, 0);
  out.push({ label: "Demain 8 h", at: tomorrow });
  return out;
});

// Spools ranked for the chosen file; the best match is preselected
watch(
  () => [slicedId.value, printerId.value, props.open] as const,
  async ([sid, pid, open]) => {
    if (!open) return;
    await loadSpools().catch(() => undefined);
    if (!state.spools.enabled) return;
    const v = model.value?.sliced.find((s) => s.id === sid);
    const params = new URLSearchParams();
    if (v?.color) params.set("color", v.color);
    if (v?.material) params.set("material", v.material);
    if (pid) params.set("printer_id", String(pid));
    if (v?.filament_g) params.set("need_g", String(v.filament_g));
    const res = await attempt(() => api.get<SpoolList>(`/spools?${params}`));
    ranked.value = res?.spools ?? [];
    if (!spoolTouched.value) spoolId.value = res?.suggested_id ?? null;
  },
);
const chosenSpool = computed(() => ranked.value.find((s) => s.id === spoolId.value) ?? null);
const loadedSpool = computed(() => spoolById(state.printers.find((p) => p.id === printerId.value)?.loaded_spool_id));
const needsSwap = computed(() => !!chosenSpool.value && !!loadedSpool.value && chosenSpool.value.id !== loadedSpool.value.id);
const tooLittle = computed(
  () =>
    !!chosenSpool.value &&
    chosenSpool.value.remaining_g != null &&
    !!version.value?.filament_g &&
    chosenSpool.value.remaining_g < version.value.filament_g * 1.05,
);

const canSave = computed(() => !!slicedId.value && !!printerId.value && !!start.value && !saving.value);

function pickLater(at?: Date) {
  now.value = false;
  if (at) setStart(at);
}

async function save() {
  if (!canSave.value || !start.value) return;
  saving.value = true;
  error.value = "";
  try {
    const job = await api.post<Job>("/jobs", {
      sliced_file_id: slicedId.value,
      printer_id: printerId.value,
      start_at: (now.value ? new Date() : start.value).toISOString(),
      start_now: now.value,
      confirm_required: confirm.value,
      spool_id: spoolId.value,
      auto_spool: !state.spools.enabled,
    });
    emit("created", job);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}

function versionLabel(v: ModelFull["sliced"][number]): string {
  const parts = [v.printer_model, v.material, v.layer_height ? `${v.layer_height} mm` : null].filter(Boolean);
  return parts.length ? parts.join(", ") : v.filename;
}
</script>

<template>
  <AppSheet :open="open" :title="now ? 'Imprimer' : 'Planifier une impression'" @close="emit('close')">
    <form id="schedule-form" class="form-grid" @submit.prevent="save">
      <!-- What -->
      <fieldset class="step">
        <legend>Quoi</legend>
        <div v-if="model" class="chosen">
          <ModelThumb :sliced-id="version?.id ?? null" size="sm" class="chosen__thumb" />
          <div class="chosen__text">
            <strong>{{ model.name }}</strong>
            <span class="muted small">
              <span class="mono">{{ duration(version?.duration_s) }}</span>
              <template v-if="version?.filament_g">, <span class="mono">{{ grams(version.filament_g) }}</span></template>
            </span>
          </div>
          <button type="button" class="btn btn--ghost btn--sm" @click="model = null; slicedId = null">Changer</button>
        </div>

        <template v-else>
          <label class="search">
            <Search aria-hidden="true" />
            <span class="visually-hidden">Rechercher un modèle</span>
            <input v-model="query" class="input" type="search" placeholder="Rechercher un modèle" autocomplete="off" autofocus />
          </label>
          <div v-if="models === null" class="picker">
            <div v-for="i in 4" :key="i" class="pick skeleton" style="height: 56px" />
          </div>
          <p v-else-if="!filtered.length" class="muted small picker-empty">
            {{ query ? "Aucun modèle ne correspond." : "La bibliothèque n’a encore aucun fichier tranché." }}
          </p>
          <ul v-else class="picker" role="list">
            <li v-for="m in filtered" :key="m.id">
              <button type="button" class="pick" @click="attempt(() => pickModel(m.id))">
                <ModelThumb :sliced-id="m.cover_id" size="sm" class="pick__thumb" />
                <span class="pick__name">{{ m.name }}</span>
                <span class="mono small muted">{{ duration(m.duration_s) }}</span>
              </button>
            </li>
          </ul>
        </template>

        <div v-if="model && model.sliced.length > 1" class="field">
          <span class="field__label">Version</span>
          <div class="versions">
            <button
              v-for="v in model.sliced"
              :key="v.id"
              type="button"
              class="version"
              :aria-pressed="v.id === slicedId"
              @click="slicedId = v.id"
            >
              <span v-if="v.color" class="swatch" :style="{ background: v.color }" />
              <span class="version__label">{{ versionLabel(v) }}</span>
              <span class="mono small">{{ duration(v.duration_s) }}</span>
            </button>
          </div>
        </div>
      </fieldset>

      <!-- Where -->
      <fieldset class="step">
        <legend>Sur quelle imprimante</legend>
        <p v-if="!state.printers.length" class="muted small">
          Aucune imprimante. <RouterLink to="/imprimantes">Ajoute ta première imprimante</RouterLink>.
        </p>
        <div v-else class="segmented" role="group" aria-label="Imprimante">
          <button
            v-for="p in state.printers"
            :key="p.id"
            type="button"
            :aria-pressed="p.id === printerId"
            @click="printerId = p.id"
          >
            {{ p.name }}
          </button>
        </div>
      </fieldset>

      <!-- Spool -->
      <fieldset v-if="state.spools.enabled && model" class="step">
        <legend>Bobine</legend>
        <SpoolPicker
          v-model="spoolId"
          :spools="ranked"
          label="Bobine"
          none-label="Ne pas suivre la bobine"
          @change="spoolTouched = true"
        />
        <p v-if="!chosenSpool && version?.color" class="muted small">
          Aucune bobine de cette couleur dans Spoolman. Choisis-en une pour suivre la consommation.
        </p>
        <p v-if="needsSwap" class="notice">
          <AlertTriangle aria-hidden="true" />
          <span>
            {{ state.printers.find((p) => p.id === printerId)?.name }} a {{ loadedSpool?.label }} chargée : Printy te demandera de
            changer de bobine avant le lancement.
          </span>
        </p>
        <p v-if="tooLittle" class="notice notice--error">
          <AlertTriangle aria-hidden="true" />
          <span>
            Il reste {{ grams(chosenSpool?.remaining_g) }} sur cette bobine, l’impression en demande {{ grams(version?.filament_g) }}.
          </span>
        </p>
      </fieldset>

      <!-- When -->
      <fieldset class="step">
        <legend>Quand</legend>
        <div class="segmented" role="group" aria-label="Quand">
          <button type="button" :aria-pressed="now" @click="now = true">Maintenant</button>
          <button type="button" :aria-pressed="!now" @click="pickLater()">Plus tard</button>
        </div>
        <template v-if="!now">
          <div v-if="quickStarts.length" class="quick">
            <button v-for="q in quickStarts" :key="q.label" type="button" class="tag" @click="pickLater(q.at)">
              {{ q.label }}
            </button>
          </div>
          <div class="form-row">
            <div class="field">
              <label for="sched-date">Jour</label>
              <input id="sched-date" v-model="date" class="input" type="date" required />
            </div>
            <div class="field">
              <label for="sched-time">Heure de début</label>
              <input id="sched-time" v-model="hour" class="input" type="time" step="60" required />
            </div>
          </div>
        </template>
        <p v-else-if="printerBusy" class="notice">
          <AlertTriangle aria-hidden="true" />
          <span>{{ printer?.name }} imprime déjà : choisis « Plus tard » ou une autre imprimante.</span>
        </p>
        <dl v-if="start && end" class="summary">
          <div>
            <dt>Fin prévue</dt>
            <dd>{{ when(end) }}</dd>
          </div>
          <div v-if="offPeak.length">
            <dt>Heures creuses</dt>
            <dd>
              {{ share > 0.99 ? "Entièrement" : share < 0.01 ? "Aucune part" : `${Math.round(share * 100)} %` }}
            </dd>
          </div>
        </dl>
        <p v-if="inPast" class="muted small">L’heure est passée : l’impression partira dès que possible.</p>
      </fieldset>

      <!-- Safety -->
      <fieldset v-if="now" class="step">
        <legend class="visually-hidden">Plateau</legend>
        <p v-if="printer?.piece_on_bed" class="notice notice--error">
          <AlertTriangle aria-hidden="true" />
          <span>La dernière pièce n’a pas été déclarée retirée : vérifie que le plateau de {{ printer.name }} est vide.</span>
        </p>
        <p v-else class="muted small">En lançant maintenant, tu confirmes que le plateau est vide.</p>
      </fieldset>
      <fieldset v-else class="step">
        <legend class="visually-hidden">Confirmation</legend>
        <label class="switch">
          <input v-model="confirm" type="checkbox" />
          <span class="switch__track" aria-hidden="true" />
          <span class="switch__text">
            <span>Confirmer que le plateau est vide</span>
            <small>Une notification avec photo te demande de valider avant le lancement.</small>
          </span>
        </label>
        <p v-if="!confirm" class="notice">
          <AlertTriangle aria-hidden="true" />
          <span>Sans confirmation, l’impression démarrera même si une pièce est restée sur le plateau.</span>
        </p>
      </fieldset>

      <p v-if="error" class="notice notice--error" role="alert">
        <AlertTriangle aria-hidden="true" />
        <span>{{ error }}</span>
      </p>
    </form>

    <template #foot>
      <button type="button" class="btn btn--ghost" @click="emit('close')">Annuler</button>
      <button type="submit" form="schedule-form" class="btn btn--primary" :disabled="!canSave" :aria-busy="saving">
        {{ now ? (saving ? "Lancement…" : "Lancer maintenant") : saving ? "Planification…" : "Planifier" }}
      </button>
    </template>
  </AppSheet>
</template>

<style scoped>
.step { display: grid; gap: var(--space-sm); margin: 0; padding: 0 0 var(--space-lg); border: 0; border-bottom: var(--rule-hair) solid var(--color-rule); min-width: 0; }
.step:last-of-type { border-bottom: 0; }
.step legend { padding: 0; margin-bottom: var(--space-sm); font-family: var(--font-display); font-weight: 700; font-size: var(--text-lg); letter-spacing: var(--tracking-display); }

.search { position: relative; display: block; }
.search svg { position: absolute; left: var(--space-sm); top: 50%; width: 16px; height: 16px; transform: translateY(-50%); color: var(--color-muted); pointer-events: none; }
.search .input { padding-left: 36px; }

.picker { display: grid; gap: 0; max-height: 280px; overflow-y: auto; margin: 0; padding: 0; list-style: none; border-top: var(--rule-hair) solid var(--color-rule); }
.picker-empty { padding: var(--space-sm) 0; }
.pick {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-sm);
  width: 100%;
  min-height: 56px;
  padding: var(--space-2xs) var(--space-xs);
  border: 0;
  border-bottom: var(--rule-hair) solid var(--color-rule);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.pick:hover { background: var(--color-accent-soft); }
.pick:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.pick__thumb { width: 44px; }
.pick__name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.chosen { display: grid; grid-template-columns: 56px minmax(0, 1fr) auto; align-items: center; gap: var(--space-sm); padding: var(--space-xs); background: var(--color-plate); border: var(--rule-hair) solid var(--color-ink); }
.chosen__thumb { width: 56px; }
.chosen__text { display: grid; gap: var(--space-3xs); min-width: 0; }

.versions { display: grid; gap: var(--space-2xs); }
.version {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-sm);
  min-height: var(--control-h);
  padding: 0 var(--space-sm);
  border: var(--rule-hair) solid var(--color-rule);
  background: var(--color-plate);
  color: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.version:hover { background: var(--color-paper-2); }
.version[aria-pressed="true"] { border-color: var(--color-ink); box-shadow: inset 0 0 0 1px var(--color-ink); background: var(--color-accent-soft); }
.version:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 1px; }
.version__label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.quick { display: flex; flex-wrap: wrap; gap: var(--space-2xs); }
.quick .tag { min-height: 32px; }

.summary { display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-xl); margin: 0; padding: var(--space-sm) var(--space-md); background: var(--color-paper-2); }
.summary div { display: grid; gap: var(--space-3xs); }
.summary dt { font-size: var(--text-sm); color: var(--color-muted); }
.summary dd { margin: 0; font-weight: 500; }

.notice--error { background: var(--color-error-soft); color: var(--color-error); }
</style>
