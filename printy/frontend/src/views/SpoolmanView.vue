<script setup lang="ts">
import { ExternalLink, Plus } from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import NewSpoolSheet from "../components/NewSpoolSheet.vue";
import { grams } from "../format";
import { attempt, loadSettings, loadSpools, state, toast, usePolling } from "../store";
import type { Spool } from "../types";

// What is on the shelf, from Spoolman, with the actions that matter day to day.
const route = useRoute();
const router = useRouter();
const failed = ref(false);

usePolling(async () => {
  try {
    await loadSpools(true);
    failed.value = false;
  } catch {
    failed.value = true;
  }
}, 30000);
onMounted(() => {
  if (!state.settings) attempt(loadSettings);
  if (route.query.nouvelle) {
    openNew(null);
    router.replace({ query: {} });
  }
});

const url = computed(() => state.settings?.spoolman_url ?? "");
const filter = ref<"all" | "low">("all");

// grouped by filament (same colour, material and brand)
interface Group {
  key: string;
  filamentId: number | null;
  spool: Spool;
  spools: Spool[];
  total: number;
}
const groups = computed<Group[]>(() => {
  const map = new Map<string, Group>();
  for (const sp of state.spools.spools) {
    const key = sp.filament_id != null ? `f${sp.filament_id}` : `s${sp.id}`;
    const g = map.get(key) ?? { key, filamentId: sp.filament_id ?? null, spool: sp, spools: [], total: 0 };
    g.spools.push(sp);
    g.total += sp.remaining_g ?? 0;
    map.set(key, g);
  }
  const list = [...map.values()];
  for (const g of list) g.spools.sort((a, b) => (a.remaining_g ?? 0) - (b.remaining_g ?? 0));
  const shown = filter.value === "low" ? list.filter((g) => g.total < 250) : list;
  return shown.sort((a, b) => (a.spool.material ?? "").localeCompare(b.spool.material ?? "") || a.spool.name.localeCompare(b.spool.name));
});

function share(sp: Spool) {
  if (sp.remaining_g == null || !sp.initial_g) return null;
  return Math.max(0, Math.min(1, sp.remaining_g / sp.initial_g));
}

// new spool sheet
const sheetOpen = ref(false);
const presetFilament = ref<number | null>(null);
function openNew(filamentId: number | null) {
  presetFilament.value = filamentId;
  sheetOpen.value = true;
}
async function onCreated(created: Spool[]) {
  sheetOpen.value = false;
  toast(created.length > 1 ? `${created.length} bobines ajoutées dans Spoolman.` : "Bobine ajoutée dans Spoolman.");
  await attempt(() => loadSpools(true));
}

async function archive(sp: Spool) {
  if (!window.confirm(`La bobine n° ${sp.id} (${sp.label}) est vide ? Elle sera archivée dans Spoolman.`)) return;
  const ok = await attempt(() => api.post(`/spoolman/spools/${sp.id}/archive`), "Bobine archivée.");
  if (ok) await attempt(() => loadSpools(true));
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="page-head__title">
        <h1>Bobines</h1>
        <p v-if="state.spools.enabled" class="muted small">
          <span class="mono">{{ state.spools.spools.length }}</span> bobine{{ state.spools.spools.length > 1 ? "s" : "" }} dans Spoolman
        </p>
      </div>
      <div class="page-head__actions">
        <a v-if="url" :href="url" target="_blank" rel="noopener" class="btn btn--ghost">
          <ExternalLink aria-hidden="true" />Ouvrir Spoolman
        </a>
        <button v-if="state.spools.enabled" type="button" class="btn btn--primary" @click="openNew(null)">
          <Plus aria-hidden="true" />Nouvelle bobine
        </button>
      </div>
    </header>

    <div v-if="state.settings && !url" class="empty">
      <h2>Spoolman n’est pas configuré</h2>
      <p>Indique l’adresse de Spoolman dans les réglages pour suivre tes bobines ici.</p>
      <RouterLink to="/reglages" class="btn btn--primary">Ouvrir les réglages</RouterLink>
    </div>

    <div v-else-if="state.spools.error || failed" class="empty empty--error">
      <h2>Spoolman ne répond pas</h2>
      <p>{{ state.spools.error ?? "Vérifie que Spoolman tourne." }}</p>
      <button type="button" class="btn btn--secondary" @click="attempt(() => loadSpools(true))">Réessayer</button>
    </div>

    <div v-else-if="!state.spools.enabled" class="grid" aria-busy="true">
      <div v-for="i in 4" :key="i" class="skeleton" style="height: 160px" />
    </div>

    <div v-else-if="!state.spools.spools.length" class="empty">
      <h2>Aucune bobine</h2>
      <p>Ajoute ta première bobine : une couleur, une matière, c’est tout.</p>
      <button type="button" class="btn btn--primary" @click="openNew(null)">Nouvelle bobine</button>
    </div>

    <template v-else>
      <div class="segmented filter" role="group" aria-label="Filtrer">
        <button type="button" :aria-pressed="filter === 'all'" @click="filter = 'all'">Toutes</button>
        <button type="button" :aria-pressed="filter === 'low'" @click="filter = 'low'">Presque vides</button>
      </div>

      <ul class="grid" role="list">
        <li v-for="g in groups" :key="g.key" class="card group">
          <header class="group__head">
            <span class="group__swatch" :style="{ background: g.spool.color ?? 'transparent' }" />
            <div class="group__title">
              <h2>{{ g.spool.label }}</h2>
              <p class="muted small">
                {{ g.spool.material ?? "matière inconnue" }}, {{ g.spools.length }} bobine{{ g.spools.length > 1 ? "s" : "" }},
                <span class="mono">{{ grams(g.total) }}</span> au total
              </p>
            </div>
          </header>

          <ul class="spools" role="list">
            <li v-for="sp in g.spools" :key="sp.id" class="spool">
              <span class="spool__id mono">n° {{ sp.id }}</span>
              <span class="spool__gauge" :aria-label="`${grams(sp.remaining_g)} restants`">
                <span class="progress"><span class="progress__bar" :style="{ transform: `scaleX(${share(sp) ?? 0})` }" /></span>
                <span class="mono small">{{ sp.remaining_g != null ? grams(sp.remaining_g) : "–" }}</span>
              </span>
              <span class="spool__tags small">
                <span v-if="sp.loaded_on" class="status status--active">Sur {{ sp.loaded_on }}</span>
                <span v-if="sp.planned_g" class="muted">{{ grams(sp.planned_g) }} prévus</span>
                <span v-if="sp.location" class="muted">{{ sp.location }}</span>
              </span>
              <button type="button" class="btn btn--ghost btn--sm" @click="archive(sp)">Vide</button>
            </li>
          </ul>

          <button type="button" class="btn btn--ghost btn--sm group__add" @click="openNew(g.filamentId)">
            <Plus aria-hidden="true" />Une bobine de plus
          </button>
        </li>
      </ul>
    </template>

    <NewSpoolSheet :open="sheetOpen" :filament-id="presetFilament" @close="sheetOpen = false" @created="onCreated" />
  </div>
</template>

<style scoped>
.filter { margin-bottom: var(--space-lg); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(380px, 100%), 1fr)); gap: var(--space-lg); margin: 0; padding: 0 var(--space-xs) var(--space-xs) 0; list-style: none; }
.group { display: grid; gap: var(--space-sm); align-content: start; padding: var(--space-md); }
.group__head { display: flex; gap: var(--space-md); align-items: center; }
.group__swatch { flex: none; width: 48px; height: 48px; border: var(--rule-hair) solid var(--color-ink); }
.group__title { display: grid; gap: var(--space-3xs); min-width: 0; }
.group__title h2 { font-family: var(--font-body); font-weight: 500; font-size: var(--text-lg); letter-spacing: 0; max-width: none; }
.spools { display: grid; margin: 0; padding: 0; list-style: none; border-top: var(--rule-hair) solid var(--color-rule); }
.spool { display: grid; grid-template-columns: auto minmax(90px, 1fr) auto auto; align-items: center; gap: var(--space-sm); min-height: 44px; border-bottom: var(--rule-hair) solid var(--color-rule); }
.spool__id { font-size: var(--text-sm); color: var(--color-muted); }
.spool__gauge { display: grid; grid-template-columns: minmax(40px, 1fr) auto; align-items: center; gap: var(--space-xs); }
.spool__tags { display: flex; flex-wrap: wrap; gap: var(--space-3xs) var(--space-xs); justify-content: flex-end; }
.group__add { justify-self: start; }
@media (max-width: 40rem) {
  .spool { grid-template-columns: auto minmax(0, 1fr) auto; }
  .spool > .btn { grid-column: 3; grid-row: 1; }
  .spool__tags { grid-column: 1 / -1; grid-row: 2; justify-content: flex-start; padding-bottom: var(--space-xs); }
}
</style>
