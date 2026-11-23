<script setup lang="ts">
import { Loader2 } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { simplify, triangleCount } from "../simplify";

// Interactive preview of an STL or 3MF source. three.js is loaded on demand.
// Heavy meshes are shown simplified by default; the detailed view stays one click away.
const props = defineProps<{ url: string; filename: string; color?: string | null }>();

const HEAVY = 300_000; // triangles
const TARGET = 150_000;

const host = ref<HTMLDivElement | null>(null);
const status = ref<"loading" | "ready" | "error">("loading");
const error = ref("");
const fullTriangles = ref(0);
const shownTriangles = ref(0);
const detailed = ref(false);
const heavy = computed(() => fullTriangles.value > HEAVY);
let cleanup: (() => void) | null = null;
let cached: { url: string; buffer: ArrayBuffer } | null = null;

const fmt = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1).replace(".", ",")} M` : `${Math.round(n / 1000)} k`;

/** A theme token as "rgb(...)": three.js does not parse oklch(), a canvas does. */
function token(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx || !value) return "";
  ctx.fillStyle = value;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `rgb(${r}, ${g}, ${b})`;
}

async function render() {
  cleanup?.();
  cleanup = null;
  status.value = "loading";
  const el = host.value;
  if (!el) return;
  try {
    const THREE = await import("three");
    const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
    if (cached?.url !== props.url) {
      const res = await fetch(props.url);
      if (!res.ok) throw new Error(`fichier indisponible (${res.status})`);
      cached = { url: props.url, buffer: await res.arrayBuffer() };
    }
    const buffer = cached.buffer;

    const toColor = (css: string, fallback: string) => {
      const c = new THREE.Color();
      try {
        c.setStyle(css || fallback);
      } catch {
        c.setStyle(fallback);
      }
      return c;
    };
    const material = new THREE.MeshStandardMaterial({
      color: toColor(props.color ?? "", "#d9d4cb"),
      roughness: 0.62,
      metalness: 0.02,
      side: THREE.DoubleSide, // lampshades are open shells: show the inside too
    });

    let object: InstanceType<typeof THREE.Object3D>;
    const name = props.filename.toLowerCase();
    if (name.endsWith(".stl")) {
      const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
      object = new THREE.Mesh(new STLLoader().parse(buffer), material);
    } else if (name.endsWith(".3mf")) {
      const { ThreeMFLoader } = await import("three/examples/jsm/loaders/3MFLoader.js");
      object = new ThreeMFLoader().parse(buffer);
    } else {
      throw new Error("format non prévisualisable");
    }

    // Count, then simplify heavy meshes unless the detailed view was asked for
    type Mesh = InstanceType<typeof THREE.Mesh>;
    const meshes: Mesh[] = [];
    object.traverse((o) => {
      if ((o as Mesh).isMesh) meshes.push(o as Mesh);
    });
    const count = (m: Mesh) => triangleCount(m.geometry.attributes.position.array, m.geometry.index?.array ?? null);
    fullTriangles.value = meshes.reduce((n, m) => n + count(m), 0);
    const simplified = heavy.value && !detailed.value;
    for (const m of meshes) {
      m.material = material;
      if (simplified) {
        const share = count(m) / fullTriangles.value;
        const positions = simplify(
          m.geometry.attributes.position.array as Float32Array,
          m.geometry.index?.array ?? null,
          Math.max(2000, Math.round(TARGET * share)),
        );
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        g.computeVertexNormals();
        m.geometry.dispose();
        m.geometry = g;
      } else if (!m.geometry.attributes.normal) {
        m.geometry.computeVertexNormals();
      }
    }
    shownTriangles.value = meshes.reduce((n, m) => n + count(m), 0);
    // Slicers are Z-up, three.js is Y-up
    object.rotation.x = -Math.PI / 2;

    const scene = new THREE.Scene();
    scene.background = toColor(token("--color-paper-2"), "#ece8e1");
    scene.add(object);
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    object.position.sub(center);
    object.position.y += size.y / 2; // rests on the grid
    const radius = Math.max(size.x, size.y, size.z) || 1;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8178, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(-1, 1.4, 1.2).multiplyScalar(radius * 2);
    scene.add(key);
    const grid = new THREE.GridHelper(radius * 2.4, 12, toColor(token("--color-rule"), "#c9c2b8"), toColor(token("--color-rule"), "#c9c2b8"));
    scene.add(grid);

    const camera = new THREE.PerspectiveCamera(35, 1, radius / 100, radius * 50);
    camera.position.set(radius * 1.5, radius * 1.1, radius * 1.9);
    // Detailed heavy meshes: no antialiasing and a lower pixel ratio keep rotation fluid
    const light = shownTriangles.value <= HEAVY;
    const renderer = new THREE.WebGLRenderer({ antialias: light, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, light ? 1.75 : 1));
    el.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, size.y / 2, 0);
    controls.enableDamping = true;
    controls.update();

    let frame = 0;
    const draw = () => {
      frame = 0;
      if (controls.update()) frame = requestAnimationFrame(draw); // keep easing while damping
      renderer.render(scene, camera);
    };
    const request = () => {
      if (!frame) frame = requestAnimationFrame(draw);
    };
    controls.addEventListener("change", request);

    const resize = () => {
      const { width, height } = el.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      request();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();
    status.value = "ready";

    cleanup = () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      object.traverse((o) => (o as InstanceType<typeof THREE.Mesh>).geometry?.dispose?.());
      material.dispose();
      renderer.domElement.remove();
    };
  } catch (e) {
    status.value = "error";
    error.value = e instanceof Error ? e.message : "aperçu impossible";
  }
}

onMounted(render);
watch(() => props.url, () => {
  detailed.value = false;
  render();
});
watch(detailed, render);
onBeforeUnmount(() => cleanup?.());
</script>

<template>
  <div class="preview">
    <div ref="host" class="preview__canvas" :aria-label="`Aperçu 3D de ${filename}`" role="img" />
    <div v-if="status === 'loading'" class="preview__state">
      <Loader2 class="spin" aria-hidden="true" />
      <span>Chargement du modèle 3D…</span>
    </div>
    <div v-else-if="status === 'error'" class="preview__state">
      <span>Aperçu 3D impossible : {{ error }}.</span>
    </div>
    <p v-else class="preview__hint">
      <template v-if="heavy">
        {{ detailed ? `${fmt(fullTriangles)} triangles affichés` : `Vue simplifiée : ${fmt(shownTriangles)} triangles sur ${fmt(fullTriangles)}` }}
      </template>
      <template v-else>Glisse pour tourner, molette ou pincement pour zoomer.</template>
    </p>
    <div v-if="heavy && status !== 'loading'" class="segmented preview__quality" role="group" aria-label="Qualité de l’aperçu">
      <button type="button" :aria-pressed="!detailed" @click="detailed = false">Simplifiée</button>
      <button type="button" :aria-pressed="detailed" @click="detailed = true">Détaillée</button>
    </div>
  </div>
</template>

<style scoped>
.preview { position: relative; aspect-ratio: 1; background: var(--color-paper-2); }
.preview__canvas { position: absolute; inset: 0; cursor: grab; touch-action: none; }
.preview__canvas:active { cursor: grabbing; }
.preview__canvas :deep(canvas) { display: block; width: 100%; height: 100%; }
.preview__state { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: var(--space-xs); color: var(--color-muted); font-size: var(--text-sm); text-align: center; padding: var(--space-lg); }
.preview__hint { position: absolute; left: var(--space-sm); bottom: var(--space-xs); font-size: var(--text-xs); color: var(--color-muted); pointer-events: none; }
.preview__quality { position: absolute; top: var(--space-xs); right: var(--space-xs); }
.preview__quality button { min-height: var(--control-h-sm); padding-inline: var(--space-sm); font-size: var(--text-xs); }
.spin { width: 24px; height: 24px; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
