// Antworten und Eingaben der Regelungs-, Betriebs- und Einstellungsschnittstelle
// (`thermoctl/web/ui_api_operations.py`). Zahlen sind Zeichenketten (Decimal), nie `number`.

// -- Raumtechnik -----------------------------------------------------------------------------

export interface ParameterLimit {
  minimum: string;
  maximum: string;
  step: string;
  unit: string | null;
  label: string;
}

export type ParameterSource = 'zone' | 'plant' | 'default';

export interface Parameter {
  /** Eigener Wert der Zone; `null`, wenn sie erbt. */
  own: string | null;
  effective: string;
  inherited: boolean;
  source: ParameterSource;
  limit: ParameterLimit;
}

export interface PiParameters {
  enabled: boolean;
  gain_per_k: string;
  integral_time_minutes: number;
  min_on_seconds: number;
  min_off_seconds: number;
  eligible: boolean;
  eligibility_reason: string;
  limits: Record<string, ParameterLimit>;
}

export interface ProfileOption {
  id: number;
  name: string;
}

export interface ZoneSensorFailure {
  own_enabled: boolean;
  own_profile_id: number | null;
  own_emergency_setpoint_c: string | null;
  effective_profile_id: number;
  effective_profile_name: string;
  profile_source: string;
  effective_emergency_setpoint_c: string;
  setpoint_source: string;
  profiles: ProfileOption[];
}

export interface BackupOffset {
  assignment_id: number;
  device_name: string;
  offset_k: string;
}

export type ControlFieldName =
  | 'hysteresis_k'
  | 'min_on_seconds'
  | 'min_off_seconds'
  | 'sensor_timeout_seconds'
  | 'temperature_offset_k'
  | 'window_resume_delay_seconds'
  | 'solar_setback_max_k';

export interface RoomParameters {
  zone_id: number;
  name: string;
  control: Record<string, Parameter>;
  solar_gain_factor: string;
  valve_protection: { enabled: boolean; interval_days: number; duration_minutes: number };
  pi: PiParameters;
  window_temp_drop_detection_enabled: boolean;
  sensor_failure: ZoneSensorFailure;
  /** `null`: dieser Zugang darf die Ausgleichswerte nicht sehen (`device.manage`). */
  backup_offsets: BackupOffset[] | null;
  assumed_relay_lifetime_operations: number;
}

/** Teil-Update: nur gesendete Felder ändern etwas; bei vererbbaren Feldern heißt `null` „erben“. */
export interface ParametersPatch {
  hysteresis_k?: string | null;
  min_on_seconds?: string | null;
  min_off_seconds?: string | null;
  sensor_timeout_seconds?: string | null;
  temperature_offset_k?: string | null;
  window_resume_delay_seconds?: string | null;
  solar_setback_max_k?: string | null;
  solar_gain_factor?: string;
  valve_protection_enabled?: boolean;
  valve_protection_interval_days?: string;
  valve_protection_duration_minutes?: string;
  pi_enabled?: boolean;
  pi_confirm?: boolean;
  pi_gain_per_k?: string;
  pi_integral_time_minutes?: string;
  pi_min_on_seconds?: string;
  pi_min_off_seconds?: string;
  window_temp_drop_detection_enabled?: boolean;
  sensor_failure_enabled?: boolean;
  sensor_failure_profile_id?: number | null;
  sensor_failure_emergency_setpoint_c?: string | null;
  backup_offsets?: { assignment_id: number; offset_k: string | null }[];
}

// -- Betrieb ---------------------------------------------------------------------------------

export type OperationState = 'dry_run' | 'armed_restart_missing' | 'live';

export interface OperationActuation {
  armed: boolean;
  sending_allowed: boolean;
  cluster_standby: boolean;
  live: boolean;
  dry_run: boolean;
  restart_required: boolean;
  state: OperationState;
}

export interface ClusterInfo {
  instance_id: string;
  standby: boolean;
  takeover_timeout_seconds: number;
}

export interface DecisionDetail {
  decided_at: string;
  would_heat: boolean;
  previous_would_heat: boolean | null;
  outcome_code: string;
  reason: string;
  temperature_c: string | null;
  setpoint_c: string | null;
  setpoint_reason: string;
  planned_setpoint_c: string | null;
  solar_setback_k: string | null;
  requested_controller: string;
  effective_controller: string;
  controller_fallback_reason: string | null;
  controller_fallback_label: string | null;
}

export interface EmergencyActuator {
  device_id: number;
  device_name: string;
  kind: string;
  phase: string | null;
  phase_deadline_at: string | null;
  on_seconds: number | null;
  off_seconds: number | null;
  cycle_source: string | null;
  handover_status_text: string;
  restore_status_text: string;
}

export interface EmergencyComparison {
  device_id: number;
  device_name: string;
  sample_count: number;
  mean_deviation_k: string | null;
  suggested_offset_text: string | null;
}

export interface EmergencyDetail {
  stage: string;
  stage_label: string;
  failure_started_at: string | null;
  active_source_device_name: string | null;
  sensor_timeout_seconds: number | null;
  recovery_sample_count: number;
  recovery_samples: number | null;
  emergency_setpoint_c: string | null;
  outdoor_status_text: string | null;
  outdoor_temperature_c: string | null;
  actuators: EmergencyActuator[];
  comparisons: EmergencyComparison[];
}

export interface OperationRoom {
  id: number;
  name: string;
  temperature_c: string | null;
  setpoint_c: string;
  setpoint_reason: string;
  decision: DecisionDetail | null;
  emergency: EmergencyDetail;
}

export interface Operation {
  actuation: OperationActuation;
  cluster: ClusterInfo;
  may_arm: boolean;
  rooms: OperationRoom[];
}

export interface ArmedResult {
  changed: boolean;
  actuation: OperationActuation;
}

// -- Einstellungen ---------------------------------------------------------------------------

export interface NumberField {
  name: string;
  label: string;
  value: string;
  minimum: string;
  maximum: string;
  integer: boolean;
}

export interface CurvePoint {
  outdoor_c: string;
  on_seconds: number;
  off_seconds: number;
}

export interface SensorFailureDefaults {
  profile_id: number;
  profile_name: string;
  fixed_on_seconds: number;
  fixed_off_seconds: number;
  recovery_seconds: number;
  recovery_samples: number;
  warm_restart_hysteresis_k: string;
  curve_points: CurvePoint[];
  emergency_setpoint_c: string;
}

export interface DeviceOption {
  id: number;
  name: string;
}

export interface Outdoor {
  source_device_id: number | null;
  source_device_name: string | null;
  reading: { status: string; temperature_c: string | null; measured_at: string | null };
  devices: DeviceOption[];
}

export interface Notifications {
  sensor_faults: boolean;
  bridge_faults: boolean;
  command_failures: boolean;
  stuck_sensor: boolean;
  window_alarm: boolean;
  tenant_reports: boolean;
  last_attempt_at: string | null;
  last_ok: boolean | null;
  last_error: string | null;
  webhook_configured: boolean;
}

export type NotificationKey =
  | 'sensor_faults'
  | 'bridge_faults'
  | 'command_failures'
  | 'stuck_sensor'
  | 'window_alarm'
  | 'tenant_reports';

export interface Settings {
  may_edit: boolean;
  defaults: NumberField[];
  timezone: string;
  solar: { enabled: boolean; latitude: string | null; longitude: string | null };
  window_alarm: NumberField[];
  window_temp_drop: NumberField[];
  sensor_failure: SensorFailureDefaults;
  outdoor: Outdoor;
  notifications: Notifications;
}

export interface NotificationTest {
  ok: boolean;
  status_code: number | null;
  duration_seconds: number;
  error: string | null;
}

// -- Schnittstellen --------------------------------------------------------------------------

export interface InterfaceDetail {
  name: string;
  value: string;
  source: string;
  variable: string | null;
}

export interface InterfaceInfo {
  key: string;
  name: string;
  purpose: string;
  state: string;
  finding: string;
  details: InterfaceDetail[];
  hint: string | null;
  more: { label: string; path: string } | null;
}

export interface Interfaces {
  interfaces: InterfaceInfo[];
  homebridge_zones: { zone_id: number; zone_name: string; config_json: string }[];
  homebridge_broker_configured: boolean;
}
