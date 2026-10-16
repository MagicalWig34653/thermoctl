// Server-Vertrag von `/ui-api/rooms` (thermoctl/web/ui_api_rooms.py). Temperaturen sind
// Decimal-Strings, Zeitpunkte ISO-8601 mit Zeitzone. Hier steht nichts, was der Server
// nicht liefert; abgeleitet wird ausschließlich in `logic.ts`.

export interface Named {
  code: string;
  label: string;
}

export interface Sensor {
  status_code: string;
  status_label: string;
  stuck: boolean;
  window_open: boolean | null;
  window_open_by_temperature: boolean;
  window_alarm: boolean;
}

export interface Setpoint {
  temperature_c: string;
  reason: string;
  mode_id: number | null;
  mode_name: string | null;
}

export interface Override {
  id: number;
  temperature_c: string | null;
  starts_at: string;
  ends_at: string | null;
}

export interface Decision {
  would_heat: boolean;
  outcome_code: string;
  reason: string;
  decided_at: string;
  planned_setpoint_c: string | null;
}

export interface Emergency {
  stage: string;
  headline: string;
  detail: string;
}

export interface DaySegment {
  start_minute: number;
  end_minute: number;
  mode_name: string;
  temperature_c: string | null;
  warmth: number;
}

export interface Actions {
  override: boolean;
  cancel_override: boolean;
  edit_setpoint_step: boolean;
  edit_mode_setpoints: boolean;
  jump_next: boolean;
  set_operating_mode: boolean;
}

export interface Room {
  id: number;
  name: string;
  operating_mode: Named;
  temperature_c: string | null;
  measured_at: string | null;
  measured_age_seconds: number | null;
  /** Der Server hält den Messwert nicht mehr für frisch (dieselbe Schwelle wie die Regelung). */
  measurement_stale: boolean;
  sensor: Sensor;
  setpoint: Setpoint;
  override: Override | null;
  decision: Decision | null;
  emergency: Emergency | null;
  plan_applies: boolean;
  day_track: DaySegment[];
  actions: Actions;
}

export type ActuationState = 'dry_run' | 'armed_restart_missing' | 'live';

export interface Actuation {
  control_armed: boolean;
  sending_allowed: boolean;
  cluster_standby: boolean;
  state: ActuationState;
}

/** Anlagenweite Außentemperatur; `null` in der Wohnungssicht. `temperature_c` nur bei `ok`. */
export interface Outdoor {
  status: 'ok' | 'veraltet' | 'keine_quelle' | string;
  temperature_c: string | null;
  measured_at: string | null;
}

export interface Rooms {
  timezone: string;
  poll_interval_seconds: number;
  now_fraction: number;
  actuation: Actuation;
  outdoor: Outdoor | null;
  rooms: Room[];
}

export interface ModeSetpoint {
  mode_id: number;
  code: string;
  name: string;
  temperature_c: string | null;
  is_current: boolean;
}

export interface NextSwitch {
  at: string;
  temperature_c: string;
  reason: string;
  mode_id: number | null;
  mode_name: string | null;
}

export interface Vacation {
  starts_at: string;
  ends_at: string;
  setback_temperature_c: string;
  running: boolean;
}

export interface RoomDetail extends Room {
  minimum_temperature_c: string;
  maximum_temperature_c: string;
  modes: ModeSetpoint[];
  operating_modes: Named[];
  next_switch: NextSwitch | null;
  vacation: Vacation | null;
}

export type OverrideEnd = 'next_switch' | 'duration' | 'permanent';
