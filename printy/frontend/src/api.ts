// Relative URLs: the app lives under Home Assistant’s Ingress prefix.
const BASE = "api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body instanceof Blob) {
    init.body = body;
    (init.headers as Record<string, string>)["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError("Printy ne répond pas. Vérifie que l’add-on tourne.", 0);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Valeur invalide."
          : res.status === 413
            ? "Fichier refusé car trop volumineux pour Home Assistant."
            : `Erreur ${res.status}`;
    throw new ApiError(message, res.status);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
};

export const urls = {
  thumbnail: (slicedId: number) => `${BASE}/sliced/${slicedId}/thumbnail`,
  downloadSliced: (id: number) => `${BASE}/sliced/${id}/download`,
  downloadSource: (id: number) => `${BASE}/sources/${id}/download`,
  printerSnapshot: (id: number) => `${BASE}/printers/${id}/snapshot?t=${Date.now()}`,
  jobSnapshot: (id: number) => `${BASE}/jobs/${id}/snapshot?t=${Date.now()}`,
};
