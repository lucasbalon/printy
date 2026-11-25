<script setup lang="ts">
import { ArrowLeft, Box, CalendarPlus, Download, Plus, Trash2, X } from "lucide-vue-next";
import { computed, defineAsyncComponent, nextTick, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, urls } from "../api";
import ModelThumb from "../components/ModelThumb.vue";
import { bytes, duration, grams, kwh, when } from "../format";
import { attempt, libraryVersion, toast, uploadFiles, uploading, uploadProgress } from "../store";
import type { ModelFull, Product } from "../types";

const props = defineProps<{ id: string }>();
const router = useRouter();

const model = ref<ModelFull | null>(null);
const missing = ref(false);
const selected = ref<number | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const sourceInput = ref<HTMLInputElement | null>(null);

// inline edits
const editingName = ref(false);
const name = ref("");
const nameInput = ref<HTMLInputElement | null>(null);
const description = ref("");
const newTag = ref("");

// stock and cost of this product
const product = ref<Product | null>(null);
async function loadProduct() {
  const all = await api.get<Product[]>("/stock");
  product.value = all.find((p) => p.id === Number(props.id)) ?? null;
}

async function load() {
  try {
    loadProduct().catch(() => undefined);
    model.value = await api.get<ModelFull>(`/models/${props.id}`);
    missing.value = false;
    name.value = model.value.name;
    description.value = model.value.description;
    if (!model.value.sliced.some((s) => s.id === selected.value)) {
      selected.value = model.value.sliced.find((s) => s.has_thumbnail)?.id ?? model.value.sliced[0]?.id ?? null;
    }
  } catch {
    missing.value = true;
  }
}
watch(() => [props.id, libraryVersion.value], load, { immediate: true });

const cover = computed(() => model.value?.sliced.find((s) => s.id === selected.value) ?? null);

// 3D preview of a source file (three.js only loads when asked)
const ModelPreview3D = defineAsyncComponent(() => import("../components/ModelPreview3D.vue"));
const previewable = computed(() => (model.value?.sources ?? []).filter((f) => /\.(stl|3mf)$/i.test(f.filename)));
const view = ref<"thumb" | "3d">("thumb");
const previewId = ref<number | null>(null);
const previewFile = computed(() => previewable.value.find((f) => f.id === previewId.value) ?? previewable.value[0] ?? null);
function show3d(id: number) {
  previewId.value = id;
  view.value = "3d";
  window.scrollTo({ top: 0, behavior: "smooth" });
}
watch(() => props.id, () => {
  view.value = "thumb";
  previewId.value = null;
});

async function patch(body: Record<string, unknown>, success?: string) {
  const res = await attempt(() => api.patch<ModelFull>(`/models/${props.id}`, body), success);
  if (res) model.value = res;
}

async function startRename() {
  editingName.value = true;
  await nextTick();
  nameInput.value?.select();
}
async function saveName() {
  editingName.value = false;
  const v = name.value.trim();
  if (v && v !== model.value?.name) await patch({ name: v });
  else name.value = model.value?.name ?? "";
}
async function saveDescription() {
  if (description.value !== model.value?.description) await patch({ description: description.value }, "Description enregistrée.");
}
async function addTag() {
  const t = newTag.value.trim().toLowerCase();
  newTag.value = "";
  if (!t || model.value?.tags.includes(t)) return;
  await patch({ tags: [...(model.value?.tags ?? []), t] });
}
const removeTag = (t: string) => patch({ tags: (model.value?.tags ?? []).filter((x) => x !== t) });

/** Change from the previous print, e.g. "+12 %" (blank when stable). */
function trend(v: number, before: number): string {
  if (!before) return "";
  const pct = Math.round(((v - before) / before) * 100);
  return Math.abs(pct) < 3 ? "=" : `${pct > 0 ? "+" : "−"}${Math.abs(pct)} %`;
}

function num(v: string): number | null {
  const n = Number(v.replace(",", "."));
  return v.trim() === "" || Number.isNaN(n) ? null : n;
}
async function saveAlert(raw: string) {
  const v = num(raw);
  await patch(v === null ? { clear_stock_alert: true } : { stock_alert: Math.round(v) }, "Seuil enregistré.");
  loadProduct().catch(() => undefined);
}
async function savePieces(id: number, raw: string) {
  const v = Math.round(num(raw) ?? 1);
  if (v < 1) return;
  await attempt(() => api.patch(`/sliced/${id}`, { pieces: v }), "Pièces par impression enregistrées.");
  load();
}

async function onFiles(e: Event) {
  const input = e.target as HTMLInputElement;
  await uploadFiles(Array.from(input.files ?? []), Number(props.id));
  input.value = "";
}

async function deleteSliced(id: number, filename: string) {
  if (!window.confirm(`Supprimer « ${filename} » ?`)) return;
  if ((await attempt(() => api.del(`/sliced/${id}`))) !== undefined) {
    toast("Fichier supprimé.");
    load();
  }
}
async function deleteSource(id: number, filename: string) {
  if (!window.confirm(`Supprimer « ${filename} » ?`)) return;
  if ((await attempt(() => api.del(`/sources/${id}`))) !== undefined) load();
}
async function deleteModel() {
  if (!window.confirm(`Supprimer « ${model.value?.name} » et tous ses fichiers ? Cette action est définitive.`)) return;
  if ((await attempt(() => api.del(`/models/${props.id}`))) !== undefined) {
    toast("Modèle supprimé.");
    router.push("/bibliotheque");
  }
}

function schedule(slicedId?: number) {
  router.push({ path: "/", query: { planifier: props.id, version: slicedId ? String(slicedId) : undefined } });
}
</script>

<template>
  <div class="page">
    <RouterLink to="/bibliotheque" class="back"><ArrowLeft aria-hidden="true" />Bibliothèque</RouterLink>

    <div v-if="missing" class="empty empty--error">
      <h2>Modèle introuvable</h2>
      <p>Il a peut-être été supprimé.</p>
      <RouterLink to="/bibliotheque" class="btn btn--secondary">Retour à la bibliothèque</RouterLink>
    </div>

    <div v-else-if="!model" class="layout" aria-busy="true">
      <div class="skeleton" style="aspect-ratio: 1" />
      <div class="skeleton" style="height: 240px" />
    </div>

    <template v-else>
      <div class="layout">
        <div class="coverwrap">
          <figure class="cover card">
            <ModelPreview3D
              v-if="view === '3d' && previewFile"
              :url="urls.downloadSource(previewFile.id)"
              :filename="previewFile.filename"
              :color="cover?.color"
            />
            <ModelThumb v-else :sliced-id="cover?.has_thumbnail ? cover.id : null" size="lg" :alt="`Aperçu de ${model.name}`" />
          </figure>
          <div v-if="previewable.length" class="segmented" role="group" aria-label="Affichage">
            <button type="button" :aria-pressed="view === 'thumb'" @click="view = 'thumb'">Miniature</button>
            <button type="button" :aria-pressed="view === '3d'" @click="view = '3d'">Aperçu 3D</button>
          </div>
        </div>

        <div class="info">
          <div class="title">
            <input
              v-if="editingName"
              ref="nameInput"
              v-model="name"
              class="input title__input"
              aria-label="Nom du modèle"
              @blur="saveName"
              @keydown.enter.prevent="saveName"
              @keydown.esc="name = model.name; editingName = false"
            />
            <h1 v-else>
              <button type="button" class="title__btn" title="Renommer" @click="startRename">{{ model.name }}</button>
            </h1>
          </div>

          <dl class="facts">
            <div>
              <dt>Durée</dt>
              <dd class="num">{{ duration(cover?.duration_s ?? model.duration_s) }}</dd>
            </div>
            <div v-if="cover?.filament_g ?? model.filament_g">
              <dt>Filament</dt>
              <dd class="num">{{ grams(cover?.filament_g ?? model.filament_g) }}</dd>
            </div>
            <div v-if="cover?.material">
              <dt>Matière</dt>
              <dd>
                <span v-if="cover.color" class="swatch" :style="{ background: cover.color }" />
                {{ cover.material }}
              </dd>
            </div>
            <div v-if="cover?.layer_height">
              <dt>Couche</dt>
              <dd class="num">{{ cover.layer_height }} mm</dd>
            </div>
          </dl>

          <div class="cta-row">
            <button type="button" class="btn btn--primary" :disabled="!model.sliced.length" @click="schedule(selected ?? undefined)">
              <CalendarPlus aria-hidden="true" />Planifier
            </button>
            <button type="button" class="btn btn--secondary" :disabled="uploading" @click="fileInput?.click()">
              <Plus aria-hidden="true" />{{ uploading ? `Import… ${Math.round(uploadProgress * 100)} %` : "Ajouter un fichier" }}
            </button>
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".gcode,.gco,.g,.3mf,.stl,.obj,.step,.stp,.blend"
              class="visually-hidden"
              tabindex="-1"
              @change="onFiles"
            />
          </div>

          <div class="field">
            <span class="field__label" id="tags-label">Étiquettes</span>
            <div class="tagrow" aria-labelledby="tags-label">
              <span v-for="t in model.tags" :key="t" class="tag">
                {{ t }}
                <button type="button" class="tag__x" :aria-label="`Retirer ${t}`" @click="removeTag(t)"><X :size="14" aria-hidden="true" /></button>
              </span>
              <form class="tagform" @submit.prevent="addTag">
                <input v-model="newTag" class="input tagform__input" placeholder="Ajouter une étiquette" aria-label="Nouvelle étiquette" />
              </form>
            </div>
          </div>

          <div class="field">
            <label for="desc">Notes</label>
            <textarea
              id="desc"
              v-model="description"
              class="textarea"
              placeholder="Couleurs vendues, assemblage, prix…"
              @blur="saveDescription"
            />
          </div>
        </div>
      </div>

      <section class="section">
        <div class="section-head">
          <h2>Fichiers tranchés</h2>
          <span class="muted small">Un fichier par imprimante ou réglage</span>
        </div>
        <p v-if="!model.sliced.length" class="muted">Aucun fichier tranché. Ajoute un .gcode pour pouvoir planifier ce modèle.</p>
        <div v-else class="table-wrap">
          <table class="spec">
            <thead>
              <tr>
                <th scope="col">Fichier</th>
                <th scope="col">Tranché pour</th>
                <th scope="col">Matière</th>
                <th scope="col">Durée</th>
                <th scope="col">Filament</th>
                <th scope="col">Pièces</th>
                <th scope="col"><span class="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="s in model.sliced"
                :key="s.id"
                :class="{ 'is-selected': s.id === selected }"
                @click="selected = s.id"
              >
                <td>
                  <span class="filename">{{ s.filename }}</span>
                  <span class="muted small mono">{{ bytes(s.size) }}</span>
                </td>
                <td>{{ s.printer_model ?? "–" }}</td>
                <td>
                  <span v-if="s.color" class="swatch" :style="{ background: s.color }" />
                  {{ s.material ?? "–" }}<template v-if="s.layer_height">, <span class="mono">{{ s.layer_height }} mm</span></template>
                </td>
                <td class="mono">{{ duration(s.duration_s) }}</td>
                <td class="mono">{{ grams(s.filament_g) || "–" }}</td>
                <td>
                  <input
                    class="input mono pieces"
                    type="number"
                    min="1"
                    inputmode="numeric"
                    :value="s.pieces"
                    :aria-label="`Pièces par impression pour ${s.filename}`"
                    @click.stop
                    @change="savePieces(s.id, ($event.target as HTMLInputElement).value)"
                  />
                </td>
                <td class="actions">
                  <button type="button" class="btn btn--ghost btn--sm" @click.stop="schedule(s.id)">Planifier</button>
                  <a :href="urls.downloadSliced(s.id)" class="btn btn--ghost btn--sm btn--icon" :aria-label="`Télécharger ${s.filename}`" @click.stop>
                    <Download aria-hidden="true" />
                  </a>
                  <button type="button" class="btn btn--ghost btn--sm btn--icon" :aria-label="`Supprimer ${s.filename}`" @click.stop="deleteSliced(s.id, s.filename)">
                    <Trash2 aria-hidden="true" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>Production</h2>
          <RouterLink to="/stock" class="small">Voir tout le stock</RouterLink>
        </div>
        <div class="production">
          <div class="production__stock">
            <span class="field__label">En stock</span>
            <p class="production__total">
              <span class="production__count">{{ product?.stock_total ?? 0 }}</span>
              <span v-if="product?.stock_low" class="status status--attention">Stock bas</span>
            </p>
            <ul v-if="product?.stock.length" class="production__variants" role="list">
              <li v-for="v in product.stock" :key="v.variant">
                <span class="swatch" :style="{ background: v.color ?? 'transparent' }" />
                {{ v.variant }} <span class="mono">{{ v.quantity }}</span>
              </li>
            </ul>
            <p v-else class="small muted">Les pièces arrivent ici à la fin de chaque impression réussie.</p>
          </div>


          <div class="production__fields">
            <div class="production__energy">
              <span class="field__label">Électricité par pièce</span>
              <p class="production__big">{{ kwh(product?.energy.kwh_per_piece) }}</p>
              <p class="small muted">
                {{ product?.energy.measured_on
                  ? `moyenne des ${product.energy.measured_on} dernières impressions mesurées par la prise`
                  : "mesurée par la prise de l’imprimante dès la prochaine impression" }}
              </p>
              <ol v-if="(product?.energy.history.length ?? 0) > 1" class="energy-history small" aria-label="Dernières impressions">
                <li v-for="(h, i) in product!.energy.history" :key="h.at">
                  <span class="muted">{{ when(h.at) }}</span>
                  <span class="num">{{ kwh(h.kwh_per_piece) }}</span>
                  <span v-if="i < product!.energy.history.length - 1" class="muted">
                    {{ trend(h.kwh_per_piece, product!.energy.history[i + 1].kwh_per_piece) }}
                  </span>
                </li>
              </ol>
            </div>
            <div class="field">
              <label for="m-alert">Alerte si stock sous</label>
              <input id="m-alert" class="input mono" inputmode="numeric" :value="model.stock_alert ?? ''" placeholder="–"
                     @change="saveAlert(($event.target as HTMLInputElement).value)" />
            </div>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>Sources</h2>
          <button type="button" class="btn btn--ghost btn--sm" :disabled="uploading" @click="sourceInput?.click()">
            <Plus aria-hidden="true" />Ajouter une source
          </button>
          <input
            ref="sourceInput"
            type="file"
            multiple
            accept=".stl,.3mf,.obj,.step,.stp,.blend"
            class="visually-hidden"
            tabindex="-1"
            @change="onFiles"
          />
        </div>
        <p v-if="!model.sources.length" class="muted small">Aucune source. Garde ici le STL, le projet 3MF ou le .blend pour pouvoir modifier ou retrancher plus tard ; tu peux aussi glisser le fichier sur cette page.</p>
        <ul v-else class="sources" role="list">
          <li v-for="f in model.sources" :key="f.id">
            <span class="filename">{{ f.filename }}</span>
            <span class="mono small muted">{{ bytes(f.size) }}</span>
            <button
              v-if="/\.(stl|3mf)$/i.test(f.filename)"
              type="button"
              class="btn btn--ghost btn--sm btn--icon"
              :aria-label="`Voir ${f.filename} en 3D`"
              @click="show3d(f.id)"
            >
              <Box aria-hidden="true" />
            </button>
            <span v-else />
            <a :href="urls.downloadSource(f.id)" class="btn btn--ghost btn--sm btn--icon" :aria-label="`Télécharger ${f.filename}`"><Download aria-hidden="true" /></a>
            <button type="button" class="btn btn--ghost btn--sm btn--icon" :aria-label="`Supprimer ${f.filename}`" @click="deleteSource(f.id, f.filename)"><Trash2 aria-hidden="true" /></button>
          </li>
        </ul>
      </section>

      <section class="section danger">
        <button type="button" class="btn btn--danger btn--sm" @click="deleteModel">Supprimer ce modèle</button>
      </section>
    </template>
  </div>
</template>

<style scoped>
.back { display: inline-flex; align-items: center; gap: var(--space-2xs); min-height: 44px; margin-bottom: var(--space-sm); color: var(--color-muted); text-decoration: none; font-size: var(--text-sm); }
.back:hover { color: var(--color-ink); }
.back svg { width: 16px; height: 16px; }

.layout { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: var(--space-xl); align-items: start; }
@media (max-width: 60rem) { .layout { grid-template-columns: minmax(0, 1fr); } }
.coverwrap { display: grid; gap: var(--space-md); justify-items: start; }
.cover { margin: 0; width: 100%; }

.info { display: grid; gap: var(--space-lg); min-width: 0; }
.title h1 { font-size: var(--text-3xl); max-width: none; }
.title__btn { padding: 0; border: 0; background: none; color: inherit; font: inherit; letter-spacing: inherit; text-align: left; cursor: text; text-decoration: underline transparent; text-underline-offset: 0.15em; transition: text-decoration-color var(--dur-micro) var(--ease-out); }
.title__btn:hover { text-decoration-color: var(--color-rule); }
.title__btn:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.title__input { font-family: var(--font-display); font-weight: 700; font-size: var(--text-2xl); min-height: 52px; letter-spacing: var(--tracking-display); }

.facts { display: flex; flex-wrap: wrap; gap: var(--space-md) var(--space-xl); margin: 0; padding: var(--space-md) 0; border-block: var(--rule-hair) solid var(--color-rule); }
.facts div { display: grid; gap: var(--space-3xs); }
.facts dt { font-size: var(--text-sm); color: var(--color-muted); }
.facts dd { margin: 0; font-size: var(--text-lg); font-weight: 500; display: flex; align-items: center; gap: var(--space-xs); }

.cta-row { display: flex; flex-wrap: wrap; gap: var(--space-xs); }

.tagrow { display: flex; flex-wrap: wrap; gap: var(--space-2xs); align-items: center; }
.tag__x { display: grid; place-items: center; width: 22px; height: 22px; margin-right: -4px; padding: 0; border: 0; background: none; color: var(--color-muted); cursor: pointer; }
.tag__x:hover { color: var(--color-ink); background: var(--color-paper-2); }
.tag__x:focus-visible { outline: 2px solid var(--color-focus); }
.tagform__input { min-height: 32px; width: 180px; font-size: var(--text-sm); }

.table-wrap { overflow-x: auto; }
.spec tbody tr { cursor: pointer; }
.spec tr.is-selected { background: var(--color-accent-soft); }
.spec tr.is-selected .filename { color: var(--color-accent); }
.filename { display: block; font-weight: 500; overflow-wrap: anywhere; }

.sources { display: grid; margin: 0; padding: 0; list-style: none; border-top: var(--rule-bar) solid var(--color-ink); }
.sources li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto auto; align-items: center; gap: var(--space-sm); padding: var(--space-xs) var(--space-sm); border-bottom: var(--rule-hair) solid var(--color-rule); font-size: var(--text-sm); }

.pieces { width: 72px; min-height: var(--control-h-sm); }
.production { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-lg) var(--space-xl); }
@media (max-width: 60rem) { .production { grid-template-columns: minmax(0, 1fr); } }
.production__stock { display: grid; gap: var(--space-xs); align-content: start; }
.production__total { display: flex; align-items: center; gap: var(--space-sm); }
.production__count { font-weight: 500; font-size: var(--text-3xl); line-height: 1; font-variant-numeric: tabular-nums; }
.production__variants { display: grid; gap: var(--space-2xs); margin: 0; padding: 0; list-style: none; }
.production__variants li { display: flex; align-items: center; gap: var(--space-xs); }
.production__energy { display: grid; gap: var(--space-3xs); }
.energy-history { display: grid; gap: var(--space-3xs); margin: var(--space-2xs) 0 0; padding: 0; list-style: none; }
.energy-history li { display: grid; grid-template-columns: minmax(0, 1fr) auto 4.5em; gap: var(--space-xs); }
.energy-history li > :last-child { text-align: right; }
.production__big { font-size: var(--text-xl); font-weight: 500; font-variant-numeric: tabular-nums; }
.production__fields { display: grid; gap: var(--space-md); align-content: start; }
.production__big { margin: 0; }
.danger { padding-top: var(--space-lg); border-top: var(--rule-hair) solid var(--color-rule); }

@media (max-width: 40rem) {
  .spec thead { display: none; }
  .spec tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; padding: var(--space-xs) 0; border-bottom: var(--rule-hair) solid var(--color-rule); }
  .spec td { border: 0; padding: 2px var(--space-xs); }
  .spec td { grid-column: 1 / -1; }
  .spec td:first-child { grid-column: 1; }
  .spec td.actions { grid-row: 1; grid-column: 2; }
}
</style>
