import { ref } from "vue";

// Appearance is a per-device preference (the same Printy may be opened from a
// light desktop and a dark phone), so it lives in this browser only.
export type Theme = "auto" | "light" | "dark";
const KEY = "printy-theme";

function read(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export const theme = ref<Theme>(read());

export function applyTheme(next: Theme = theme.value) {
  theme.value = next;
  const root = document.documentElement;
  if (next === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", next);
  try {
    if (next === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, next);
  } catch {
    /* private mode: the choice lasts for this visit */
  }
}
