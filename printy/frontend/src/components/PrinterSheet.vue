<script setup lang="ts">
import { AlertTriangle, ChevronRight } from "lucide-vue-next";
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api";
import { attempt, loadDrivers, loadEntities, state } from "../store";
import type { Printer } from "../types";
import AppSheet from "./AppSheet.vue";
import EntitySelect from "./EntitySelect.vue";

const props = defineProps<{ open: boolean; printer: Printer | null }>();
const emit = defineEmits<{ close: []; saved: [printer: Printer]; deleted: [] }>();

const form = reactive({
  name: "",
  driver: "creality_k2",
  host: "",
  options: {} as Record<string, unknown>,
  switch_entity: null as string | null,
  power_entity: null as string | null,
  camera_source: "printer" as Printer["camera_source"],
  camera_entity: null as string | null,
  power_on_lead_min: 10,
  cooldown_temp: 45,
});
const saving = ref(false);
const error = ref("");
const triedSave = ref(false);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    error.value = "";
    triedSave.value = false;
    await Promise.all([attempt(loadDrivers), loadEntities(true)]);
    const p = props.printer;
    Object.assign(form, {
      name: p?.name ?? "",
      driver: p?.driver ?? "creality_k2",
      host: p?.host ?? "",
      options: { ...(p?.options ?? {}) },
      switch_entity: p?.switch_entity ?? null,
      power_entity: p?.power_entity ?? null,
      camera_source: p?.camera_source ?? "printer",
      camera_entity: p?.camera_entity ?? null,
      power_on_lead_min: p?.power_on_lead_min ?? 10,
      cooldown_temp: p?.cooldown_temp ?? 45,
    });
    fillDefaults();
  },
  { immediate: true },
);

const driver = computed(() => state.drivers.find((d) => d.key === form.driver) ?? null);
function fillDefaults() {
  for (const o of driver.value?.options ?? []) {
    if (form.options[o.key] === undefined && o.default != null) form.options[o.key] = o.default;
  }
}
watch(() => form.driver, fillDefaults);

const entities = computed(() => state.entities ?? { switches: [], cameras: [], power_sensors: [], notify: [] });
const nameError = computed(() => triedSave.value && !form.name.trim());
const hostError = computed(() => triedSave.value && !!driver.value?.host_required && !form.host.trim());

async function save() {
  triedSave.value = true;
  if (nameError.value || hostError.value) return;
  saving.value = true;
  error.value = "";
  try {
    const body = { ...form, camera_entity: form.camera_source === "ha" ? form.camera_entity : null };
    const saved = props.printer
      ? await api.put<Printer>(`/printers/${props.printer.id}`, body)
      : await api.post<Printer>("/printers", body);
    emit("saved", saved);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.printer || !window.confirm(`Supprimer « ${props.printer.name} » ?`)) return;
  const ok = await attempt(() => api.del(`/printers/${props.printer!.id}`));
  if (ok !== undefined) emit("deleted");
}
</script>

<template>
  <AppSheet :open="open" :title="printer ? `Modifier ${printer.name}` : 'Ajouter une imprimante'" @close="emit('close')">
    <form id="printer-form" class="form-grid" novalidate @submit.prevent="save">
      <div class="field">
        <label for="p-name">Nom</label>
        <input
          id="p-name"
          v-model="form.name"
          class="input"
          name="printer-name"
          autocomplete="off"
          placeholder="K2"
          :aria-invalid="nameError"
          autofocus
        />
        <span class="field__help" :class="{ 'field__help--error': nameError }">{{ nameError ? "Donne un nom à l’imprimante." : "" }}</span>
      </div>

      <fieldset class="field drivers">
        <legend class="field__label">Modèle</legend>
        <label v-for="d in state.drivers" :key="d.key" class="driver" :class="{ 'is-checked': form.driver === d.key }">
          <input v-model="form.driver" type="radio" name="driver" :value="d.key" />
          <span class="driver__text">
            <span>{{ d.label }}</span>
            <small v-if="d.note">{{ d.note }}</small>
          </span>
        </label>
      </fieldset>

      <div v-if="driver?.host_required" class="field">
        <label for="p-host">{{ driver.host_label }}</label>
        <input
          id="p-host"
          v-model.trim="form.host"
          class="input mono"
          name="printer-host"
          inputmode="url"
          autocomplete="off"
          spellcheck="false"
          placeholder="192.168.1.50"
          :aria-invalid="hostError"
        />
        <span class="field__help" :class="{ 'field__help--error': hostError }">
          {{ hostError ? "L’adresse est nécessaire pour joindre l’imprimante." : "Visible dans les réglages réseau de l’imprimante." }}
        </span>
      </div>

      <div class="field">
        <label for="p-switch">Prise connectée</label>
        <EntitySelect id="p-switch" v-model="form.switch_entity" :options="entities.switches" placeholder="switch.prise_k2" empty-label="Pas de prise" />
        <span class="field__help">Allumée avant l’impression, coupée une fois l’imprimante refroidie.</span>
      </div>

      <div class="field">
        <span class="field__label" id="cam-label">Caméra pour la photo du plateau</span>
        <div class="segmented" role="group" aria-labelledby="cam-label">
          <button type="button" :aria-pressed="form.camera_source === 'printer'" :disabled="!driver?.has_camera" @click="form.camera_source = 'printer'">
            Intégrée
          </button>
          <button type="button" :aria-pressed="form.camera_source === 'ha'" @click="form.camera_source = 'ha'">Home Assistant</button>
          <button type="button" :aria-pressed="form.camera_source === 'none'" @click="form.camera_source = 'none'">Aucune</button>
        </div>
        <EntitySelect
          v-if="form.camera_source === 'ha'"
          id="p-camera"
          v-model="form.camera_entity"
          :options="entities.cameras"
          placeholder="camera.atelier"
          empty-label="Choisir une caméra"
          aria-label="Caméra Home Assistant"
        />
        <span class="field__help">{{ form.camera_source === "none" ? "La notification partira sans photo." : "" }}</span>
      </div>

      <details class="more">
        <summary><ChevronRight aria-hidden="true" />Réglages avancés</summary>
        <div class="more__body">
          <div class="form-row">
            <div class="field">
              <label for="p-lead">Allumer avant (min)</label>
              <input id="p-lead" v-model.number="form.power_on_lead_min" class="input mono" type="number" min="0" max="120" inputmode="numeric" />
            </div>
            <div class="field">
              <label for="p-cool">Couper sous (°C)</label>
              <input id="p-cool" v-model.number="form.cooldown_temp" class="input mono" type="number" min="25" max="80" inputmode="numeric" />
            </div>
          </div>
          <div class="field">
            <label for="p-power">Mesure de consommation</label>
            <EntitySelect id="p-power" v-model="form.power_entity" :options="entities.power_sensors" placeholder="sensor.prise_k2_puissance" />
            <span class="field__help">Mesure les kWh consommés par pièce.</span>
          </div>
          <div v-for="o in driver?.options ?? []" :key="o.key" class="field">
            <label :for="`opt-${o.key}`">{{ o.label }}</label>
            <input
              :id="`opt-${o.key}`"
              v-model="form.options[o.key]"
              class="input mono"
              :type="o.kind === 'number' ? 'number' : o.kind === 'password' ? 'password' : 'text'"
              autocomplete="off"
              spellcheck="false"
              :placeholder="o.kind === 'password' && printer ? 'Inchangée' : ''"
            />
            <span class="field__help">{{ o.help }}</span>
          </div>
        </div>
      </details>

      <p v-if="error" class="notice notice--error" role="alert">
        <AlertTriangle aria-hidden="true" />
        <span>{{ error }}</span>
      </p>
    </form>

    <template #foot>
      <button v-if="printer" type="button" class="btn btn--danger foot-left" @click="remove">Supprimer</button>
      <button type="button" class="btn btn--ghost" @click="emit('close')">Annuler</button>
      <button type="submit" form="printer-form" class="btn btn--primary" :disabled="saving" :aria-busy="saving">
        {{ saving ? "Enregistrement…" : printer ? "Enregistrer" : "Ajouter" }}
      </button>
    </template>
  </AppSheet>
</template>

<style scoped>
.drivers { display: grid; gap: var(--space-2xs); margin: 0; padding: 0; border: 0; }
.drivers legend { margin-bottom: var(--space-2xs); padding: 0; }
.driver {
  position: relative;
  display: flex;
  gap: var(--space-sm);
  align-items: flex-start;
  padding: var(--space-sm) var(--space-md);
  border: var(--rule-hair) solid var(--color-rule);
  background: var(--color-plate);
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.driver:hover { background: var(--color-paper-2); }
.driver.is-checked { border-color: var(--color-ink); box-shadow: inset 0 0 0 1px var(--color-ink); }
.driver input { accent-color: var(--color-ink); margin-top: var(--space-2xs); }
.driver input:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.driver__text { display: grid; gap: var(--space-3xs); }
.driver__text small { color: var(--color-muted); font-size: var(--text-sm); }
.segmented { margin-bottom: var(--space-2xs); }
.notice--error { background: var(--color-error-soft); color: var(--color-error); }
.foot-left { margin-right: auto; }
</style>
