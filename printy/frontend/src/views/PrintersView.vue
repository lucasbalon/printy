<script setup lang="ts">
import { Camera, Plus, RefreshCw, Settings2 } from "lucide-vue-next";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, urls } from "../api";
import PrinterSheet from "../components/PrinterSheet.vue";
import SpoolPicker from "../components/SpoolPicker.vue";
import StatusTag from "../components/StatusTag.vue";
import { duration, temp } from "../format";
import { attempt, loadPrinters, loadSpools, state, toast, usePolling } from "../store";
import type { Printer } from "../types";

const route = useRoute();
const router = useRouter();
const failed = ref(false);

usePolling(async () => {
  try {
    await Promise.all([loadPrinters(), loadSpools().catch(() => undefined)]);
    failed.value = false;
  } catch {
    failed.value = true;
  }
}, 4000);

// camera snapshots refresh on their own rhythm
const snapKey = ref(Date.now());
const snapFailed = ref<Record<number, boolean>>({});
const snapTimer = window.setInterval(() => (snapKey.value = Date.now()), 15000);
onBeforeUnmount(() => window.clearInterval(snapTimer));
function snapUrl(p: Printer) {
  void snapKey.value;
  return urls.printerSnapshot(p.id);
}
function refreshSnap(p: Printer) {
  snapFailed.value[p.id] = false;
  snapKey.value = Date.now();
}

const editing = ref<Printer | null>(null);
const sheetOpen = ref(false);
function openSheet(p: Printer | null) {
  editing.value = p;
  sheetOpen.value = true;
}
onMounted(() => {
  if (route.query.ajouter) {
    openSheet(null);
    router.replace({ query: {} });
  }
});
watch(sheetOpen, (o) => !o && (editing.value = null));

async function onSaved(p: Printer) {
  sheetOpen.value = false;
  toast(editing.value ? "Imprimante enregistrée." : `${p.name} ajoutée.`);
  await attempt(loadPrinters);
}
async function onDeleted() {
  sheetOpen.value = false;
  toast("Imprimante supprimée.");
  await attempt(loadPrinters);
}

const powering = ref<number | null>(null);
async function power(p: Printer, on: boolean) {
  powering.value = p.id;
  await attempt(() => api.post(`/printers/${p.id}/power`, { on }), on ? "Prise allumée." : "Prise coupée.");
  powering.value = null;
  attempt(loadPrinters);
}
async function setLoaded(p: Printer, id: number | null) {
  await attempt(() => api.post(`/printers/${p.id}/spool`, { spool_id: id }), id ? "Bobine chargée mise à jour." : "Bobine retirée.");
  attempt(loadPrinters);
}
async function bedClear(p: Printer) {
  await attempt(() => api.post(`/printers/${p.id}/bed-clear`), "Plateau marqué comme vidé.");
  attempt(loadPrinters);
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="page-head__title">
        <h1>Imprimantes</h1>
      </div>
      <div class="page-head__actions">
        <button type="button" class="btn btn--primary" @click="openSheet(null)">
          <Plus aria-hidden="true" />Ajouter une imprimante
        </button>
      </div>
    </header>

    <div v-if="failed && !state.printersLoaded" class="empty empty--error">
      <h2>Imprimantes indisponibles</h2>
      <p>Printy ne répond pas. Vérifie que l’add-on tourne, puis réessaie.</p>
      <button type="button" class="btn btn--secondary" @click="attempt(loadPrinters)">Réessayer</button>
    </div>

    <div v-else-if="!state.printersLoaded" class="list" aria-busy="true">
      <div v-for="i in 2" :key="i" class="card printer"><div class="skeleton" style="aspect-ratio: 4/3" /><div class="skeleton" style="height: 160px" /></div>
    </div>

    <div v-else-if="!state.printers.length" class="empty">
      <h2>Aucune imprimante</h2>
      <p>Ajoute ta Creality K2 avec son adresse IP. Associe sa prise connectée pour qu’elle s’allume et s’éteigne toute seule autour des impressions.</p>
      <button type="button" class="btn btn--primary" @click="openSheet(null)">Ajouter une imprimante</button>
    </div>

    <ul v-else class="list" role="list">
      <li v-for="p in state.printers" :key="p.id" class="card printer">
        <div class="cam">
          <img
            v-if="p.camera_source !== 'none' && p.live.state !== 'offline' && !snapFailed[p.id]"
            :src="snapUrl(p)"
            :alt="`Caméra de ${p.name}`"
            width="640"
            height="480"
            @error="snapFailed[p.id] = true"
          />
          <div v-else class="cam__empty">
            <Camera aria-hidden="true" />
            <span>{{ p.camera_source === "none" ? "Pas de caméra" : p.live.state === "offline" ? "Imprimante éteinte" : "Image indisponible" }}</span>
          </div>
          <button
            v-if="p.camera_source !== 'none'"
            type="button"
            class="btn btn--sm btn--icon cam__refresh"
            aria-label="Actualiser l’image"
            @click="refreshSnap(p)"
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </div>

        <div class="printer__body">
          <header class="printer__head">
            <div>
              <h2>{{ p.name }}</h2>
              <p class="muted small">{{ p.driver_label }}<template v-if="p.host">, <span class="mono">{{ p.host }}</span></template></p>
            </div>
            <button type="button" class="btn btn--ghost btn--icon" :aria-label="`Modifier ${p.name}`" @click="openSheet(p)">
              <Settings2 aria-hidden="true" />
            </button>
          </header>

          <div class="printer__state">
            <StatusTag :printer="p.live.state" />
            <span v-if="p.live.message" class="small muted">{{ p.live.message }}</span>
          </div>

          <div v-if="p.live.state === 'printing' || p.live.state === 'paused'" class="printing">
            <p class="small"><span class="filename">{{ p.live.file }}</span></p>
            <div class="progress" role="progressbar" :aria-valuenow="Math.round((p.live.progress ?? 0) * 100)" aria-valuemin="0" aria-valuemax="100">
              <div class="progress__bar" :style="{ transform: `scaleX(${p.live.progress ?? 0})` }" />
            </div>
            <p class="small">
              <span class="mono">{{ Math.round((p.live.progress ?? 0) * 100) }} %</span>
              <template v-if="p.live.remaining_s != null">, encore <span class="mono">{{ duration(p.live.remaining_s) }}</span></template>
            </p>
          </div>

          <dl class="readings">
            <div><dt>Buse</dt><dd class="num">{{ temp(p.live.nozzle_temp) }}</dd></div>
            <div><dt>Plateau</dt><dd class="num">{{ temp(p.live.bed_temp) }}</dd></div>
            <div v-if="p.power_entity"><dt>Puissance</dt><dd class="num">{{ p.live.power_w != null ? `${Math.round(p.live.power_w)} W` : "–" }}</dd></div>
          </dl>

          <div v-if="state.spools.enabled" class="field">
            <span class="field__label">Bobine chargée</span>
            <SpoolPicker
              :model-value="p.loaded_spool_id"
              :spools="state.spools.spools"
              label="Bobine chargée"
              none-label="Bobine inconnue"
              @change="(id) => setLoaded(p, id)"
            />
            <span class="field__help">
              {{ p.live.spool_tracked ? "Synchronisée avec Klipper : la consommation est déduite par l’imprimante." : "" }}
              <RouterLink to="/bobines">Gérer les bobines</RouterLink>
            </span>
          </div>

          <div class="printer__rows">
            <div v-if="p.switch_entity" class="row">
              <label class="switch">
                <input
                  type="checkbox"
                  :checked="!!p.live.power_on"
                  :disabled="powering === p.id || p.live.power_on === null"
                  @change="power(p, ($event.target as HTMLInputElement).checked)"
                />
                <span class="switch__track" aria-hidden="true" />
                <span class="switch__text">
                  <span>Prise {{ p.live.power_on ? "allumée" : p.live.power_on === false ? "coupée" : "injoignable" }}</span>
                  <small class="mono">{{ p.switch_entity }}</small>
                </span>
              </label>
            </div>
            <p v-else class="small muted">Pas de prise associée : allumage manuel.</p>

            <div class="row bed">
              <span v-if="p.bed_clear" class="status status--ok">Plateau libéré</span>
              <template v-else-if="p.live.state === 'finished'">
                <span class="status status--warn">Pièce sur le plateau</span>
                <button type="button" class="btn btn--secondary btn--sm" @click="bedClear(p)">J’ai vidé le plateau</button>
              </template>
            </div>
          </div>
        </div>
      </li>
    </ul>

    <PrinterSheet :open="sheetOpen" :printer="editing" @close="sheetOpen = false" @saved="onSaved" @deleted="onDeleted" />
  </div>
</template>

<style scoped>
.list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(560px, 100%), 1fr)); gap: var(--space-xl); margin: 0; padding: 0 var(--space-xs) var(--space-xs) 0; list-style: none; }
.printer { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); }
@media (max-width: 40rem) { .printer { grid-template-columns: minmax(0, 1fr); } }

.cam { position: relative; min-height: 260px; background: var(--color-media); overflow: hidden; }
.cam img { position: absolute; inset: 0; }
@media (max-width: 40rem) { .cam { min-height: 0; aspect-ratio: 4 / 3; } }
.cam img { width: 100%; height: 100%; object-fit: cover; }
.cam__empty { display: grid; place-content: center; justify-items: center; gap: var(--space-xs); height: 100%; color: var(--color-media-ink); font-size: var(--text-sm); text-align: center; padding: var(--space-md); }
.cam__empty svg { width: 28px; height: 28px; }
.cam__refresh { position: absolute; right: var(--space-xs); bottom: var(--space-xs); background: var(--color-plate); color: var(--color-ink); }
.cam__refresh:hover { background: var(--color-paper-2); }

.printer__body { display: grid; align-content: start; gap: var(--space-md); padding: var(--space-md) var(--space-md) var(--space-lg); min-width: 0; }
.printer__head { display: flex; justify-content: space-between; gap: var(--space-sm); align-items: start; }
.printer__head h2 { font-size: var(--text-xl); }
.printer__state { display: flex; flex-wrap: wrap; gap: var(--space-xs); align-items: center; }
.printing { display: grid; gap: var(--space-2xs); }
.filename { overflow-wrap: anywhere; }

.readings { display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-lg); margin: 0; padding: var(--space-sm) 0; border-block: var(--rule-hair) solid var(--color-rule); }
.readings div { display: grid; gap: var(--space-3xs); }
.readings dt { font-size: var(--text-sm); color: var(--color-muted); }
.readings dd { margin: 0; font-size: var(--text-xl); font-weight: 500; }

.printer__rows { display: grid; gap: var(--space-sm); }
.bed { display: flex; flex-wrap: wrap; gap: var(--space-xs); align-items: center; }
.bed:empty { display: none; }
</style>
