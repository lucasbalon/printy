<script setup lang="ts">
// A numeric setting: label, value, unit-aware help. Emits the number on change.
const props = defineProps<{
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  help?: string;
}>();
const emit = defineEmits<{ save: [value: number] }>();

function onChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  if (Number.isFinite(v) && v >= props.min && v <= props.max) emit("save", v);
}
</script>

<template>
  <div class="field">
    <label :for="id">{{ label }}</label>
    <input
      :id="id"
      class="input mono"
      type="number"
      :min="min"
      :max="max"
      :step="step ?? 1"
      :value="value"
      inputmode="decimal"
      @change="onChange"
    />
    <span v-if="help" class="field__help">{{ help }}</span>
  </div>
</template>
