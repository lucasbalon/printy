<script setup lang="ts">
import { FileQuestion } from "lucide-vue-next";
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api";
import { duration, grams } from "../format";
import { attempt, libraryVersion, loadPending, state, toast } from "../store";
import type { ModelFull, ModelSummary, PendingFile, SlicedFile } from "../types";

// Files whose name matches no model: the user files each one, into a new model or an
// existing one (possibly replacing the version for the same printer and material).
const props = defineProps<{ models: ModelSummary[] }>();
const emit = defineEmits<{ filed: [modelId: number] }>();

const items = ref<PendingFile[]>([]);

interface Choice {
  mode: "new" | "existing";
  name: string;
  modelId: number | null;
  replaceable: SlicedFile | null;
  replace: boolean;
  busy: boolean;
}
const choices = reactive<Record<number, Choice>>({});

async function load() {
  items.value = (await attempt(() => api.get<PendingFile[]>("/pending"))) ?? [];
  for (const p of items.value) {
    choices[p.id] ??= { mode: "new", name: p.suggested_name, modelId: null, replaceable: null, replace: true, busy: false };
  }
  state.pending = items.value.length;
}
watch(libraryVersion, load, { immediate: true });

const sortedModels = computed(() => [...props.models].sort((a, b) => a.name.localeCompare(b.name)));

function same(a: string | null | undefined, b: string | null | undefined) {
  return (a ?? "").toLowerCase() === (b ?? "").toLowerCase();
}

async function pickModel(p: PendingFile, id: number | null) {
  const c = choices[p.id];
  c.modelId = id;
  c.replaceable = null;
  if (!id || p.kind !== "sliced") return;
  const m = await attempt(() => api.get<ModelFull>(`/models/${id}`));
  const match = (m?.sliced ?? []).filter((v) => same(v.printer_model, p.printer_model) && same(v.material, p.material));
  c.replaceable = match.at(-1) ?? null;
  c.replace = true;
}

async function file(p: PendingFile) {
  const c = choices[p.id];
  if (c.mode === "existing" && !c.modelId) return;
  c.busy = true;
  const body = c.mode === "new" ? { new_name: c.name } : { model_id: c.modelId, replace: !!c.replaceable && c.replace };
  const res = await attempt(() => api.post<{ model_id: number; model_name: string }>(`/pending/${p.id}/file`, body));
  c.busy = false;
  if (!res) return;
  toast(`Rangé dans « ${res.model_name} ».`);
  delete choices[p.id];
  libraryVersion.value++;
  emit("filed", res.model_id);
}

async function discard(p: PendingFile) {
  if (!window.confirm(`Ignorer « ${p.filename} » ? Le fichier sera supprimé.`)) return;
  if ((await attempt(() => api.del(`/pending/${p.id}`))) !== undefined) {
    delete choices[p.id];
    await load();
    await loadPending();
  }
}

const thumb = (p: PendingFile) => `api/v1/pending/${p.id}/thumbnail`;
</script>

<template>
  <section v-if="items.length" class="pending panel" aria-labelledby="pending-title">
    <header class="pending__head">
      <h2 id="pending-title">À trier <span class="mono">{{ items.length }}</span></h2>
      <p class="small muted">Le nom de ces fichiers ne correspond à aucun modèle : range-les dans un nouveau modèle ou un modèle existant.</p>
    </header>
    <ul class="pending__list" role="list">
      <li v-for="p in items" :key="p.id" class="item">
        <div class="item__thumb">
          <img v-if="p.has_thumbnail" :src="thumb(p)" alt="" width="72" height="72" />
          <FileQuestion v-else aria-hidden="true" />
        </div>
        <div class="item__body">
          <p class="item__file mono small">{{ p.filename }}</p>
          <p class="small muted">
            <template v-if="p.kind === 'source'">Fichier source</template>
            <template v-else>
              <span class="mono">{{ duration(p.duration_s) }}</span>
              <template v-if="p.filament_g">, <span class="mono">{{ grams(p.filament_g) }}</span></template>
              <template v-if="p.material">, {{ p.material }}</template>
              <template v-if="p.printer_model">, {{ p.printer_model }}</template>
            </template>
          </p>

          <div class="segmented" role="group" :aria-label="`Ranger ${p.filename}`">
            <button type="button" :aria-pressed="choices[p.id].mode === 'new'" @click="choices[p.id].mode = 'new'">Nouveau modèle</button>
            <button type="button" :aria-pressed="choices[p.id].mode === 'existing'" :disabled="!models.length" @click="choices[p.id].mode = 'existing'">
              Modèle existant
            </button>
          </div>

          <div class="item__form">
            <input
              v-if="choices[p.id].mode === 'new'"
              v-model="choices[p.id].name"
              class="input"
              :aria-label="`Nom du nouveau modèle pour ${p.filename}`"
              maxlength="200"
            />
            <select
              v-else
              class="input"
              :value="choices[p.id].modelId ?? ''"
              :aria-label="`Modèle pour ${p.filename}`"
              @change="pickModel(p, Number(($event.target as HTMLSelectElement).value) || null)"
            >
              <option value="">Choisir un modèle…</option>
              <option v-for="m in sortedModels" :key="m.id" :value="m.id">{{ m.name }}</option>
            </select>
            <button
              type="button"
              class="btn btn--primary btn--sm"
              :disabled="choices[p.id].busy || (choices[p.id].mode === 'existing' ? !choices[p.id].modelId : !choices[p.id].name.trim())"
              :aria-busy="choices[p.id].busy"
              @click="file(p)"
            >
              Ranger
            </button>
            <button type="button" class="btn btn--ghost btn--sm" @click="discard(p)">Ignorer</button>
          </div>

          <label v-if="choices[p.id].mode === 'existing' && choices[p.id].replaceable" class="check small">
            <input v-model="choices[p.id].replace" type="checkbox" />
            Remplacer la version actuelle ({{ choices[p.id].replaceable!.filename }}) : elle sera archivée avec son historique, les
            impressions prévues passeront sur ce fichier.
          </label>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.pending { display: grid; gap: var(--space-md); margin-bottom: var(--space-xl); padding: var(--space-md); border-color: var(--color-accent); }
.pending__head { display: grid; gap: var(--space-3xs); }
.pending__head h2 { font-size: var(--text-lg); }
.pending__list { display: grid; margin: 0; padding: 0; list-style: none; }
.item { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: var(--space-md); padding: var(--space-sm) 0; border-top: var(--rule-hair) solid var(--color-rule); }
.item__thumb { display: grid; place-items: center; width: 72px; height: 72px; background: var(--color-media); color: var(--color-media-ink); overflow: hidden; }
.item__thumb img { width: 100%; height: 100%; object-fit: cover; }
.item__thumb svg { width: 28px; height: 28px; }
.item__body { display: grid; gap: var(--space-xs); min-width: 0; }
.item__file { overflow-wrap: anywhere; margin: 0; }
.item__form { display: flex; flex-wrap: wrap; gap: var(--space-xs); align-items: center; }
.item__form .input { flex: 1 1 220px; min-width: 0; }
.check { display: flex; gap: var(--space-xs); align-items: flex-start; cursor: pointer; }
.check input { margin-top: 3px; }
</style>
