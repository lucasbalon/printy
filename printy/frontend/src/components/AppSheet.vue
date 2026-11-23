<script setup lang="ts">
import { X } from "lucide-vue-next";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{ open: boolean; title: string }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
let returnFocus: HTMLElement | null = null;

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
  if (e.key === "Tab" && panel.value) {
    const items = panel.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      returnFocus = document.activeElement as HTMLElement | null;
      document.addEventListener("keydown", onKey);
      document.body.style.overflow = "hidden";
      await nextTick();
      const target = panel.value?.querySelector<HTMLElement>("[autofocus], .sheet__body input, .sheet__body button");
      (target ?? panel.value)?.focus();
    } else {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      returnFocus?.focus?.();
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKey);
  document.body.style.overflow = "";
});
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="sheet-scrim" @click="emit('close')" />
    </Transition>
    <Transition name="sheet">
      <section v-if="open" ref="panel" class="sheet" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1">
        <header class="sheet__head">
          <h2>{{ title }}</h2>
          <button type="button" class="btn btn--ghost btn--icon" aria-label="Fermer" @click="emit('close')">
            <X aria-hidden="true" />
          </button>
        </header>
        <div class="sheet__body"><slot /></div>
        <footer v-if="$slots.foot" class="sheet__foot"><slot name="foot" /></footer>
      </section>
    </Transition>
  </Teleport>
</template>
