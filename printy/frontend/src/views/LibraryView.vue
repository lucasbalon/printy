<script setup lang="ts">
import { Search, Upload } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import ModelThumb from "../components/ModelThumb.vue";
import { duration, grams } from "../format";
import { libraryVersion, uploadFiles, uploading, uploadProgress } from "../store";
import type { ModelSummary } from "../types";

const route = useRoute();
const router = useRouter();

const models = ref<ModelSummary[] | null>(null);
const tags = ref<{ name: string; count: number }[]>([]);
const failed = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

// Search and tag live in the URL
const q = ref(typeof route.query.q === "string" ? route.query.q : "");
const tag = computed(() => (typeof route.query.tag === "string" ? route.query.tag : ""));

let debounce: number | undefined;
watch(q, (v) => {
  window.clearTimeout(debounce);
  debounce = window.setTimeout(() => router.replace({ query: { ...route.query, q: v || undefined } }), 250);
});

async function load() {
  try {
    const params = new URLSearchParams();
    if (route.query.q) params.set("q", String(route.query.q));
    if (tag.value) params.set("tag", tag.value);
    const [m, t] = await Promise.all([
      api.get<ModelSummary[]>(`/models?${params}`),
      api.get<{ name: string; count: number }[]>("/tags"),
    ]);
    models.value = m;
    tags.value = t;
    failed.value = false;
  } catch {
    failed.value = true;
  }
}
watch(() => [route.query.q, route.query.tag, libraryVersion.value], load, { immediate: true });

function toggleTag(name: string) {
  router.replace({ query: { ...route.query, tag: tag.value === name ? undefined : name } });
}

async function onFiles(e: Event) {
  const input = e.target as HTMLInputElement;
  const res = await uploadFiles(Array.from(input.files ?? []));
  input.value = "";
  if (res?.imported.length === 1) router.push(`/bibliotheque/${res.imported[0].model_id}`);
}

const filtering = computed(() => !!route.query.q || !!tag.value);
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="page-head__title">
        <h1>Bibliothèque</h1>
        <p v-if="models" class="muted small">
          <span class="mono">{{ models.length }}</span> modèle{{ models.length > 1 ? "s" : "" }}{{ filtering ? " trouvés" : "" }}
        </p>
      </div>
      <div class="page-head__actions">
        <label class="search">
          <Search aria-hidden="true" />
          <span class="visually-hidden">Rechercher</span>
          <input v-model="q" class="input" type="search" placeholder="Rechercher" autocomplete="off" />
        </label>
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".gcode,.gco,.g,.3mf,.stl,.obj,.step,.stp,.blend"
          class="visually-hidden"
          tabindex="-1"
          @change="onFiles"
        />
        <button type="button" class="btn btn--primary" :aria-busy="uploading" :disabled="uploading" @click="fileInput?.click()">
          <Upload aria-hidden="true" />{{ uploading ? `Import… ${Math.round(uploadProgress * 100)} %` : "Ajouter des fichiers" }}
        </button>
      </div>
    </header>

    <div v-if="tags.length" class="tags" role="group" aria-label="Filtrer par étiquette">
      <button
        v-for="t in tags"
        :key="t.name"
        type="button"
        class="tag"
        :aria-pressed="tag === t.name"
        @click="toggleTag(t.name)"
      >
        {{ t.name }} <span class="mono">{{ t.count }}</span>
      </button>
    </div>

    <div v-if="failed" class="empty empty--error">
      <h2>Bibliothèque indisponible</h2>
      <p>Printy ne répond pas. Vérifie que l’add-on tourne, puis réessaie.</p>
      <button type="button" class="btn btn--secondary" @click="load">Réessayer</button>
    </div>

    <div v-else-if="models === null" class="grid" aria-busy="true">
      <div v-for="i in 8" :key="i" class="card tile">
        <div class="skeleton" style="aspect-ratio: 1" />
        <div class="tile__body"><div class="skeleton" style="height: 16px; width: 70%" /></div>
      </div>
    </div>

    <div v-else-if="!models.length && !filtering" class="empty">
      <h2>Ta bibliothèque est vide</h2>
      <p>Ajoute tes fichiers tranchés : la miniature, la durée, le filament et la couleur sont lus automatiquement. Les fichiers d’un même objet sont regroupés.</p>
      <ol class="ways">
        <li><strong>Glisse-dépose</strong> tes .gcode ou .gcode.3mf n’importe où dans Printy.</li>
        <li><strong>Envoie depuis Orca Slicer</strong> comme vers une imprimante OctoPrint (voir Réglages).</li>
        <li><strong>Copie-les</strong> dans le dossier partagé <span class="mono">share/printy/inbox</span>.</li>
      </ol>
      <button type="button" class="btn btn--primary" @click="fileInput?.click()">Choisir des fichiers</button>
    </div>

    <div v-else-if="!models.length" class="empty">
      <h2>Aucun résultat</h2>
      <p>Aucun modèle ne correspond à cette recherche.</p>
      <RouterLink to="/bibliotheque" class="btn btn--secondary" @click="q = ''">Tout afficher</RouterLink>
    </div>

    <ul v-else class="grid" role="list">
      <li v-for="m in models" :key="m.id">
        <RouterLink :to="`/bibliotheque/${m.id}`" class="card tile">
          <ModelThumb :sliced-id="m.cover_id" :alt="''" />
          <div class="tile__body">
            <h2 class="tile__name">{{ m.name }}</h2>
            <p class="tile__meta">
              <span class="mono">{{ duration(m.duration_s) }}</span>
              <span v-if="m.filament_g" class="mono">{{ grams(m.filament_g) }}</span>
              <span v-for="c in m.colors.slice(0, 4)" :key="c" class="swatch" :style="{ background: c }" />
            </p>
            <p v-if="m.sliced_count === 0" class="small muted">Source seule, pas encore tranché</p>
            <p v-else-if="m.sliced_count > 1" class="small muted">{{ m.sliced_count }} versions</p>
          </div>
        </RouterLink>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.search { position: relative; display: block; flex: 0 1 280px; min-width: 180px; }
.search svg { position: absolute; left: var(--space-sm); top: 50%; width: 16px; height: 16px; transform: translateY(-50%); color: var(--color-muted); pointer-events: none; }
.search .input { padding-left: 36px; }

.tags { display: flex; flex-wrap: wrap; gap: var(--space-2xs); margin-bottom: var(--space-lg); }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(200px, 100%), 1fr));
  gap: var(--space-lg) var(--space-lg);
  margin: 0;
  padding: 0 var(--space-xs) var(--space-xs) 0;
  list-style: none;
}
.tile { display: grid; grid-template-rows: auto 1fr; height: 100%; }
.tile :deep(.thumb) { border-bottom: var(--rule-hair) solid var(--color-rule); }
.tile__body { display: grid; align-content: start; gap: var(--space-2xs); padding: var(--space-sm) var(--space-sm) var(--space-md); }
.tile__name { font-family: var(--font-body); font-weight: 500; font-size: var(--text-md); letter-spacing: 0; line-height: 1.3; }
.tile__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2xs) var(--space-sm); font-size: var(--text-sm); color: var(--color-ink-2); }

.ways { display: grid; gap: var(--space-xs); margin: 0; padding-left: 1.2em; max-width: 60ch; }
.ways li { padding-left: var(--space-2xs); }
</style>
