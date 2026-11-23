<script setup lang="ts">
import { computed, ref } from "vue";
import { grams } from "../format";
import type { Spool } from "../types";

// Shows the chosen spool; "Changer" opens the list, best matches first.
const props = defineProps<{
  spools: Spool[];
  noneLabel?: string;
  disabled?: boolean;
  label: string;
}>();
const model = defineModel<number | null>({ required: true });
const emit = defineEmits<{ change: [id: number | null] }>();

const open = ref(false);
const showAll = ref(false);
const selected = computed(() => props.spools.find((s) => s.id === model.value) ?? null);
const visible = computed(() => (showAll.value ? props.spools : props.spools.slice(0, 5)));

function pick(id: number | null) {
  model.value = id;
  open.value = false;
  showAll.value = false;
  emit("change", id);
}
</script>

<template>
  <div class="picker">
    <div class="current">
      <span class="swatch swatch--lg" :style="{ background: selected?.color ?? 'transparent' }" :class="{ 'is-empty': !selected }" />
      <span class="current__text">
        <span class="current__name">
          {{ selected ? selected.label : (noneLabel ?? "Aucune bobine") }}
          <span v-if="selected" class="muted mono small">n° {{ selected.id }}</span>
        </span>
        <span v-if="selected" class="muted small">
          {{ selected.material ?? "matière inconnue" }}<template v-if="selected.remaining_g != null">, reste <span class="mono">{{ grams(selected.remaining_g) }}</span></template><template v-if="selected.location">, {{ selected.location }}</template>
        </span>
      </span>
      <button
        type="button"
        class="btn btn--ghost btn--sm"
        :aria-expanded="open"
        :aria-label="`${label} : changer`"
        :disabled="disabled"
        @click="open = !open"
      >
        {{ open ? "Fermer" : "Changer" }}
      </button>
    </div>

    <div v-if="open" class="list" role="listbox" :aria-label="label">
      <button
        v-for="s in visible"
        :key="s.id"
        type="button"
        role="option"
        class="option"
        :aria-selected="s.id === model"
        @click="pick(s.id)"
      >
        <span class="swatch" :style="{ background: s.color ?? 'transparent' }" />
        <span class="option__name">{{ s.label }} <span class="muted mono small">n° {{ s.id }}</span><template v-if="s.loaded_on"><span class="muted small">, sur {{ s.loaded_on }}</span></template></span>
        <span class="muted small">{{ s.material }}</span>
        <span class="mono small">{{ s.remaining_g != null ? grams(s.remaining_g) : "–" }}</span>
        <span v-if="s.match" class="status status--ok option__match">Correspond</span>
      </button>
      <button v-if="!showAll && spools.length > 5" type="button" class="btn btn--ghost btn--sm" @click="showAll = true">
        Toutes les bobines ({{ spools.length }})
      </button>
      <button type="button" class="option option--none" :aria-selected="model === null" @click="pick(null)">
        {{ noneLabel ?? "Aucune bobine" }}
      </button>
      <RouterLink to="/bobines?nouvelle=1" class="btn btn--ghost btn--sm">Ajouter une bobine dans Spoolman</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.picker { display: grid; gap: var(--space-xs); }
.current { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: var(--space-sm); padding: var(--space-xs) var(--space-xs) var(--space-xs) var(--space-sm); border: var(--rule-hair) solid var(--color-rule); background: var(--color-plate); }
.current__text { display: grid; gap: var(--space-3xs); min-width: 0; }
.current__name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.swatch--lg { width: 24px; height: 24px; }
.swatch.is-empty { border-style: dashed; border-color: var(--color-muted); }

.list { display: grid; border-top: var(--rule-bar) solid var(--color-ink); max-height: 300px; overflow-y: auto; }
.option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: var(--space-sm);
  min-height: 44px;
  padding: 0 var(--space-sm);
  border: 0;
  border-bottom: var(--rule-hair) solid var(--color-rule);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out);
}
.option:hover { background: var(--color-paper-2); }
.option[aria-selected="true"] { background: var(--color-accent-soft); }
.option:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.option__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.option__match { min-height: 20px; }
.option--none { grid-template-columns: minmax(0, 1fr); color: var(--color-muted); }
.list .btn { justify-self: start; margin: var(--space-2xs) 0; }
</style>
