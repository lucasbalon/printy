<script setup lang="ts">
import { CalendarDays, Disc3, Library, Package, Printer, SlidersHorizontal } from "lucide-vue-next";
import { computed, onMounted } from "vue";
import DropOverlay from "./components/DropOverlay.vue";
import ToastStack from "./components/ToastStack.vue";
import { attempt, loadAttention, loadSettings, state, usePolling } from "./store";

usePolling(loadAttention, 10000);

onMounted(() => {
  if (!state.settings) attempt(loadSettings);
});

// "Bobines" (Spoolman) only shows once Spoolman is configured
const links = computed(() => [
  { to: "/", label: "Planning", icon: CalendarDays, exact: true },
  { to: "/bibliotheque", label: "Bibliothèque", icon: Library },
  { to: "/imprimantes", label: "Imprimantes", icon: Printer },
  { to: "/stock", label: "Stock", icon: Package },
  ...(state.settings?.spoolman_url ? [{ to: "/bobines", label: "Bobines", icon: Disc3 }] : []),
  { to: "/reglages", label: "Réglages", icon: SlidersHorizontal },
]);

const attentionCount = computed(() => state.attention.length);
const pendingCount = computed(() => state.pending);
</script>

<template>
  <a class="skip" href="#main">Aller au contenu</a>
  <header class="topbar">
    <div class="container topbar__inner">
      <RouterLink to="/" class="wordmark" aria-label="Printy, planning">Printy</RouterLink>
      <nav aria-label="Navigation principale" class="nav">
        <RouterLink
          v-for="l in links"
          :key="l.to"
          :to="l.to"
          class="nav__link"
          :class="{ 'is-exact': l.exact }"
          :exact-active-class="l.exact ? 'is-active' : ''"
          :active-class="l.exact ? '' : 'is-active'"
        >
          <component :is="l.icon" class="nav__icon" aria-hidden="true" />
          <span>{{ l.label }}</span>
          <span v-if="l.exact && attentionCount" class="nav__count" :aria-label="`${attentionCount} à traiter`">{{ attentionCount }}</span>
          <span v-if="l.to === '/bibliotheque' && pendingCount" class="nav__count" :aria-label="`${pendingCount} fichier(s) à trier`">
            {{ pendingCount }}
          </span>
        </RouterLink>
      </nav>
    </div>
  </header>
  <main id="main" class="container">
    <RouterView />
  </main>
  <DropOverlay />
  <ToastStack />
</template>

<style scoped>
.skip {
  position: absolute;
  left: var(--space-md);
  top: -100px;
  z-index: var(--z-tooltip);
  padding: var(--space-xs) var(--space-md);
  background: var(--color-ink);
  color: var(--color-paper);
}
.skip:focus-visible { top: var(--space-xs); }

.topbar {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky-nav);
  background: var(--color-paper);
  border-bottom: var(--rule-bar) solid var(--color-ink);
}
.topbar__inner {
  display: flex;
  align-items: stretch;
  gap: var(--space-xl);
  height: var(--topbar-h);
}
.wordmark {
  display: flex;
  align-items: center;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: var(--text-xl);
  letter-spacing: var(--tracking-display);
  text-decoration: none;
}
.wordmark:focus-visible { outline-offset: -2px; }
.nav {
  display: flex;
  align-items: stretch;
  gap: var(--space-2xs);
  overflow-x: auto;
  scrollbar-width: none;
}
.nav__link {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  padding: 0 var(--space-sm);
  color: var(--color-muted);
  font-weight: 500;
  font-size: var(--text-md);
  text-decoration: none;
  white-space: nowrap;
  transition: color var(--dur-micro) var(--ease-out);
}
.nav__link::after {
  content: "";
  position: absolute;
  left: var(--space-sm);
  right: var(--space-sm);
  bottom: -2px;
  height: 4px;
  background: var(--color-ink);
  transform: scaleY(0);
  transform-origin: bottom;
  transition: transform var(--dur-micro) var(--ease-out);
}
.nav__link:hover { color: var(--color-ink); }
.nav__link.is-active { color: var(--color-ink); }
.nav__link.is-active::after { transform: scaleY(1); }
.nav__link:focus-visible { outline-offset: -2px; }
.nav__count {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 4px;
  background: var(--color-accent);
  color: var(--color-accent-ink);
  font-family: var(--font-outlier);
  font-size: var(--text-xs);
}
.nav__icon { display: none; }

/* Phones (and the Home Assistant companion app): the destinations move to a bottom tab bar */
@media (max-width: 40rem) {
  .wordmark { font-size: var(--text-lg); }
  .nav {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: var(--z-sticky-nav);
    gap: 0;
    padding-bottom: env(safe-area-inset-bottom);
    background: var(--color-paper);
    border-top: var(--rule-bar) solid var(--color-ink);
  }
  .nav__link {
    flex: 1;
    flex-direction: column;
    justify-content: center;
    gap: var(--space-3xs);
    min-height: 60px;
    padding: var(--space-2xs);
    font-size: var(--text-xs);
  }
  .nav__icon { display: block; width: 22px; height: 22px; }
  .nav__link::after { top: -2px; bottom: auto; left: 0; right: 0; transform-origin: top; }
  .nav__count { position: absolute; top: var(--space-2xs); left: calc(50% + 6px); }
  main { padding-bottom: calc(76px + env(safe-area-inset-bottom)); }
}
</style>
