<script setup lang="ts">
import { Minus, Plus } from "lucide-vue-next";
import { computed, ref } from "vue";
import { api } from "../api";
import ModelThumb from "../components/ModelThumb.vue";
import { kwh, when } from "../format";
import { attempt, toast, usePolling } from "../store";
import type { Product, StockMove } from "../types";

const products = ref<Product[] | null>(null);
const moves = ref<StockMove[]>([]);
const failed = ref(false);
const busy = ref(false);
const filter = ref<"all" | "low" | "in">("all");

async function load() {
  try {
    const [p, m] = await Promise.all([api.get<Product[]>("/stock"), api.get<StockMove[]>("/stock/moves?limit=30")]);
    products.value = p;
    moves.value = m;
    failed.value = false;
  } catch {
    failed.value = true;
  }
}
usePolling(load, 15000);

const shown = computed(() =>
  (products.value ?? []).filter((p) =>
    filter.value === "low" ? p.stock_low : filter.value === "in" ? p.stock_total > 0 : true,
  ),
);
const totals = computed(() => {
  const list = products.value ?? [];
  return {
    pieces: list.reduce((n, p) => n + p.stock_total, 0),
    low: list.filter((p) => p.stock_low).length,
  };
});

async function stockMove(p: Product, variant: string, color: string | null, delta: number, reason: StockMove["reason"]) {
  busy.value = true;
  const res = await attempt(() => api.post<Product>("/stock/move", { model_id: p.id, variant, color, delta, reason }));
  busy.value = false;
  if (!res) return;
  const i = products.value?.findIndex((x) => x.id === p.id) ?? -1;
  if (i >= 0 && products.value) products.value[i] = res;
  attempt(async () => (moves.value = await api.get<StockMove[]>("/stock/moves?limit=30")));
  if (reason === "sale") {
    toast(`Vente enregistrée : ${p.name}, ${variant}.`, "info", {
      label: "Annuler",
      run: () => stockMove(p, variant, color, -delta, "adjust"),
    });
  }
}

// add a colour by hand (pieces printed before Printy, or bought in)
const adding = ref<number | null>(null);
const newVariant = ref("");
const newQty = ref(1);
async function addVariant(p: Product) {
  const name = newVariant.value.trim();
  if (!name || newQty.value < 1) return;
  await stockMove(p, name, null, newQty.value, "adjust");
  adding.value = null;
  newVariant.value = "";
  newQty.value = 1;
}

const reasonLabel: Record<StockMove["reason"], string> = {
  print: "Impression",
  sale: "Vente",
  adjust: "Ajustement",
  defect: "Ratée",
};
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="page-head__title">
        <h1>Stock</h1>
        <p v-if="products" class="muted small">
          <span class="mono">{{ totals.pieces }}</span> pièce{{ totals.pieces > 1 ? "s" : "" }} en stock<template v-if="totals.low">,
          <strong class="low">{{ totals.low }} sous le seuil</strong></template>
        </p>
      </div>
      <div class="page-head__actions">
        <div class="segmented" role="group" aria-label="Filtrer">
          <button type="button" :aria-pressed="filter === 'all'" @click="filter = 'all'">Tous</button>
          <button type="button" :aria-pressed="filter === 'in'" @click="filter = 'in'">En stock</button>
          <button type="button" :aria-pressed="filter === 'low'" @click="filter = 'low'">Stock bas</button>
        </div>
      </div>
    </header>

    <div v-if="failed && !products" class="empty empty--error">
      <h2>Stock indisponible</h2>
      <p>Printy ne répond pas. Vérifie que l’add-on tourne, puis réessaie.</p>
      <button type="button" class="btn btn--secondary" @click="load">Réessayer</button>
    </div>

    <div v-else-if="!products" class="list" aria-busy="true">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 120px" />
    </div>

    <div v-else-if="!products.length" class="empty">
      <h2>Aucun produit</h2>
      <p>
        Chaque modèle de la bibliothèque est un produit. Les pièces arrivent en stock toutes seules à la fin d’une
        impression réussie, rangées par couleur de bobine.
      </p>
      <RouterLink to="/bibliotheque" class="btn btn--primary">Ouvrir la bibliothèque</RouterLink>
    </div>

    <p v-else-if="!shown.length" class="muted">Aucun produit dans ce filtre.</p>

    <ul v-else class="list" role="list">
      <li v-for="p in shown" :key="p.id" class="product" :class="{ 'is-low': p.stock_low }">
        <RouterLink :to="`/bibliotheque/${p.id}`" class="product__thumb" :aria-label="p.name">
          <ModelThumb :sliced-id="p.cover_id" size="sm" />
        </RouterLink>

        <div class="product__main">
          <div class="product__head">
            <RouterLink :to="`/bibliotheque/${p.id}`" class="product__name">{{ p.name }}</RouterLink>
            <span v-if="p.stock_low" class="status status--attention">Stock bas</span>
          </div>
          <p v-if="p.energy.kwh_per_piece != null || p.stock_alert != null" class="product__meta small muted">
            <template v-if="p.energy.kwh_per_piece != null">≈ <span class="num">{{ kwh(p.energy.kwh_per_piece) }}</span> par pièce</template>
            <template v-if="p.energy.kwh_per_piece != null && p.stock_alert != null">, </template>
            <template v-if="p.stock_alert != null">alerte sous {{ p.stock_alert }}</template>
          </p>

          <ul v-if="p.stock.length" class="variants" role="list">
            <li v-for="v in p.stock" :key="v.variant" class="variant">
              <span class="swatch" :style="{ background: v.color ?? 'transparent' }" />
              <span class="variant__name">{{ v.variant }}</span>
              <span class="variant__qty mono" :aria-label="`${v.quantity} en stock`">{{ v.quantity }}</span>
              <span class="variant__actions">
                <button
                  type="button"
                  class="btn btn--ghost btn--sm btn--icon"
                  :aria-label="`Retirer une pièce ${v.variant}`"
                  :disabled="busy || v.quantity === 0"
                  @click="stockMove(p, v.variant, v.color, -1, 'adjust')"
                >
                  <Minus aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="btn btn--ghost btn--sm btn--icon"
                  :aria-label="`Ajouter une pièce ${v.variant}`"
                  :disabled="busy"
                  @click="stockMove(p, v.variant, v.color, 1, 'adjust')"
                >
                  <Plus aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="btn btn--secondary btn--sm"
                  :disabled="busy || v.quantity === 0"
                  @click="stockMove(p, v.variant, v.color, -1, 'sale')"
                >
                  Vendue
                </button>
              </span>
            </li>
          </ul>
          <p v-else class="small muted">Rien en stock.</p>

          <form v-if="adding === p.id" class="addform" @submit.prevent="addVariant(p)">
            <label :for="`nv-${p.id}`" class="visually-hidden">Couleur</label>
            <input :id="`nv-${p.id}`" v-model="newVariant" class="input" placeholder="Couleur, ex. Blanc mat" autocomplete="off" autofocus />
            <label :for="`nq-${p.id}`" class="visually-hidden">Quantité</label>
            <input :id="`nq-${p.id}`" v-model.number="newQty" class="input mono addform__qty" type="number" min="1" inputmode="numeric" />
            <button type="submit" class="btn btn--secondary btn--sm" :disabled="!newVariant.trim()">Ajouter</button>
            <button type="button" class="btn btn--ghost btn--sm" @click="adding = null">Annuler</button>
          </form>
          <button v-else type="button" class="btn btn--ghost btn--sm addbtn" @click="adding = p.id">
            <Plus aria-hidden="true" />Ajouter des pièces
          </button>
        </div>

        <div class="product__total">
          <span class="product__count">{{ p.stock_total }}</span>
          <span class="small muted">en stock</span>
        </div>
      </li>
    </ul>

    <section v-if="moves.length" class="section">
      <div class="section-head">
        <h2>Mouvements récents</h2>
      </div>
      <table class="spec">
        <thead>
          <tr>
            <th scope="col">Quand</th>
            <th scope="col">Produit</th>
            <th scope="col">Couleur</th>
            <th scope="col">Motif</th>
            <th scope="col" class="num">Quantité</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in moves" :key="m.id">
            <td class="small">{{ when(m.at) }}</td>
            <td>{{ m.model_name }}</td>
            <td>{{ m.variant }}</td>
            <td>{{ reasonLabel[m.reason] }}</td>
            <td class="num mono" :class="m.delta > 0 ? 'plus' : 'minus'">{{ m.delta > 0 ? `+${m.delta}` : m.delta }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.low { color: var(--color-accent-strong); font-weight: 500; }
.list { display: grid; gap: var(--space-sm); margin: 0; padding: 0; list-style: none; }
.product {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) auto;
  gap: var(--space-md);
  align-items: start;
  padding: var(--space-md);
  background: var(--color-plate);
  border: var(--rule-hair) solid var(--color-rule);
  border-left: 4px solid var(--color-rule);
}
.product.is-low { border-left-color: var(--color-accent); }
.product__thumb { display: block; width: 72px; }
.product__main { display: grid; gap: var(--space-xs); min-width: 0; }
.product__head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); }
.product__name { font-weight: 500; font-size: var(--text-lg); text-decoration: none; }
.product__name:hover { text-decoration: underline; }
.product__total { display: grid; justify-items: end; }
.product__count { font-weight: 500; font-size: var(--text-3xl); line-height: 1; font-variant-numeric: tabular-nums; }

.variants { display: grid; margin: 0; padding: 0; list-style: none; border-top: var(--rule-hair) solid var(--color-rule); }
.variant { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: var(--space-sm); min-height: 44px; border-bottom: var(--rule-hair) solid var(--color-rule); }
.variant__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.variant__qty { font-size: var(--text-lg); min-width: 2ch; text-align: right; }
.variant__actions { display: flex; align-items: center; gap: var(--space-3xs); }

.addbtn { justify-self: start; }
.addform { display: flex; flex-wrap: wrap; gap: var(--space-xs); align-items: center; }
.addform .input { min-height: var(--control-h-sm); width: auto; flex: 1 1 160px; }
.addform .addform__qty { flex: 0 0 80px; }

td.plus { color: var(--color-success); }
td.minus { color: var(--color-accent-strong); }
.spec .num { text-align: right; }

@media (max-width: 40rem) {
  .product { grid-template-columns: 56px minmax(0, 1fr); }
  .product__thumb { width: 56px; }
  .product__total { grid-column: 2; grid-row: 1; justify-self: end; align-self: start; }
  .product__main { grid-column: 1 / -1; }
  .variant { grid-template-columns: auto minmax(0, 1fr) auto; }
  .variant__actions { grid-column: 1 / -1; justify-content: flex-end; padding-bottom: var(--space-xs); }
  .spec thead { display: none; }
  .spec tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; padding: var(--space-xs) 0; border-bottom: var(--rule-hair) solid var(--color-rule); }
  .spec td { border: 0; padding: 0 var(--space-xs); }
  .spec td.num { grid-row: 1 / span 2; grid-column: 2; align-self: center; }
}
</style>
