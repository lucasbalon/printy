import "@fontsource/syne/700.css";
import "@fontsource/onest/400.css";
import "@fontsource/onest/500.css";
import "@fontsource/jetbrains-mono/500.css";
import "./styles/app.css";

import { createApp } from "vue";
import { applyTheme } from "./theme";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import LibraryView from "./views/LibraryView.vue";
import ModelView from "./views/ModelView.vue";
import PlanningView from "./views/PlanningView.vue";
import PrintersView from "./views/PrintersView.vue";
import SettingsView from "./views/SettingsView.vue";
import SpoolmanView from "./views/SpoolmanView.vue";
import StockView from "./views/StockView.vue";

// Hash history: Home Assistant Ingress serves the app under a dynamic path.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "planning", component: PlanningView, meta: { title: "Planning" } },
    { path: "/bibliotheque", name: "library", component: LibraryView, meta: { title: "Bibliothèque" } },
    { path: "/bibliotheque/:id", name: "model", component: ModelView, props: true, meta: { title: "Modèle" } },
    { path: "/imprimantes", name: "printers", component: PrintersView, meta: { title: "Imprimantes" } },
    { path: "/stock", name: "stock", component: StockView, meta: { title: "Stock" } },
    { path: "/bobines", name: "spools", component: SpoolmanView, meta: { title: "Bobines" } },
    { path: "/reglages", name: "settings", component: SettingsView, meta: { title: "Réglages" } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

router.afterEach((to) => {
  document.title = `${to.meta.title ?? "Printy"} – Printy`;
});

applyTheme();
createApp(App).use(router).mount("#app");
