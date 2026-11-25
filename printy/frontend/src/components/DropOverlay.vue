<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { uploadFiles } from "../store";

// Files dropped anywhere in the app go to the library.
// On a model page they are attached to that model.
const visible = ref(false);
let depth = 0;
const route = useRoute();
const router = useRouter();

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");

function enter(e: DragEvent) {
  if (!hasFiles(e)) return;
  depth++;
  visible.value = true;
}
function leave(e: DragEvent) {
  if (!hasFiles(e)) return;
  depth = Math.max(0, depth - 1);
  if (depth === 0) visible.value = false;
}
function over(e: DragEvent) {
  if (hasFiles(e)) e.preventDefault();
}
async function drop(e: DragEvent) {
  if (!hasFiles(e)) return;
  e.preventDefault();
  depth = 0;
  visible.value = false;
  const files = Array.from(e.dataTransfer?.files ?? []);
  const modelId = route.name === "model" ? Number(route.params.id) : undefined;
  const res = await uploadFiles(files, modelId);
  const first = res?.imported[0];
  if (!first || modelId || res.imported.length !== 1) return;
  // filed: open its model; waiting to be filed: the library, where it can be filed
  router.push(first.model_id ? `/bibliotheque/${first.model_id}` : "/bibliotheque");
}

onMounted(() => {
  window.addEventListener("dragenter", enter);
  window.addEventListener("dragleave", leave);
  window.addEventListener("dragover", over);
  window.addEventListener("drop", drop);
});
onBeforeUnmount(() => {
  window.removeEventListener("dragenter", enter);
  window.removeEventListener("dragleave", leave);
  window.removeEventListener("dragover", over);
  window.removeEventListener("drop", drop);
});
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="drop" aria-hidden="true">
      <div class="drop__plate">
        <p class="drop__title">Déposer pour ajouter</p>
        <p class="drop__hint">
          {{ route.name === "model" ? "Les fichiers rejoindront ce modèle." : "Fichiers tranchés (.gcode, .gcode.3mf) ou sources (.stl, .3mf, .blend)." }}
        </p>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.drop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: grid;
  place-items: center;
  padding: var(--space-lg);
  background: color-mix(in oklab, var(--color-paper) 88%, transparent);
  pointer-events: none;
}
.drop__plate {
  display: grid;
  gap: var(--space-xs);
  width: min(560px, 100%);
  padding: var(--space-2xl) var(--space-xl);
  border: var(--rule-bar) dashed var(--color-ink);
  background: var(--color-plate);
  text-align: center;
}
.drop__title { font-family: var(--font-display); font-weight: 700; font-size: var(--text-2xl); letter-spacing: var(--tracking-display); }
.drop__hint { color: var(--color-muted); }
</style>
