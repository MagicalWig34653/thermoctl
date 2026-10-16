// Server-Vertrag von `/ui-api/schedule` (thermoctl/web/ui_api_schedule.py). Zeiten sind Minuten
// der lokalen Woche (Wochentag 1 = Montag … 7 = Sonntag, Minute 0..1440), Temperaturen
// Decimal-Strings. Hier steht nichts, was der Server nicht liefert; Regeln liegen dort.

export interface PlanPoint {
  id: number;
  weekday: number;
  minute_of_day: number;
  mode_id: number;
}

export interface PlanSegment {
  weekday: number;
  start_minute: number;
  end_minute: number;
  mode_id: number;
  mode_name: string;
  /** Schaltpunkt, der den Balken beginnt; `null` für den Rest eines Blocks, der von davor herüberreicht. */
  point_id: number | null;
  temperature_c: string | null;
  warmth: number;
}

export interface PlanMode {
  id: number;
  code: string;
  name: string;
  temperature_c: string | null;
  warmth: number;
}

export interface CopyTarget {
  id: number;
  name: string;
  version: string;
  has_plan: boolean;
}

export interface Plan {
  zone_id: number;
  zone_name: string;
  timezone: string;
  version: string;
  points: PlanPoint[];
  segments: PlanSegment[];
  modes: PlanMode[];
  can_edit: boolean;
  full_editor: boolean;
  copy_targets: CopyTarget[];
}

/** Antwort auf eine Schreibabsicht: der neue Stand und die Rücknahme-Marke(n). */
export interface PlanChange extends Plan {
  changed: boolean;
  /** Marke für den nächsten Rücknahme-Schritt; `null`, wenn es nichts (mehr) zurückzunehmen gibt. */
  undo_token: string | null;
}

export interface ForecastSegment {
  starts_at: string;
  ends_at: string;
  local_starts_at: string;
  duration_minutes: number;
  temperature_c: string;
  reason: string;
  mode_id: number | null;
  mode_name: string | null;
  warmth: number;
}

export interface Preview {
  zone_id: number;
  timezone: string;
  generated_at: string;
  horizon_hours: number;
  segments: ForecastSegment[];
}

export interface Vacation {
  id: number;
  starts_at: string;
  ends_at: string;
  start_date: string;
  end_date: string;
  setback_temperature_c: string;
  running: boolean;
}

export interface VacationState {
  timezone: string;
  can_manage: boolean;
  vacation: Vacation | null;
}

export interface AbsenceInfo {
  id: number;
  starts_at: string;
  ends_at: string;
  setback_temperature_c: string;
  zones: CopyTarget[];
}

export interface AbsenceState {
  timezone: string;
  can_start: boolean;
  eligible_zone_ids: number[];
  absence: AbsenceInfo | null;
}

export interface ModeAdmin {
  id: number;
  code: string;
  name: string;
  sort_order: number;
  is_builtin: boolean;
  delete_lock: string | null;
}

export interface Modes {
  modes: ModeAdmin[];
}
