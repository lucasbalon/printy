<script setup lang="ts">
import { computed } from "vue";
import type { EntityOption } from "../types";

// A Home Assistant entity picker. Falls back to a text field when the list is
// empty (Home Assistant unreachable) so the user is never stuck.
const props = defineProps<{ id: string; options: EntityOption[]; placeholder?: string; emptyLabel?: string }>();
const model = defineModel<string | null>({ required: true });

const known = computed(() => !model.value || props.options.some((o) => o.id === model.value));
</script>

<template>
  <select v-if="options.length && known" :id="id" v-model="model" class="select">
    <option :value="null">{{ emptyLabel ?? "Aucune" }}</option>
    <option v-for="o in options" :key="o.id" :value="o.id">{{ o.name }}</option>
  </select>
  <input
    v-else
    :id="id"
    v-model.trim="model"
    class="input mono"
    type="text"
    autocomplete="off"
    spellcheck="false"
    :placeholder="placeholder"
  />
</template>
