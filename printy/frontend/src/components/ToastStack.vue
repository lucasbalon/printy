<script setup lang="ts">
import { X } from "lucide-vue-next";
import { dismiss, toasts } from "../store";
</script>

<template>
  <div class="toasts" role="status" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`toast--${t.tone}`">
        <p>{{ t.message }}</p>
        <button
          v-if="t.action"
          type="button"
          class="toast__action"
          @click="t.action.run(); dismiss(t.id)"
        >
          {{ t.action.label }}
        </button>
        <button type="button" class="toast__close" aria-label="Fermer" @click="dismiss(t.id)">
          <X :size="16" aria-hidden="true" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  z-index: var(--z-toast);
  left: 50%;
  bottom: var(--space-lg);
  display: grid;
  gap: var(--space-xs);
  width: min(440px, calc(100% - 2 * var(--space-md)));
  transform: translateX(-50%);
}
.toast {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-xs) var(--space-sm) var(--space-md);
  background: var(--color-ink);
  color: var(--color-paper);
  box-shadow: 6px 6px 0 var(--color-paper-3);
  font-size: var(--text-sm);
}
.toast p { flex: 1; }
.toast--error { background: var(--color-error); }
.toast__action, .toast__close {
  display: grid;
  place-items: center;
  min-height: 32px;
  padding: 0 var(--space-xs);
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-weight: 500;
}
.toast__action { text-decoration: underline; text-underline-offset: 0.2em; }
.toast__action:hover, .toast__close:hover { background: color-mix(in oklab, var(--color-paper) 14%, transparent); }
.toast__close { width: 32px; padding: 0; }
@media (max-width: 40rem) { .toasts { bottom: calc(76px + env(safe-area-inset-bottom)); } }
.toast-enter-active { animation: toast-in var(--dur-short) var(--ease-out); }
.toast-leave-active { animation: toast-in var(--dur-short) var(--ease-in) reverse; }
@keyframes toast-in { from { opacity: 0; transform: translateY(8px); } }
</style>
