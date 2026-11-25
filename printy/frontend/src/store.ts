import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { api, ApiError } from "./api";
import type { DriverInfo, HaEntities, Job, Printer, Settings, Spool, SpoolList } from "./types";

// ---- toasts ---------------------------------------------------------------

export interface Toast {
  id: number;
  message: string;
  tone: "info" | "error";
  action?: { label: string; run: () => void };
}

export const toasts = ref<Toast[]>([]);
let toastId = 0;

const timers = new Map<number, number>();

export function toast(message: string, tone: Toast["tone"] = "info", action?: Toast["action"]) {
  // the same message already on screen is extended, not stacked
  const same = toasts.value.find((t) => t.message === message && t.tone === tone && !t.action && !action);
  const id = same ? same.id : ++toastId;
  if (!same) toasts.value.push({ id, message, tone, action });
  window.clearTimeout(timers.get(id));
  timers.set(id, window.setTimeout(() => dismiss(id), tone === "error" ? 7000 : 4500));
}

export function dismiss(id: number) {
  toasts.value = toasts.value.filter((t) => t.id !== id);
}

/** Runs an API call and turns its failure into an error toast. */
export async function attempt<T>(fn: () => Promise<T>, success?: string): Promise<T | undefined> {
  try {
    const out = await fn();
    if (success) toast(success);
    return out;
  } catch (e) {
    toast(e instanceof ApiError ? e.message : "Une erreur inattendue est survenue.", "error");
    return undefined;
  }
}

// ---- shared state -----------------------------------------------------------

export const state = reactive({
  printers: [] as Printer[],
  printersLoaded: false,
  drivers: [] as DriverInfo[],
  settings: null as Settings | null,
  entities: null as HaEntities | null,
  attention: [] as Job[],
  pending: 0, // imported files waiting to be filed
  spools: { enabled: false, error: null, spools: [] } as SpoolList,
});

/** All spools (unsorted), refreshed at most every 20 s unless forced. */
let spoolsAt = 0;
export async function loadSpools(force = false) {
  if (!force && Date.now() - spoolsAt < 20_000) return;
  state.spools = await api.get<SpoolList>("/spools");
  spoolsAt = Date.now();
}

export function spoolById(id: number | null | undefined): Spool | null {
  return id == null ? null : (state.spools.spools.find((s) => s.id === id) ?? null);
}

export async function loadPrinters() {
  state.printers = await api.get<Printer[]>("/printers");
  state.printersLoaded = true;
}

export async function loadSettings() {
  state.settings = await api.get<Settings>("/settings");
}

export async function loadDrivers() {
  if (!state.drivers.length) state.drivers = await api.get<DriverInfo[]>("/drivers");
}

export async function loadEntities(force = false) {
  if (state.entities && !force) return;
  try {
    state.entities = await api.get<HaEntities>("/ha/entities");
  } catch {
    state.entities = { switches: [], cameras: [], power_sensors: [], notify: [] };
  }
}

/** Jobs that need the user: awaiting confirmation or needing attention. */
export async function loadAttention() {
  const now = new Date();
  const from = new Date(now.getTime() - 7 * 86_400_000).toISOString();
  const to = new Date(now.getTime() + 2 * 86_400_000).toISOString();
  const jobs = await api.get<Job[]>(`/jobs?start=${encodeURIComponent(from)}&end=${encodeURIComponent(to)}`);
  state.attention = jobs.filter((j) => j.status === "awaiting_confirmation" || j.status === "needs_attention");
  await loadPending().catch(() => undefined);
}

/** How many imported files wait to be filed (badge on "Bibliothèque"). */
export async function loadPending() {
  state.pending = (await api.get<unknown[]>("/pending")).length;
}

// ---- polling ----------------------------------------------------------------

/** Calls fn now and every `ms` while the component is mounted and the tab visible. */
export function usePolling(fn: () => Promise<unknown>, ms = 5000) {
  let timer: number | undefined;
  const tick = async () => {
    if (document.visibilityState === "visible") {
      try {
        await fn();
      } catch {
        /* the next tick will retry */
      }
    }
    timer = window.setTimeout(tick, ms);
  };
  onMounted(tick);
  onBeforeUnmount(() => window.clearTimeout(timer));
}

// ---- upload -------------------------------------------------------------------

export interface UploadResult {
  imported: {
    filename: string;
    model_id: number | null;
    model_name: string | null;
    kind: string; // sliced | source | pending
    duplicate: boolean;
    pending_id?: number | null;
  }[];
  errors: { filename: string; error: string }[];
}

export const uploading = ref(false);
export const uploadProgress = ref(0); // 0..1 across the whole batch
export const libraryVersion = ref(0); // bumped after each import so views refetch

// Home Assistant's Ingress proxy rejects large request bodies (error 413):
// files are sent in pieces and reassembled by Printy.
const CHUNK = 4 * 1024 * 1024;

async function sendFile(file: File, modelId: number | undefined, onBytes: (n: number) => void): Promise<UploadResult> {
  // crypto.randomUUID needs HTTPS; getRandomValues works on http://<ip>:8123 too
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  const total = Math.max(1, Math.ceil(file.size / CHUNK));
  let res: UploadResult | { received: number } = { received: 0 };
  for (let i = 0; i < total; i++) {
    const piece = file.slice(i * CHUNK, (i + 1) * CHUNK);
    const params = new URLSearchParams({ upload_id: id, index: String(i), total: String(total), filename: file.name });
    if (modelId) params.set("model_id", String(modelId));
    res = await api.post(`/upload/chunk?${params}`, piece);
    onBytes(piece.size);
  }
  return res as UploadResult;
}

export async function uploadFiles(files: File[], modelId?: number): Promise<UploadResult | undefined> {
  if (!files.length) return;
  uploading.value = true;
  uploadProgress.value = 0;
  const totalBytes = files.reduce((n, f) => n + f.size, 0) || 1;
  let sent = 0;
  const res: UploadResult = { imported: [], errors: [] };
  try {
    for (const f of files) {
      try {
        const r = await sendFile(f, modelId, (n) => {
          sent += n;
          uploadProgress.value = sent / totalBytes;
        });
        res.imported.push(...r.imported);
        res.errors.push(...r.errors);
      } catch (e) {
        res.errors.push({ filename: f.name, error: e instanceof ApiError ? e.message : "envoi impossible" });
      }
    }
    if (res.imported.length) libraryVersion.value++;
    for (const e of res.errors) toast(`${e.filename} : ${e.error}`, "error");
    const fresh = res.imported.filter((i) => !i.duplicate);
    const dupes = res.imported.length - fresh.length;
    const waiting = fresh.filter((i) => i.kind === "pending").length;
    const filed = fresh.filter((i) => i.kind !== "pending");
    if (filed.length === 1) {
      const f = filed[0];
      toast(f.kind === "source" ? `Source ajoutée à « ${f.model_name} ».` : `Ajouté à « ${f.model_name} ».`);
    } else if (filed.length > 1) toast(`${filed.length} fichiers ajoutés à la bibliothèque.`);
    if (waiting) {
      toast(waiting === 1 ? "Nouveau fichier à trier dans la bibliothèque." : `${waiting} fichiers à trier dans la bibliothèque.`);
      loadPending().catch(() => undefined);
    }
    if (dupes) toast(dupes === 1 ? "Ce fichier était déjà dans la bibliothèque." : `${dupes} fichiers étaient déjà dans la bibliothèque.`);
    return res;
  } finally {
    uploading.value = false;
  }
}
