<script setup lang="ts">
import { Box } from "lucide-vue-next";
import { ref, watch } from "vue";
import { urls } from "../api";

const props = defineProps<{ slicedId: number | null; alt?: string; size?: "sm" | "md" | "lg" }>();
const failed = ref(false);
watch(() => props.slicedId, () => (failed.value = false));
</script>

<template>
  <div class="thumb" :class="`thumb--${size ?? 'md'}`">
    <img
      v-if="slicedId && !failed"
      :src="urls.thumbnail(slicedId)"
      :alt="alt ?? ''"
      loading="lazy"
      width="300"
      height="300"
      @error="failed = true"
    />
    <Box v-else class="thumb__empty" aria-hidden="true" />
  </div>
</template>

<style scoped>
.thumb {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  background: var(--color-paper-2);
  overflow: hidden;
}
.thumb img { width: 100%; height: 100%; object-fit: contain; }
.thumb__empty { width: 40%; height: 40%; color: var(--color-paper-3); stroke-width: 1.25; }
.thumb--sm .thumb__empty { width: 55%; height: 55%; }
</style>
