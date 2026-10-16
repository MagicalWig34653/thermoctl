// Server-Vertrag der Auswertung (thermoctl/web/ui_api_reports.py). Temperaturen sind
// Decimal-Strings, Zeitpunkte ISO-8601 mit Zeitzone. Hier steht nichts, was der Server nicht liefert.

export interface Bucket {
  start: string;
  /** 0: keine Zeile im Fach -- eine Lücke, alle Werte sind dann `null`. */
  rows: number;
  measurements: number;
  temperature_c: string | null;
  temperature_min_c: string | null;
  temperature_max_c: string | null;
  scheduled_min_c: string | null;
  scheduled_max_c: string | null;
  effective_min_c: string | null;
  effective_max_c: string | null;
  heat_percent: number | null;
  /** `null`: unbekannt (vor der Aufzeichnung), `"0"`: belegt keine Absenkung. */
  solar_setback_k: string | null;
}

export interface Gap {
  start: string;
  end: string;
}

export interface History {
  zone_id: number;
  zone_name: string;
  timezone: string;
  start: string;
  end: string;
  resolution_seconds: number;
  rows: number;
  units: { temperature: string; setback: string; heat: string };
  limits: {
    max_span_days: number;
    max_buckets: number;
    min_resolution_seconds: number;
    max_rows: number;
    gap_threshold_seconds: number;
  };
  buckets: Bucket[];
  gaps: Gap[];
}

export interface HeatingTime {
  timezone: string;
  period: string;
  control_armed: boolean;
  zones: { zone_id: number; zone_name: string; seconds_total: number; days: { day: string; seconds: number }[] }[];
}

export interface RelayWear {
  timezone: string;
  period: string;
  assumed_lifetime_operations: number;
  devices: {
    zone_id: number;
    zone_name: string;
    device_name: string;
    operations_total: number;
    annual_projection: number;
    lifetime_percent_per_year: number;
    lifetime_years: number | null;
    wear_level: string;
    days: { day: string; operations: number }[];
  }[];
}

export interface Named {
  code: string;
  label: string;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  source: Named;
  actor: string | null;
  action: string;
  object_type: string;
  object_id: string | null;
  summary: string;
  detail: string | null;
}

export interface Audit {
  timezone: string;
  page: number;
  page_size: number;
  has_more: boolean;
  entries: AuditEntry[];
  sources: Named[];
  actions: string[];
  zones: { id: number; name: string }[];
}

export interface ChangeField {
  field: string;
  before: string | null;
  after: string | null;
}

export interface Change {
  id: number;
  occurred_at: string;
  source: string;
  user_name: string | null;
  token_name: string | null;
  change_type: string;
  zone_id: number | null;
  zone_name: string | null;
  object_ref: string | null;
  summary: string;
  revertible: boolean;
  not_revertible_reason: string | null;
  reverted_at: string | null;
  reverted_by_id: number | null;
  reverts_id: number | null;
  forced: boolean;
  fields: ChangeField[];
}

export interface Changes {
  timezone: string;
  page: number;
  page_size: number;
  has_more: boolean;
  entries: Change[];
  change_types: string[];
  sources: Named[];
}

export interface RevertResult {
  outcome: 'reverted' | 'already_effective';
  change: Change;
}

export interface RevertConflict {
  field: string;
  current: string | null;
  expected: string | null;
}
