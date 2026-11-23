<script setup lang="ts">
import { AlertTriangle, ChevronRight, Minus, Plus, Search } from "lucide-vue-next";
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api";
import { attempt, loadPrinters, state } from "../store";
import type { Spool } from "../types";
import AppSheet from "./AppSheet.vue";

interface Filament {
  id: number;
  name: string;
  vendor: string | null;
  material: string | null;
  color: string | null;
  weight: number | null;
}

// Preset: "one more like this one" from the spools page
const props = defineProps<{ open: boolean; filamentId?: number | null }>();
const emit = defineEmits<{ close: []; created: [spools: Spool[]] }>();

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU"];

const filaments = ref<Filament[] | null>(null);
const mode = ref<"existing" | "new">("existing");
const query = ref("");
const filamentId = ref<number | null>(null);
const form = reactive({ vendor: "", name: "", material: "PLA", color: "#FFFFFF", weight: 1000, diameter: 1.75 });
const count = ref(1);
const location = ref("");
const loadOn = ref<number | null>(null);
const saving = ref(false);
const error = ref("");

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    error.value = "";
    count.value = 1;
    location.value = "";
    loadOn.value = null;
    query.value = "";
    filamentId.value = props.filamentId ?? null;
    Object.assign(form, { vendor: "", name: "", material: "PLA", color: "#FFFFFF", weight: 1000, diameter: 1.75 });
    if (!state.printersLoaded) attempt(loadPrinters);
    filaments.value = (await attempt(() => api.get<Filament[]>("/spoolman/filaments"))) ?? [];
    mode.value = filaments.value.length ? "existing" : "new";
  },
  { immediate: true },
);

const vendors = computed(() => [...new Set((filaments.value ?? []).map((f) => f.vendor).filter(Boolean))] as string[]);
const shown = computed(() => {
  const q = query.value.trim().toLowerCase();
  const list = filaments.value ?? [];
  return q
    ? list.filter((f) => `${f.vendor ?? ""} ${f.name} ${f.material ?? ""}`.toLowerCase().includes(q))
    : list;
});
const canSave = computed(
  () => !saving.value && (mode.value === "existing" ? !!filamentId.value : form.name.trim().length > 0),
);

async function save() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    const body: Record<string, unknown> = { count: count.value, location: location.value, load_on_printer_id: loadOn.value };
    if (mode.value === "existing") body.filament_id = filamentId.value;
    else body.filament = { ...form, name: form.name.trim(), vendor: form.vendor.trim() };
    const created = await api.post<Spool[]>("/spoolman/spools", body);
    emit("created", created);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <AppSheet :open="open" title="Nouvelle bobine" @close="emit('close')">
    <form id="spool-form" class="form-grid" @submit.prevent="save">
      <div class="segmented" role="group" aria-label="Filament">
        <button type="button" :aria-pressed="mode === 'existing'" :disabled="!filaments?.length" @click="mode = 'existing'">
          Filament connu
        </button>
        <button type="button" :aria-pressed="mode === 'new'" @click="mode = 'new'">Nouveau filament</button>
      </div>

      <!-- Existing filament: a list, newest picks are one click -->
      <template v-if="mode === 'existing'">
        <label class="search">
          <Search aria-hidden="true" />
          <span class="visually-hidden">Rechercher un filament</span>
          <input v-model="query" class="input" type="search" placeholder="Marque, couleur, matière" autocomplete="off" />
        </label>
        <div v-if="filaments === null" class="skeleton" style="height: 200px" />
        <ul v-else class="filaments" role="listbox" aria-label="Filaments">
          <li v-for="f in shown" :key="f.id">
            <button type="button" role="option" class="filament" :aria-selected="f.id === filamentId" @click="filamentId = f.id">
              <span class="swatch swatch--lg" :style="{ background: f.color ?? 'transparent' }" />
              <span class="filament__name">{{ f.vendor ? `${f.vendor} ${f.name}` : f.name }}</span>
              <span class="muted small">{{ f.material }}</span>
            </button>
          </li>
          <li v-if="!shown.length" class="muted small empty-line">
            Aucun filament ne correspond.
            <button type="button" class="linkish" @click="mode = 'new'; form.name = query">Créer ce filament</button>
          </li>
        </ul>
      </template>

      <!-- New filament: colour first, the rest has sensible defaults -->
      <template v-else>
        <div class="colorrow">
          <label class="colorpick">
            <span class="visually-hidden">Couleur</span>
            <input v-model="form.color" type="color" />
          </label>
          <div class="field grow">
            <label for="nf-name">Nom de la couleur</label>
            <input id="nf-name" v-model="form.name" class="input" placeholder="Blanc mat" autocomplete="off" autofocus />
          </div>
        </div>
        <div class="field">
          <span class="field__label" id="nf-mat">Matière</span>
          <div class="segmented" role="group" aria-labelledby="nf-mat">
            <button v-for="m in MATERIALS" :key="m" type="button" :aria-pressed="form.material === m" @click="form.material = m">
              {{ m }}
            </button>
          </div>
        </div>
        <div class="field">
          <label for="nf-vendor">Marque</label>
          <input id="nf-vendor" v-model="form.vendor" class="input" list="nf-vendors" placeholder="Elegoo" autocomplete="off" />
          <datalist id="nf-vendors">
            <option v-for="v in vendors" :key="v" :value="v" />
          </datalist>
        </div>
        <details class="more">
          <summary><ChevronRight aria-hidden="true" />Poids et diamètre</summary>
          <div class="more__body form-row">
            <div class="field">
              <label for="nf-weight">Poids net (g)</label>
              <input id="nf-weight" v-model.number="form.weight" class="input mono" type="number" min="1" inputmode="numeric" />
            </div>
            <div class="field">
              <label for="nf-diam">Diamètre (mm)</label>
              <input id="nf-diam" v-model.number="form.diameter" class="input mono" type="number" step="0.05" min="1" inputmode="decimal" />
            </div>
          </div>
        </details>
      </template>

      <fieldset class="step">
        <legend class="visually-hidden">Bobines</legend>
        <div class="field">
          <span class="field__label">Nombre de bobines</span>
          <div class="stepper">
            <button type="button" class="btn btn--secondary btn--icon" aria-label="Une de moins" :disabled="count <= 1" @click="count--">
              <Minus aria-hidden="true" />
            </button>
            <span class="stepper__value mono" aria-live="polite">{{ count }}</span>
            <button type="button" class="btn btn--secondary btn--icon" aria-label="Une de plus" :disabled="count >= 20" @click="count++">
              <Plus aria-hidden="true" />
            </button>
          </div>
        </div>
        <div class="field">
          <label for="ns-loc">Emplacement <span class="muted">(facultatif)</span></label>
          <input id="ns-loc" v-model="location" class="input" placeholder="Étagère A" autocomplete="off" />
        </div>
        <div v-if="state.printers.length" class="field">
          <span class="field__label" id="ns-load">Mettre tout de suite sur</span>
          <div class="segmented" role="group" aria-labelledby="ns-load">
            <button type="button" :aria-pressed="loadOn === null" @click="loadOn = null">Aucune</button>
            <button v-for="p in state.printers" :key="p.id" type="button" :aria-pressed="loadOn === p.id" @click="loadOn = p.id">
              {{ p.name }}
            </button>
          </div>
        </div>
      </fieldset>

      <p v-if="error" class="notice notice--error" role="alert">
        <AlertTriangle aria-hidden="true" />
        <span>{{ error }}</span>
      </p>
    </form>

    <template #foot>
      <button type="button" class="btn btn--ghost" @click="emit('close')">Annuler</button>
      <button type="submit" form="spool-form" class="btn btn--primary" :disabled="!canSave" :aria-busy="saving">
        {{ saving ? "Ajout…" : count > 1 ? `Ajouter les ${count} bobines` : "Ajouter la bobine" }}
      </button>
    </template>
  </AppSheet>
</template>

<style scoped>
.search { position: relative; display: block; }
.search svg { position: absolute; left: var(--space-sm); top: 50%; width: 16px; height: 16px; transform: translateY(-50%); color: var(--color-muted); pointer-events: none; }
.search .input { padding-left: 36px; }

.filaments { display: grid; max-height: 300px; overflow-y: auto; margin: 0; padding: 0; list-style: none; border-top: var(--rule-bar) solid var(--color-ink); }
.filament {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-sm);
  width: 100%;
  min-height: 48px;
  padding: 0 var(--space-sm);
  border: 0;
  border-bottom: var(--rule-hair) solid var(--color-rule);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.filament:hover { background: var(--color-paper-2); }
.filament[aria-selected="true"] { background: var(--color-accent-soft); box-shadow: inset 3px 0 0 var(--color-ink); }
.filament:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.filament__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.swatch--lg { width: 24px; height: 24px; }
.empty-line { padding: var(--space-sm) 0; display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.linkish { padding: 0; border: 0; background: none; color: inherit; text-decoration: underline; cursor: pointer; font: inherit; }

.colorrow { display: flex; gap: var(--space-md); align-items: end; }
.grow { flex: 1; }
.colorpick input {
  display: block;
  width: 56px;
  height: var(--control-h);
  padding: 0;
  border: var(--rule-hair) solid var(--color-ink);
  background: var(--color-plate);
  cursor: pointer;
}
.colorpick input:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }

.step { display: grid; gap: var(--space-md); margin: 0; padding: var(--space-md) 0 0; border: 0; border-top: var(--rule-hair) solid var(--color-rule); min-width: 0; }
.stepper { display: flex; align-items: center; gap: var(--space-sm); }
.stepper__value { min-width: 3ch; text-align: center; font-size: var(--text-xl); }
.notice--error { background: var(--color-error-soft); color: var(--color-error); }
</style>
