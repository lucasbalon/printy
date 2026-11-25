<script setup lang="ts">
import { AlertTriangle, ChevronRight, Minus, Plus, X } from "lucide-vue-next";
import { computed, nextTick, ref, watch } from "vue";
import { api } from "../api";
import { duration, grams } from "../format";
import { attempt, loadPrinters, loadSettings, state, toast } from "../store";
import type { FinishCatalogItem, FinishProposal, FinishResult, Job } from "../types";
import AppSheet from "./AppSheet.vue";

// Suggestions to use up a spool: prints that leave the least filament on it,
// among some tags or every model, and a combination to compose by hand.
const props = defineProps<{ open: boolean; spoolId: number | null }>();
const emit = defineEmits<{ close: []; planned: [jobs: Job[]] }>();

const tags = ref<string[]>([]);
const selected = ref<string[]>([]); // empty: every model
const result = ref<FinishResult | null>(null);
const loading = ref(false);
const error = ref("");
const printerId = ref<number | null>(null);
const planning = ref<string | null>(null);
const savingRule = ref(false);

// the combination composed by hand
const mix = ref<{ sliced_id: number; count: number }[]>([]);
const composeOpen = ref(false);
const composer = ref<HTMLElement | null>(null);
const MAX_COUNT = 20;

async function load(initial = false) {
  if (!props.spoolId) return;
  loading.value = true;
  error.value = "";
  try {
    const q = new URLSearchParams();
    if (!initial) {
      if (selected.value.length) selected.value.forEach((t) => q.append("tag", t));
      else q.set("everything", "true");
    }
    const qs = q.toString();
    result.value = await api.get<FinishResult>(`/spools/${props.spoolId}/finish${qs ? `?${qs}` : ""}`);
    selected.value = [...result.value.tags];
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
    selected.value = [];
    result.value = null;
    mix.value = [];
    composeOpen.value = false;
    if (!state.printersLoaded) await attempt(loadPrinters);
    const loadedOn = state.printers.find((p) => p.loaded_spool_id === props.spoolId);
    printerId.value = loadedOn?.id ?? state.printers[0]?.id ?? null;
    const list = (await attempt(() => api.get<{ name: string; count: number }[]>("/tags"))) ?? [];
    tags.value = list.map((t) => t.name);
    await load(true);
  },
  { immediate: true },
);

// ---- what to print: every model, or one or more tags ----------------------------------

function toggleTag(t: string) {
  selected.value = selected.value.includes(t) ? selected.value.filter((x) => x !== t) : [...selected.value, t];
  load();
}

function pickAll() {
  if (!selected.value.length) return;
  selected.value = [];
  load();
}

const spool = computed(() => result.value?.spool ?? null);
const kindLabel = computed(() => {
  const sp = spool.value;
  if (!sp) return "";
  return [sp.material, "de cette couleur"].filter(Boolean).join(" ");
});
const quoted = (list: string[]) => list.map((t) => `« ${t} »`).join(", ");
const selectionLabel = computed(() => (selected.value.length ? quoted(selected.value) : "parmi tous les modèles"));

// chosen tags not proposed yet for this kind of spool: offer to remember them
const toRemember = computed(() => selected.value.filter((t) => !result.value?.suggested_tags.includes(t)));

async function rememberTags() {
  if (!toRemember.value.length || !spool.value) return;
  savingRule.value = true;
  await attempt(loadSettings);
  const rules = [...(state.settings?.tag_rules ?? [])];
  for (const t of toRemember.value) rules.push({ tag: t, material: spool.value.material, color: spool.value.color });
  const ok = await attempt(() => api.patch("/settings", { tag_rules: rules }), `${quoted(toRemember.value)} sera proposée d’office pour ces bobines.`);
  savingRule.value = false;
  if (ok) {
    await attempt(loadSettings);
    await load();
  }
}

async function forgetRule(index: number, tag: string) {
  savingRule.value = true;
  await attempt(loadSettings);
  const rules = (state.settings?.tag_rules ?? []).filter((_, i) => i !== index);
  const ok = await attempt(() => api.patch("/settings", { tag_rules: rules }), `« ${tag} » ne sera plus proposée d’office.`);
  savingRule.value = false;
  if (ok) {
    await attempt(loadSettings);
    await load();
  }
}

// ---- proposals ---------------------------------------------------------------------

const title = (p: FinishProposal) => (p.kind === "stock" ? "Le plus utile pour le stock" : "Le moins de reste");

async function adjust(p: FinishProposal) {
  mix.value = p.items.map((i) => ({ sliced_id: i.sliced_id, count: i.count }));
  composeOpen.value = true;
  await nextTick();
  composer.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- the combination composed by hand -----------------------------------------------

const catalog = computed(() => result.value?.catalog ?? []);
const byId = computed(() => new Map(catalog.value.map((c) => [c.sliced_id, c])));
const mixRows = computed(() =>
  mix.value
    .map((m) => ({ ...m, item: byId.value.get(m.sliced_id) }))
    .filter((r): r is { sliced_id: number; count: number; item: FinishCatalogItem } => !!r.item),
);
const mixUsed = computed(() => mixRows.value.reduce((a, r) => a + r.item.filament_g * r.count, 0));
const mixNeed = computed(() => mixRows.value.reduce((a, r) => a + r.item.need_g * r.count, 0));
const mixPrints = computed(() => mixRows.value.reduce((a, r) => a + r.count, 0));
const mixDuration = computed(() => mixRows.value.reduce((a, r) => a + r.item.duration_s * r.count, 0));
const mixLeft = computed(() => (result.value ? result.value.available_g - mixUsed.value : 0));
// with the margin kept on each print: how much is missing for the whole combination to fit
const mixMissing = computed(() => (result.value ? Math.max(0, mixNeed.value - result.value.available_g) : 0));

const itemLabel = (c: FinishCatalogItem) => `${c.model_name}${c.label ? ` (${c.label})` : ""}`;

function addToMix(e: Event) {
  const el = e.target as HTMLSelectElement;
  const id = Number(el.value);
  el.value = "";
  if (!id) return;
  const row = mix.value.find((m) => m.sliced_id === id);
  if (row) row.count = Math.min(MAX_COUNT, row.count + 1);
  else mix.value.push({ sliced_id: id, count: 1 });
}

function step(id: number, delta: number) {
  const row = mix.value.find((m) => m.sliced_id === id);
  if (!row) return;
  row.count = Math.min(MAX_COUNT, row.count + delta);
  if (row.count <= 0) mix.value = mix.value.filter((m) => m.sliced_id !== id);
}

// ---- planning ----------------------------------------------------------------------

async function plan(key: string, items: { sliced_id: number; count: number }[]) {
  if (!props.spoolId || !printerId.value || !items.length) return;
  const prints = items.reduce((a, i) => a + i.count, 0);
  const printer = state.printers.find((x) => x.id === printerId.value);
  if (!window.confirm(`Planifier ${prints} impression${prints > 1 ? "s" : ""} sur ${printer?.name} ?`)) return;
  planning.value = key;
  const jobs = await attempt(() =>
    api.post<Job[]>(`/spools/${props.spoolId}/finish/plan`, {
      printer_id: printerId.value,
      items: items.map((i) => ({ sliced_id: i.sliced_id, count: i.count })),
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

      <section v-if="result" class="field">
        <span class="field__label">Quoi imprimer</span>
        <div class="tagpick" role="group" aria-label="Modèles à proposer">
          <button type="button" class="tag" :aria-pressed="!selected.length" @click="pickAll">Tous les modèles</button>
          <button v-for="t in tags" :key="t" type="button" class="tag" :aria-pressed="selected.includes(t)" @click="toggleTag(t)">
            {{ t }}
          </button>
        </div>
        <span v-if="tags.length" class="field__help">Touche une ou plusieurs étiquettes, touche-la à nouveau pour la retirer.</span>
        <span v-else class="field__help">
          Ajoute des étiquettes à tes modèles (par exemple « abat-jour ») depuis leur fiche pour ne viser qu’une famille.
        </span>

        <ul v-if="result.rules.length" class="rules" role="list">
          <li v-for="r in result.rules" :key="r.index" class="rules__row small">
            <span>« {{ r.tag }} » est proposée d’office pour les bobines {{ kindLabel }}</span>
            <button type="button" class="btn btn--ghost btn--sm" :disabled="savingRule" @click="forgetRule(r.index, r.tag)">
              <X aria-hidden="true" />Ne plus proposer
            </button>
          </li>
        </ul>
        <button v-if="toRemember.length" type="button" class="btn btn--ghost btn--sm rules__add" :disabled="savingRule" @click="rememberTags">
          Proposer {{ quoted(toRemember) }} d’office pour les bobines {{ kindLabel }}
        </button>
      </section>

      <div v-if="loading" class="skeleton" style="height: 180px" aria-busy="true" />

      <template v-else-if="result">
        <div v-if="state.printers.length > 1" class="segmented" role="group" aria-label="Imprimante">
          <button v-for="p in state.printers" :key="p.id" type="button" :aria-pressed="p.id === printerId" @click="printerId = p.id">
            {{ p.name }}
          </button>
        </div>

        <p v-if="!result.candidates" class="muted small">
          Aucun modèle {{ selected.length ? selectionLabel : "" }} n’a de fichier tranché
          {{ spool?.material ? `en ${spool.material}` : "" }} avec son poids de filament.
        </p>
        <p v-else-if="!result.proposals.length" class="muted small">
          Aucune impression {{ selectionLabel }} ne tient dans les {{ grams(result.available_g) }} restants.
        </p>

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
            <button type="button" class="btn btn--primary btn--sm" :disabled="!printerId || !!planning" :aria-busy="planning === p.kind" @click="plan(p.kind, p.items)">
              Planifier ces impressions
            </button>
            <button type="button" class="btn btn--ghost btn--sm" @click="adjust(p)">Ajuster</button>
          </div>
        </article>

        <!-- compose by hand, from every model that fits the spool -->
        <div ref="composer">
          <details class="more" :open="composeOpen" @toggle="composeOpen = ($event.target as HTMLDetailsElement).open">
            <summary><ChevronRight aria-hidden="true" />Composer ma combinaison</summary>
            <div class="more__body">
              <p v-if="!catalog.length" class="small muted">
                Aucun modèle n’a de fichier tranché {{ spool?.material ? `en ${spool.material}` : "" }} avec son poids de filament.
              </p>
              <template v-else>
                <ul v-if="mixRows.length" class="items" role="list">
                  <li v-for="r in mixRows" :key="r.sliced_id" class="item item--mix">
                    <span class="stepper">
                      <button type="button" class="btn btn--ghost btn--icon btn--sm" :aria-label="`Une impression de ${r.item.model_name} en moins`" @click="step(r.sliced_id, -1)">
                        <Minus aria-hidden="true" />
                      </button>
                      <span class="mono">{{ r.count }}</span>
                      <button type="button" class="btn btn--ghost btn--icon btn--sm" :aria-label="`Une impression de ${r.item.model_name} en plus`" :disabled="r.count >= MAX_COUNT" @click="step(r.sliced_id, 1)">
                        <Plus aria-hidden="true" />
                      </button>
                    </span>
                    <span class="item__name">
                      {{ r.item.model_name }}<span v-if="r.item.label" class="muted small"> ({{ r.item.label }})</span>
                    </span>
                    <span class="mono small muted">{{ grams(r.item.filament_g * r.count) }}</span>
                  </li>
                </ul>
                <p v-else class="small muted">Ajoute des impressions, ou touche « Ajuster » sur une proposition pour partir d’elle.</p>

                <label class="visually-hidden" for="mix-add">Ajouter une impression</label>
                <select id="mix-add" class="select" @change="addToMix">
                  <option value="">Ajouter une impression…</option>
                  <option v-for="c in catalog" :key="c.sliced_id" :value="c.sliced_id">
                    {{ itemLabel(c) }}, {{ grams(c.filament_g) }}
                  </option>
                </select>

                <dl v-if="mixRows.length" class="summary" :class="{ 'summary--over': mixMissing > 0 }">
                  <div>
                    <dt>{{ mixMissing > 0 ? "Il manque" : "Reste sur la bobine" }}</dt>
                    <dd class="mono">≈ {{ grams(mixMissing > 0 ? mixMissing : mixLeft) }}</dd>
                  </div>
                  <div>
                    <dt>{{ mixPrints }} impression{{ mixPrints > 1 ? "s" : "" }}</dt>
                    <dd class="mono">{{ duration(mixDuration) }}</dd>
                  </div>
                </dl>
                <p v-if="mixMissing > 0" class="small item__stock--low">
                  Ça ne tient pas sur la bobine avec la marge de {{ result.margin_pct }} % : retire une impression.
                </p>
                <div v-if="mixRows.length" class="row">
                  <button
                    type="button"
                    class="btn btn--primary btn--sm"
                    :disabled="!printerId || !!planning || mixMissing > 0"
                    :aria-busy="planning === 'mix'"
                    @click="plan('mix', mix)"
                  >
                    Planifier ma combinaison
                  </button>
                  <button type="button" class="btn btn--ghost btn--sm" @click="mix = []">Tout retirer</button>
                </div>
              </template>
            </div>
          </details>
        </div>

        <p class="small muted">
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
.tagpick .tag { cursor: pointer; }
.tagpick .tag[aria-pressed="true"] { background: var(--color-ink); color: var(--color-paper); border-color: var(--color-ink); }
.rules { display: grid; gap: var(--space-2xs); margin: var(--space-xs) 0 0; padding: 0; list-style: none; }
.rules__row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-xs); }
.rules__row svg { width: 14px; height: 14px; }
.rules__add { justify-self: start; text-align: left; height: auto; padding-block: var(--space-2xs); }
.proposal { display: grid; gap: var(--space-sm); padding: var(--space-md); border: var(--rule-hair) solid var(--color-ink); background: var(--color-plate); }
.proposal__head h3 { font-size: var(--text-lg); }
.items { display: grid; margin: 0; padding: 0; list-style: none; }
.item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: var(--space-sm); align-items: baseline; padding: var(--space-2xs) 0; border-bottom: var(--rule-hair) solid var(--color-rule); }
.item--mix { grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; }
.item__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item__stock { color: var(--color-muted); }
.item__stock--low { color: var(--color-accent-strong); font-weight: 500; }
.stepper { display: inline-flex; align-items: center; gap: var(--space-2xs); }
.stepper .mono { min-width: 2ch; text-align: center; }
.summary { display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-xl); margin: 0; padding: var(--space-sm) var(--space-md); background: var(--color-paper-2); }
.summary div { display: grid; gap: var(--space-3xs); }
.summary dt { font-size: var(--text-sm); color: var(--color-muted); }
.summary dd { margin: 0; font-weight: 500; font-size: var(--text-lg); }
.summary--over div:first-child dd { color: var(--color-accent-strong); }
</style>
