<script setup lang="ts">
import { computed } from "vue";
import { jobStatusLabel, printerStateLabel } from "../format";
import type { JobStatus, PrinterState } from "../types";

const props = defineProps<{ job?: JobStatus; printer?: PrinterState }>();

const tone: Record<string, string> = {
  scheduled: "",
  powering_on: "active",
  awaiting_confirmation: "attention",
  ready: "active",
  starting: "active",
  printing: "active",
  paused: "warn",
  finished: "ok",
  failed: "error",
  cancelled: "off",
  needs_attention: "attention",
  // printers
  offline: "off",
  idle: "ok",
  busy: "warn",
  error: "error",
};

const key = computed(() => (props.job ?? props.printer) as string);
const label = computed(() =>
  props.job ? jobStatusLabel[props.job] : props.printer ? printerStateLabel[props.printer] : "",
);
const cls = computed(() => {
  const t = props.printer === "printing" ? "active" : props.printer === "finished" ? "warn" : tone[key.value];
  return t ? `status--${t}` : "";
});
</script>

<template>
  <span class="status" :class="cls">{{ label }}</span>
</template>
