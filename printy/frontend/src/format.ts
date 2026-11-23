import type { JobStatus, OffPeakRange, PrinterState } from "./types";

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "durée inconnue";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h}h${String(r).padStart(2, "0")}` : `${h}h`;
}

export function grams(g: number | null | undefined): string {
  if (g == null) return "";
  return g >= 1000
    ? `${(g / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} kg`
    : `${Math.round(g)} g`;
}

export function bytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} Ko`;
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} Mo`;
}

const timeFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
const shortDayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric" });

export const time = (d: Date | string) => timeFmt.format(new Date(d));
export const day = (d: Date | string) => dayFmt.format(new Date(d));
export const shortDay = (d: Date | string) => shortDayFmt.format(new Date(d));

export function when(d: Date | string): string {
  const date = new Date(d);
  const today = startOfDay(new Date());
  const diff = Math.round((startOfDay(date).getTime() - today.getTime()) / 86_400_000);
  const prefix = diff === 0 ? "aujourd’hui" : diff === 1 ? "demain" : diff === -1 ? "hier" : day(date);
  return `${prefix} à ${time(date)}`;
}

export function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

export function startOfWeek(d: Date): Date {
  const x = startOfDay(d);
  const dow = (x.getDay() + 6) % 7; // Monday = 0
  x.setDate(x.getDate() - dow);
  return x;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

export function sameDay(a: Date, b: Date): boolean {
  return a.toDateString() === b.toDateString();
}

/** "YYYY-MM-DD" and "HH:MM" in local time, for date and time inputs. */
export function toInputs(d: Date): { date: string; time: string } {
  const p = (n: number) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`,
    time: `${p(d.getHours())}:${p(d.getMinutes())}`,
  };
}

export function fromInputs(date: string, t: string): Date {
  const [y, m, dd] = date.split("-").map(Number);
  const [h, mi] = t.split(":").map(Number);
  return new Date(y, m - 1, dd, h, mi);
}

function minutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/** Off-peak ranges as [startMin, endMin) inside one day (ranges over midnight are split). */
export function offPeakSegments(ranges: OffPeakRange[]): [number, number][] {
  const out: [number, number][] = [];
  for (const r of ranges) {
    const s = minutes(r.start);
    const e = minutes(r.end);
    if (s < e) out.push([s, e]);
    else {
      out.push([s, 1440]);
      if (e > 0) out.push([0, e]);
    }
  }
  return out;
}

/** Share of [start, end) that falls in off-peak hours, 0..1. */
export function offPeakShare(start: Date, end: Date, ranges: OffPeakRange[]): number {
  const segs = offPeakSegments(ranges);
  const total = end.getTime() - start.getTime();
  if (total <= 0 || !segs.length) return 0;
  let inside = 0;
  for (let t = start.getTime(); t < end.getTime(); t += 60_000) {
    const d = new Date(t);
    const m = d.getHours() * 60 + d.getMinutes();
    if (segs.some(([s, e]) => m >= s && m < e)) inside += 60_000;
  }
  return Math.min(1, inside / total);
}

/** Next off-peak start after `from`. */
export function nextOffPeak(from: Date, ranges: OffPeakRange[]): Date | null {
  if (!ranges.length) return null;
  const candidates: Date[] = [];
  for (let i = 0; i < 2; i++) {
    for (const r of ranges) {
      const [h, m] = r.start.split(":").map(Number);
      const d = addDays(startOfDay(from), i);
      d.setHours(h, m);
      if (d > from) candidates.push(d);
    }
  }
  return candidates.sort((a, b) => a.getTime() - b.getTime())[0] ?? null;
}

export const jobStatusLabel: Record<JobStatus, string> = {
  scheduled: "Planifiée",
  powering_on: "Allumage",
  awaiting_confirmation: "Plateau à confirmer",
  ready: "Prête",
  starting: "Lancement",
  printing: "En impression",
  paused: "En pause",
  finished: "Terminée",
  failed: "Échec",
  cancelled: "Annulée",
  needs_attention: "À revoir",
};

export const printerStateLabel: Record<PrinterState, string> = {
  offline: "Éteinte",
  idle: "Prête",
  printing: "En impression",
  paused: "En pause",
  finished: "Impression terminée",
  error: "Erreur",
  busy: "Occupée",
};

export function kwh(v: number | null | undefined): string {
  if (v == null) return "–";
  return `${v.toLocaleString("fr-FR", { maximumFractionDigits: v < 1 ? 3 : 2 })} kWh`;
}

export function temp(t: number | null | undefined): string {
  return t == null ? "–" : `${Math.round(t)} °C`;
}
