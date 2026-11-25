export interface SlicedFile {
  id: number;
  model_id: number;
  filename: string;
  size: number;
  has_thumbnail: boolean;
  printer_model: string | null;
  duration_s: number | null;
  filament_g: number | null;
  filament_m: number | null;
  material: string | null;
  color: string | null;
  layer_height: number | null;
  nozzle: number | null;
  pieces: number;
  created_at: string;
}

export interface SourceFile {
  id: number;
  filename: string;
  size: number;
  created_at: string;
}

export interface ModelSummary {
  id: number;
  name: string;
  description: string;
  tags: string[];
  cover_id: number | null;
  sliced_count: number;
  source_count: number;
  duration_s: number | null;
  filament_g: number | null;
  colors: string[];
  materials: string[];
  updated_at: string;
  stock_alert: number | null;
}

export interface Energy {
  kwh_per_piece: number | null;
  measured_on: number;
}

export interface StockVariant {
  variant: string;
  color: string | null;
  quantity: number;
}

export interface Product extends ModelSummary {
  stock: StockVariant[];
  stock_total: number;
  stock_low: boolean;
  energy: Energy;
}

export interface StockMove {
  id: number;
  model_id: number;
  model_name: string;
  variant: string;
  delta: number;
  reason: "print" | "sale" | "adjust" | "defect";
  job_id: number | null;
  note: string;
  at: string;
}

export interface ModelFull extends ModelSummary {
  sliced: SlicedFile[];
  sources: SourceFile[];
}

export type PrinterState = "offline" | "idle" | "printing" | "paused" | "finished" | "error" | "busy";

export interface PrinterLive {
  state: PrinterState;
  nozzle_temp: number | null;
  bed_temp: number | null;
  progress: number | null;
  file: string | null;
  remaining_s: number | null;
  message: string;
  power_on: boolean | null;
  power_w: number | null;
  spool_tracked?: boolean;
  elapsed_s?: number | null;
  print_started_at?: string | null;
  print_ends_at?: string | null;
  job_id?: number | null;
}

export interface Printer {
  id: number;
  name: string;
  driver: string;
  driver_label: string;
  host: string;
  options: Record<string, unknown>;
  switch_entity: string | null;
  power_entity: string | null;
  camera_source: "printer" | "ha" | "none";
  camera_entity: string | null;
  power_on_lead_min: number;
  cooldown_temp: number;
  bed_clear: boolean;
  piece_on_bed: boolean;
  loaded_spool_id: number | null;
  sort_order: number;
  live: PrinterLive;
}

export interface OptionField {
  key: string;
  label: string;
  kind: "text" | "password" | "number";
  default: unknown;
  help: string;
  required: boolean;
}

export interface DriverInfo {
  key: string;
  label: string;
  host_label: string;
  host_required: boolean;
  has_camera: boolean;
  options: OptionField[];
  note: string;
}

export type JobStatus =
  | "scheduled"
  | "powering_on"
  | "awaiting_confirmation"
  | "ready"
  | "starting"
  | "printing"
  | "paused"
  | "finished"
  | "failed"
  | "cancelled"
  | "needs_attention";

export interface JobEvent {
  at: string;
  kind: string;
  message: string;
}

export interface Job {
  id: number;
  status: JobStatus;
  printer_id: number;
  sliced_file_id: number;
  model_id: number;
  model_name: string;
  has_thumbnail: boolean;
  color: string | null;
  material: string | null;
  filament_g: number | null;
  start_at: string;
  end_at: string;
  duration_s: number;
  confirm_required: boolean;
  confirmed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  note: string;
  progress?: number | null;
  remaining_s?: number | null;
  events?: JobEvent[];
  spool_id: number | null;
  filament_used_g: number | null;
  spool_change?: boolean;
  previous_spool_id?: number | null;
  low_filament?: boolean;
  spool_remaining_g?: number | null;
  waits_for_print?: boolean;
  energy_wh: number | null;
  stock_added: number;
}

export interface Spool {
  id: number;
  name: string;
  vendor: string | null;
  material: string | null;
  color: string | null;
  remaining_g: number | null;
  location: string | null;
  label: string;
  match: boolean;
  filament_id?: number | null;
  initial_g?: number | null;
  loaded_on?: string | null;
  planned_g?: number;
}

export interface SpoolList {
  enabled: boolean;
  error: string | null;
  spools: Spool[];
  suggested_id?: number | null;
  url?: string;
}

export interface OffPeakRange {
  start: string;
  end: string;
}

export interface Settings {
  notify_targets: string[];
  confirm_by_default: boolean;
  confirm_lead_min: number;
  confirm_timeout_min: number;
  keep_on_window_min: number;
  off_peak: OffPeakRange[];
  import_octoprint: boolean;
  import_watch_folder: boolean;
  octoprint_api_key: string;
  spoolman_url: string;
  spool_reminder_finish: boolean;
  spool_reminder_morning: string | null;
  stock_auto: boolean;
  ha_entities: boolean;
  env: {
    home_assistant: boolean;
    inbox_dir: string;
    timezone: string;
    entities: { mode: "mqtt" | "rest" | "off"; count: number; last_ok: number | null; error: string };
    errors: Record<string, { error: string; at: string }>;
  };
}

export interface EntityOption {
  id: string;
  name: string;
}

export interface HaEntities {
  switches: EntityOption[];
  cameras: EntityOption[];
  power_sensors: EntityOption[];
  notify: EntityOption[];
}
