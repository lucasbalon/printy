<script setup lang="ts">
import { AlertTriangle, ChevronRight, Copy, Plus, X } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api";
import { attempt, loadEntities, loadSettings, loadSpools, state, toast } from "../store";
import { applyTheme, theme } from "../theme";
import type { OffPeakRange, Settings } from "../types";

const failed = ref(false);
const saving = ref(false);
const directPort = 5300;

onMounted(async () => {
  loadEntities();
  try {
    await loadSettings();
    syncRanges();
  } catch {
    failed.value = true;
  }
});

const s = computed(() => state.settings);

/** Time pickers fire a change on every notch: wait until the user stops. */
let pending: Partial<Settings> = {};
let pendingTimer: number | undefined;
function saveSoon(patch: Partial<Settings>) {
  pending = { ...pending, ...patch };
  window.clearTimeout(pendingTimer);
  pendingTimer = window.setTimeout(() => {
    const p = pending;
    pending = {};
    save(p);
  }, 900);
}

onBeforeUnmount(() => {
  // leaving the page before the delay: save right away
  window.clearTimeout(pendingTimer);
  if (Object.keys(pending).length) save(pending);
});

async function save(patch: Partial<Settings>, message = "Enregistré.") {
  saving.value = true;
  const res = await attempt(() => api.patch<Settings>("/settings", patch), message);
  saving.value = false;
  if (res) state.settings = res;
}

// notifications
const notifyOptions = computed(() => state.entities?.notify ?? []);
function toggleTarget(id: string, on: boolean) {
  const current = new Set(s.value?.notify_targets ?? []);
  if (on) current.add(id);
  else current.delete(id);
  save({ notify_targets: [...current] });
}
const manualTarget = ref("");
function addManualTarget() {
  const v = manualTarget.value.trim();
  manualTarget.value = "";
  if (!v) return;
  const id = v.startsWith("notify.") ? v : `notify.${v}`;
  toggleTarget(id, true);
}
const testing = ref(false);
async function testNotification() {
  testing.value = true;
  await attempt(() => api.post("/settings/test-notification"), "Notification envoyée.");
  testing.value = false;
}

// off-peak ranges
const ranges = ref<OffPeakRange[]>([]);
function syncRanges() {
  ranges.value = (s.value?.off_peak ?? []).map((r) => ({ ...r }));
}
function saveRanges() {
  if (ranges.value.some((r) => !r.start || !r.end)) return;
  saveSoon({ off_peak: ranges.value.map((r) => ({ ...r })) });
}
function addRange() {
  ranges.value.push({ start: "12:00", end: "14:00" });
  saveRanges();
}
function removeRange(i: number) {
  ranges.value.splice(i, 1);
  saveRanges();
}

// Spoolman
const spoolmanUrl = ref("");
const testingSpoolman = ref(false);
const spoolmanStatus = ref<{ text: string; error: boolean }>({ text: "", error: false });
watch(
  () => s.value?.spoolman_url,
  (v) => {
    spoolmanUrl.value = v ?? "";
    if (v && !spoolmanStatus.value.text) spoolmanStatus.value = { text: "Connecté.", error: false };
  },
  { immediate: true },
);
async function saveSpoolman() {
  const url = spoolmanUrl.value;
  if (!url) {
    await save({ spoolman_url: "" }, "Spoolman désactivé.");
    spoolmanStatus.value = { text: "", error: false };
    return;
  }
  testingSpoolman.value = true;
  try {
    const r = await api.post<{ version: string; spools: number }>("/spoolman/test", { url });
    await save({ spoolman_url: url }, "Spoolman connecté.");
    await loadSpools(true).catch(() => undefined);
    spoolmanStatus.value = { text: `Spoolman ${r.version}, ${r.spools} bobine${r.spools > 1 ? "s" : ""} active${r.spools > 1 ? "s" : ""}.`, error: false };
  } catch (e) {
    spoolmanStatus.value = { text: (e as Error).message, error: true };
  } finally {
    testingSpoolman.value = false;
  }
}

const morningTime = ref("08:00");
watch(
  () => s.value?.spool_reminder_morning,
  (v) => {
    if (v) morningTime.value = v;
  },
  { immediate: true },
);

// slicer upload
const orcaUrl = computed(() => `http://${window.location.hostname}:${directPort}`);
async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copiée.`);
  } catch {
    toast("Copie impossible : sélectionne le texte à la main.", "error");
  }
}
async function rotateKey() {
  if (!window.confirm("Générer une nouvelle clé ? Il faudra la recopier dans Orca Slicer.")) return;
  const res = await attempt(() => api.post<Settings>("/settings/octoprint-key"), "Nouvelle clé générée.");
  if (res) state.settings = res;
}
</script>

<template>
  <div class="page settings">
    <header class="page-head">
      <div class="page-head__title">
        <h1>Réglages</h1>
        <p class="muted small" aria-live="polite">{{ saving ? "Enregistrement…" : "Chaque modification est enregistrée aussitôt." }}</p>
      </div>
    </header>

    <div v-if="failed" class="empty empty--error">
      <h2>Réglages indisponibles</h2>
      <p>Printy ne répond pas. Vérifie que l’add-on tourne, puis recharge la page.</p>
    </div>

    <div v-else-if="!s" class="stack" aria-busy="true">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 160px" />
    </div>

    <div v-else class="stack">
      <!-- Notifications -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Notifications</h2>
          <p class="muted small">Les téléphones qui reçoivent la photo du plateau avant chaque impression.</p>
        </div>
        <div class="group__body">
          <p v-if="!s.env.home_assistant" class="notice">
            <AlertTriangle aria-hidden="true" />
            <span>Mode développement : Home Assistant n’est pas joignable, les notifications sont seulement journalisées.</span>
          </p>
          <div v-if="notifyOptions.length" class="checks">
            <label v-for="n in notifyOptions" :key="n.id" class="switch">
              <input type="checkbox" :checked="s.notify_targets.includes(n.id)" @change="toggleTarget(n.id, ($event.target as HTMLInputElement).checked)" />
              <span class="switch__track" aria-hidden="true" />
              <span class="switch__text">
                <span>{{ n.name }}</span>
                <small class="mono">{{ n.id }}</small>
              </span>
            </label>
          </div>
          <form v-else class="inline" @submit.prevent="addManualTarget">
            <label for="notify-manual" class="visually-hidden">Service de notification</label>
            <input id="notify-manual" v-model="manualTarget" class="input mono" placeholder="notify.mobile_app_telephone" autocomplete="off" />
            <button type="submit" class="btn btn--secondary">Ajouter</button>
          </form>
          <div v-if="s.notify_targets.some((t) => !notifyOptions.some((n) => n.id === t))" class="tagrow">
            <span v-for="t in s.notify_targets.filter((t) => !notifyOptions.some((n) => n.id === t))" :key="t" class="tag mono">
              {{ t }}
              <button type="button" class="tag__x" :aria-label="`Retirer ${t}`" @click="toggleTarget(t, false)"><X :size="14" aria-hidden="true" /></button>
            </span>
          </div>
          <div>
            <button type="button" class="btn btn--secondary btn--sm" :disabled="!s.notify_targets.length || testing" @click="testNotification">
              Envoyer une notification de test
            </button>
          </div>
        </div>
      </section>

      <!-- Safety -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Avant chaque impression</h2>
          <p class="muted small">Réglage par défaut des nouvelles impressions ; modifiable une par une.</p>
        </div>
        <div class="group__body">
          <label class="switch">
            <input type="checkbox" :checked="s.confirm_by_default" @change="save({ confirm_by_default: ($event.target as HTMLInputElement).checked })" />
            <span class="switch__track" aria-hidden="true" />
            <span class="switch__text">
              <span>Demander de confirmer que le plateau est vide</span>
              <small>Une photo est envoyée ; l’impression ne démarre qu’après « Lancer ». Si tu touches « Plateau vidé » à la fin d’une impression, la suivante part sans redemander.</small>
            </span>
          </label>
          <p v-if="!s.confirm_by_default" class="notice">
            <AlertTriangle aria-hidden="true" />
            <span>Les impressions démarreront sans vérification. Une pièce oubliée sur le plateau peut abîmer la buse ou le plateau.</span>
          </p>
        </div>
      </section>

      <!-- Off-peak -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Heures creuses</h2>
          <p class="muted small">Affichées dans le planning et proposées pour les longues impressions.</p>
        </div>
        <div class="group__body">
          <ul class="ranges" role="list">
            <li v-for="(r, i) in ranges" :key="i">
              <label :for="`hc-s-${i}`" class="visually-hidden">Début</label>
              <input :id="`hc-s-${i}`" v-model="r.start" type="time" class="input mono" @change="saveRanges" />
              <span class="muted">à</span>
              <label :for="`hc-e-${i}`" class="visually-hidden">Fin</label>
              <input :id="`hc-e-${i}`" v-model="r.end" type="time" class="input mono" @change="saveRanges" />
              <button type="button" class="btn btn--ghost btn--icon" aria-label="Retirer cette plage" @click="removeRange(i)"><X aria-hidden="true" /></button>
            </li>
          </ul>
          <div><button type="button" class="btn btn--ghost btn--sm" @click="addRange"><Plus aria-hidden="true" />Ajouter une plage</button></div>
        </div>
      </section>

      <!-- Import -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Import des fichiers</h2>
          <p class="muted small">Le glisser-déposer marche toujours. Active les autres voies selon ton usage.</p>
        </div>
        <div class="group__body">
          <label class="switch">
            <input type="checkbox" :checked="s.import_octoprint" @change="save({ import_octoprint: ($event.target as HTMLInputElement).checked })" />
            <span class="switch__track" aria-hidden="true" />
            <span class="switch__text">
              <span>Envoi depuis Orca Slicer</span>
              <small>Dans Orca : imprimante physique, type OctoPrint. Le fichier arrive dans la bibliothèque, pas sur l’imprimante.</small>
            </span>
          </label>
          <dl v-if="s.import_octoprint" class="creds">
            <div>
              <dt>Adresse</dt>
              <dd>
                <code class="mono">{{ orcaUrl }}</code>
                <button type="button" class="btn btn--ghost btn--sm btn--icon" aria-label="Copier l’adresse" @click="copy(orcaUrl, 'Adresse')"><Copy aria-hidden="true" /></button>
              </dd>
            </div>
            <div>
              <dt>Clé API</dt>
              <dd>
                <code class="mono">{{ s.octoprint_api_key }}</code>
                <button type="button" class="btn btn--ghost btn--sm btn--icon" aria-label="Copier la clé" @click="copy(s.octoprint_api_key, 'Clé')"><Copy aria-hidden="true" /></button>
                <button type="button" class="btn btn--ghost btn--sm" @click="rotateKey">Régénérer</button>
              </dd>
            </div>
          </dl>

          <label class="switch">
            <input type="checkbox" :checked="s.import_watch_folder" @change="save({ import_watch_folder: ($event.target as HTMLInputElement).checked })" />
            <span class="switch__track" aria-hidden="true" />
            <span class="switch__text">
              <span>Dossier surveillé</span>
              <small>Tout fichier copié dans <span class="mono">{{ s.env.inbox_dir }}</span> (partage Samba « share ») est importé puis retiré du dossier.</small>
            </span>
          </label>
        </div>
      </section>

      <!-- Spoolman -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Spoolman</h2>
          <p class="muted small">Associe une bobine à chaque impression, déduit le filament consommé et prévient quand il faut changer de bobine.</p>
        </div>
        <div class="group__body">
          <form class="field" @submit.prevent="saveSpoolman">
            <label for="spoolman-url">Adresse de Spoolman</label>
            <div class="inline">
              <input
                id="spoolman-url"
                v-model.trim="spoolmanUrl"
                class="input mono"
                type="url"
                inputmode="url"
                autocomplete="off"
                spellcheck="false"
                placeholder="http://192.168.0.6:7912"
              />
              <button type="submit" class="btn btn--secondary" :disabled="testingSpoolman">
                {{ testingSpoolman ? "Test…" : "Tester et enregistrer" }}
              </button>
            </div>
            <span class="field__help" :class="{ 'field__help--error': spoolmanStatus.error }">{{ spoolmanStatus.text }}</span>
          </form>
          <div v-if="s.spoolman_url" class="reminders">
            <span class="field__label">Rappels de changement de bobine</span>
            <label class="switch">
              <input type="checkbox" :checked="s.spool_reminder_finish" @change="save({ spool_reminder_finish: ($event.target as HTMLInputElement).checked })" />
              <span class="switch__track" aria-hidden="true" />
              <span class="switch__text">
                <span>À la fin de l’impression précédente</span>
                <small>Dans la notification « Impression terminée », avec un bouton « Bobine changée ».</small>
              </span>
            </label>
            <div class="morning">
              <label class="switch">
                <input type="checkbox" :checked="!!s.spool_reminder_morning" @change="save({ spool_reminder_morning: ($event.target as HTMLInputElement).checked ? morningTime || '08:00' : '' })" />
                <span class="switch__track" aria-hidden="true" />
                <span class="switch__text">
                  <span>Chaque matin, si une impression des prochaines 24 h demande une autre bobine</span>
                  <small>Signale aussi les bobines qui n’auront pas assez de filament. Rien n’est envoyé s’il n’y a rien à préparer.</small>
                </span>
              </label>
              <label v-if="s.spool_reminder_morning" class="morning__time">
                <span class="visually-hidden">Heure du rappel du matin</span>
                <input v-model="morningTime" type="time" class="input mono" @change="morningTime && saveSoon({ spool_reminder_morning: morningTime })" />
              </label>
            </div>
            <p class="small muted">Au lancement, Printy demande toujours de confirmer le changement de bobine, quels que soient ces réglages.</p>
          </div>
          <p class="small muted">Laisse vide pour désactiver. Avec l’add-on Spoolman de Home Assistant, l’adresse est celle de ton Home Assistant suivie du port de Spoolman (7912 par défaut).</p>
        </div>
      </section>

      <!-- Stock and Home Assistant -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Stock et Home Assistant</h2>
          <p class="muted small">Ce que Printy range et publie tout seul.</p>
        </div>
        <div class="group__body">
          <label class="switch">
            <input type="checkbox" :checked="s.stock_auto" @change="save({ stock_auto: ($event.target as HTMLInputElement).checked })" />
            <span class="switch__track" aria-hidden="true" />
            <span class="switch__text">
              <span>Ajouter les pièces au stock à la fin d’une impression réussie</span>
              <small>Rangées par couleur de bobine. « Impression ratée » sur l’impression les retire.</small>
            </span>
          </label>
          <label v-if="s.env.home_assistant" class="switch">
            <input type="checkbox" :checked="s.ha_entities" @change="save({ ha_entities: ($event.target as HTMLInputElement).checked })" />
            <span class="switch__track" aria-hidden="true" />
            <span class="switch__text">
              <span>Publier les entités dans Home Assistant</span>
              <small>État, progression, temps restant, fin et prochaine impression de chaque imprimante ; impressions à traiter ; stock.</small>
            </span>
          </label>
          <p v-if="s.env.home_assistant && s.ha_entities" class="notice" :class="{ 'notice--ok': s.env.entities.mode === 'mqtt' && !s.env.entities.error }">
            <span>
              <template v-if="s.env.entities.mode === 'mqtt'">
                {{ s.env.entities.count }} entités publiées par MQTT, regroupées dans les appareils « Printy ».
              </template>
              <template v-else-if="s.env.entities.mode === 'rest'">
                {{ s.env.entities.count }} entités publiées sans MQTT (visibles dans Outils de développement → États).
                Installe l’add-on Mosquitto pour de vraies entités, avec appareil et historique.
              </template>
              <template v-else>Publication en cours de démarrage.</template>
              <template v-if="s.env.entities.error"><br />Dernière erreur : {{ s.env.entities.error }}</template>
            </span>
          </p>
        </div>
      </section>

      <!-- Appearance -->
      <section class="panel group">
        <div class="group__intro">
          <h2>Apparence</h2>
          <p class="muted small">Propre à cet appareil.</p>
        </div>
        <div class="group__body">
          <div class="segmented" role="group" aria-label="Thème">
            <button type="button" :aria-pressed="theme === 'auto'" @click="applyTheme('auto')">Automatique</button>
            <button type="button" :aria-pressed="theme === 'light'" @click="applyTheme('light')">Clair</button>
            <button type="button" :aria-pressed="theme === 'dark'" @click="applyTheme('dark')">Sombre</button>
          </div>
        </div>
      </section>

      <details class="more panel">
        <summary><ChevronRight aria-hidden="true" />Réglages avancés</summary>
        <div class="more__body">
          <div class="form-row">
            <div class="field">
              <label for="adv-timeout">Attente de confirmation (min)</label>
              <input id="adv-timeout" class="input mono" type="number" min="5" max="240" :value="s.confirm_timeout_min" inputmode="numeric"
                     @change="saveSoon({ confirm_timeout_min: Number(($event.target as HTMLInputElement).value) })" />
              <span class="field__help">Passé ce délai, l’impression passe « à revoir » et la prise est coupée.</span>
            </div>
            <div class="field">
              <label for="adv-keep">Garder allumée si la suivante démarre dans (min)</label>
              <input id="adv-keep" class="input mono" type="number" min="0" max="240" :value="s.keep_on_window_min" inputmode="numeric"
                     @change="saveSoon({ keep_on_window_min: Number(($event.target as HTMLInputElement).value) })" />
              <span class="field__help">Évite d’éteindre puis rallumer entre deux impressions proches.</span>
            </div>
          </div>
          <p class="small muted">Fuseau horaire : <span class="mono">{{ s.env.timezone }}</span></p>
          <div v-if="Object.keys(s.env.errors).length" class="notice notice--error">
            <span>
              Erreurs récentes :
              <template v-for="(e, name) in s.env.errors" :key="name"><br />{{ name }} : {{ e.error }}</template>
            </span>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.stack { display: grid; gap: var(--space-lg); max-width: 960px; }
.group { display: grid; grid-template-columns: minmax(0, 4fr) minmax(0, 7fr); gap: var(--space-lg) var(--space-xl); }
@media (max-width: 60rem) { .group { grid-template-columns: minmax(0, 1fr); } }
.group__intro { display: grid; align-content: start; gap: var(--space-xs); }
.group__intro h2 { font-size: var(--text-lg); }
.group__body { display: grid; gap: var(--space-md); align-content: start; min-width: 0; }
.checks { display: grid; gap: var(--space-sm); }
.inline { display: flex; gap: var(--space-xs); }
.tagrow { display: flex; flex-wrap: wrap; gap: var(--space-2xs); }
.tag__x { display: grid; place-items: center; width: 22px; height: 22px; margin-right: -4px; padding: 0; border: 0; background: none; color: var(--color-muted); cursor: pointer; }
.tag__x:hover { color: var(--color-ink); background: var(--color-paper-2); }
.tag__x:focus-visible { outline: 2px solid var(--color-focus); }

.notice--ok { background: var(--color-success-soft); color: var(--color-success); }
.notice--error { background: var(--color-error-soft); color: var(--color-error); }
.reminders { display: grid; gap: var(--space-sm); padding-top: var(--space-md); border-top: var(--rule-hair) solid var(--color-rule); }
.morning { display: flex; flex-wrap: wrap; gap: var(--space-sm); align-items: flex-start; justify-content: space-between; }
.morning .switch { flex: 1 1 280px; }
.morning__time .input { width: 120px; }
.ranges { display: grid; gap: var(--space-xs); margin: 0; padding: 0; list-style: none; }
.ranges li { display: flex; align-items: center; gap: var(--space-xs); }
.ranges .input { width: 130px; }

.creds { display: grid; gap: var(--space-xs); margin: 0 0 var(--space-xs) 52px; padding: var(--space-sm) var(--space-md); background: var(--color-paper-2); }
.creds div { display: grid; gap: var(--space-3xs); min-width: 0; }
.creds dt { font-size: var(--text-sm); color: var(--color-muted); }
.creds dd { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2xs); margin: 0; }
.creds code { overflow-wrap: anywhere; }
@media (max-width: 40rem) { .creds { margin-left: 0; } }

details.more.panel { padding-block: 0; }
</style>
