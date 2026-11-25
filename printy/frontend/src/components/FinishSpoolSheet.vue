<script setup lang="ts">
import { AlertTriangle } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { api } from "../api";
import { duration, grams } from "../format";
import { attempt, loadPrinters, loadSettings, state, toast } from "../store";
import type { FinishProposal, FinishResult, Job } from "../types";
import AppSheet from "./AppSheet.vue";

// Suggestions to use up a spool: prints that leave the least filament on it.
const props = defineProps<{ open: boolean; spoolId: number | null }>();
const emit = defineEmits<{ close: []; planned: [jobs: Job[]] }>();

const tags = ref<string[]>([]);
const tag = ref<string | null>(null);
const result = ref<FinishResult | null>(null);
const loading = ref(false);
const error = ref("");
const printerId = ref<number | null>(null);
const planning = ref<string | null>(null);
const remember = ref(false);

async function load() {
  if (!props.spoolId) return;
  loading.value = true;
  error.value = "";
  try {
    const q = tag.value ? `?tag=${encodeURIComponent(tag.value)}` : "";
    result.value = await api.get<FinishResult>(`/spools/${props.spoolId}/finish${q}`);
    tag.value = result.value.tag;
  } catch (e) {
    error.value = (e as Error).message;
    result.value = null;
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.spoolId] as const,
  async ([open]) => {
    if (!open) return;
    tag.value = null;
    result.value = null;
    remember.value = false;
    if (!state.printersLoaded) await attempt(loadPrinters);
    const loadedOn = state.printers.find((p) => p.loaded_spool_id === props.spoolId);
    printerId.value = loadedOn?.id ?? state.printers[0]?.id ?? null;
    const list = (await attempt(() => api.get<{ name: string; count: number }[]>("/tags"))) ?? [];
    tags.value = list.map((t) => t.name);
    await load();
  },
  { immediate: true },
);

function pickTag(t: string) {
  if (t === tag.value) return;
  tag.value = t;
  remember.value = false;
  load();
}

const spool = computed(() => result.value?.spool ?? null);
// a tag chosen by hand for this kind of spool: offer to remember it
const canRemember = computed(() => !!tag.value && !!result.value && !result.value.suggested_tags.includes(tag.value));
const kindLabel = computed(() => {
  const sp = spool.value;
  if (!sp) return "";
  return [sp.material, "de cette couleur"].filter(Boolean).join(" ");
});

async function saveRule() {
  if (!tag.value || !spool.value) return;
  if (!state.settings) await attempt(loadSettings);
  const rules = [...(state.settings?.tag_rules ?? [])];
  rules.push({ tag: tag.value, material: spool.value.material, color: spool.value.color });
  const ok = await attempt(() => api.patch("/settings", { tag_rules: rules }), `« ${tag.value} » sera proposée pour ces bobines.`);
  if (ok) {
    await attempt(loadSettings);
    remember.value = false;
    await load();
  }
}

const title = (p: FinishProposal) => (p.kind === "stock" ? "Le plus utile pour le stock" : "Le moins de reste");

async function plan(p: FinishProposal) {
  if (!props.spoolId || !printerId.value) return;
  const printer = state.printers.find((x) => x.id === printerId.value);
  if (!window.confirm(`Planifier ${p.prints} impression${p.prints > 1 ? "s" : ""} sur ${printer?.name} ?`)) return;
  planning.value = p.kind;
  const jobs = await attempt(() =>
    api.post<Job[]>(`/spools/${props.spoolId}/finish/plan`, {
      printer_id: printerId.value,
      items: p.items.map((i) => ({ sliced_id: i.sliced_id, count: i.count })),
    }),
  );
  planning.value = null;
  if (jobs) {
    toast(`${jobs.length} impression${jobs.length > 1 ? "s" : ""} planifiée${jobs.length > 1 ? "s" : ""}.`);
    emit("planned", jobs);
  }
}
</script>

<template>
  <AppSheet :open="open" title="Finir la bobine" @close="emit('close')">
    <div class="finish">
      <p v-if="error" class="notice notice--error" role="alert">
        <AlertTriangle aria-hidden="true" />
        <span>{{ error }}</span>
      </p>

      <section v-if="spool" class="head">
        <span class="swatch head__swatch" :style="{ background: spool.color ?? 'transparent' }" />
        <div>
          <strong>{{ spool.label }}</strong> <span class="mono muted small">n° {{ spool.id }}</span>
          <p class="small muted">
            <span class="mono">{{ grams(result!.remaining_g) }}</span> restants
            <template v-if="result!.planned_g">, <span class="mono">{{ grams(result!.planned_g) }}</span> déjà prévus</template>
            <template v-if="result!.reserve_g">, <span class="mono">{{ grams(result!.reserve_g) }}</span> de réserve</template>
            : <strong class="mono">{{ grams(result!.available_g) }}</strong> à utiliser
          </p>
        </div>
      </section>

      <section class="field">
        <span class="field__label">Quoi imprimer</span>
        <div v-if="tags.length" class="tagpick" role="group" aria-label="Étiquette">
          <button v-for="t in tags" :key="t" type="button" class="tag" :aria-pressed="t === tag" @click="pickTag(t)">
            {{ t }}
          </button>
        </div>
        <p v-else class="small muted">
          Ajoute une étiquette à tes modèles (par exemple « abat-jour ») depuis leur fiche dans la bibliothèque.
        </p>
        <label v-if="canRemember" class="check small">
          <input v-model="remember" type="checkbox" @change="remember && saveRule()" />
          Proposer « {{ tag }} » pour les bobines {{ kindLabel }}
        </label>
      </section>

      <div v-if="loading" class="skeleton" style="height: 180px" aria-busy="true" />

      <template v-else-if="result && tag">
        <p v-if="!result.candidates" class="muted small">
          Aucun modèle « {{ tag }} » n’a de fichier tranché {{ spool?.material ? `en ${spool.material}` : "" }} avec son poids de filament.
        </p>
        <p v-else-if="!result.proposals.length" class="muted small">
          Aucune impression « {{ tag }} » ne tient dans les {{ grams(result.available_g) }} restants.
        </p>

        <div v-if="result.proposals.length && state.printers.length > 1" class="segmented" role="group" aria-label="Imprimante">
          <button v-for="p in state.printers" :key="p.id" type="button" :aria-pressed="p.id === printerId" @click="printerId = p.id">
            {{ p.name }}
          </button>
        </div>

        <article v-for="p in result.proposals" :key="p.kind" class="proposal">
          <header class="proposal__head">
            <h3>{{ title(p) }}</h3>
            <p v-if="p.also_stock" class="small muted">C’est aussi la plus utile pour le stock.</p>
          </header>
          <ul class="items" role="list">
            <li v-for="i in p.items" :key="i.sliced_id" class="item">
              <span class="mono">{{ i.count }} ×</span>
              <span class="item__name">
                {{ i.model_name }}<span v-if="i.label" class="muted small"> ({{ i.label }})</span>
              </span>
              <span class="mono small muted">{{ grams(i.filament_g) }}</span>
              <span class="item__stock small" :class="{ 'item__stock--low': i.under_alert }">
                stock {{ i.stock }}<template v-if="i.alert != null"> / {{ i.alert }}</template>
              </span>
            </li>
          </ul>
          <dl class="summary">
            <div>
              <dt>Reste sur la bobine</dt>
              <dd class="mono">≈ {{ grams(p.leftover_g) }}</dd>
            </div>
            <div>
              <dt>{{ p.prints }} impression{{ p.prints > 1 ? "s" : "" }}</dt>
              <dd class="mono">{{ duration(p.duration_s) }}</dd>
            </div>
          </dl>
          <div class="row">
            <button type="button" class="btn btn--primary btn--sm" :disabled="!printerId || !!planning" :aria-busy="planning === p.kind" @click="plan(p)">
              Planifier ces impressions
            </button>
          </div>
        </article>

        <p v-if="result.proposals.length" class="small muted">
          Une marge de {{ result.margin_pct }} % est gardée sur chaque impression (réglable dans Réglages → Spoolman) : le
          reste réel dépend de la précision de Spoolman. Les
          impressions sont placées l’une après l’autre, en heures creuses si possible, et demandent la confirmation du plateau
          comme d’habitude.
        </p>
      </template>
    </div>

    <template #foot>
      <button type="button" class="btn btn--secondary" @click="emit('close')">Fermer</button>
    </template>
  </AppSheet>
</template>

<style scoped>
.finish { display: grid; gap: var(--space-lg); }
.head { display: flex; gap: var(--space-md); align-items: center; }
.head__swatch { width: 40px; height: 40px; }
.tagpick { display: flex; flex-wrap: wrap; gap: var(--space-2xs); }
.tagpick .tag[aria-pressed="true"] { background: var(--color-ink); color: var(--color-paper); border-color: var(--color-ink); }
.check { display: flex; gap: var(--space-xs); align-items: center; cursor: pointer; }
.proposal { display: grid; gap: var(--space-sm); padding: var(--space-md); border: var(--rule-hair) solid var(--color-ink); background: var(--color-plate); }
.proposal__head h3 { font-size: var(--text-lg); }
.items { display: grid; margin: 0; padding: 0; list-style: none; }
.item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: var(--space-sm); align-items: baseline; padding: var(--space-2xs) 0; border-bottom: var(--rule-hair) solid var(--color-rule); }
.item__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item__stock { color: var(--color-muted); }
.item__stock--low { color: var(--color-accent-strong); font-weight: 500; }
.summary { display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-xl); margin: 0; padding: var(--space-sm) var(--space-md); background: var(--color-paper-2); }
.summary div { display: grid; gap: var(--space-3xs); }
.summary dt { font-size: var(--text-sm); color: var(--color-muted); }
.summary dd { margin: 0; font-weight: 500; font-size: var(--text-lg); }
</style>
