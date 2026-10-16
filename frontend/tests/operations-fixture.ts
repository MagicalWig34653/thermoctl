import type { OperationActuation, RoomParameters, Settings } from '../src/lib/operations/types.ts';

const limit = (label: string, unit: string | null = null) => ({ minimum: '0', maximum: '100', step: '1', unit, label });

export function parameters(over: Partial<RoomParameters> = {}): RoomParameters {
  const ctrl = (own: string | null, effective: string, source: 'zone' | 'plant' | 'default' = 'plant') => ({
    own,
    effective,
    inherited: own === null,
    source: own === null ? source : ('zone' as const),
    limit: limit('Wert', 'K'),
  });
  return {
    zone_id: 1,
    name: 'Wohnzimmer',
    control: {
      hysteresis_k: ctrl(null, '0.5'),
      min_on_seconds: ctrl('300', '300'),
      min_off_seconds: ctrl(null, '300'),
      sensor_timeout_seconds: ctrl(null, '1800'),
      temperature_offset_k: ctrl(null, '0.0', 'default'),
      window_resume_delay_seconds: ctrl(null, '120'),
      solar_setback_max_k: ctrl(null, '1.0'),
    },
    solar_gain_factor: '0.50',
    valve_protection: { enabled: false, interval_days: 7, duration_minutes: 5 },
    pi: {
      enabled: false,
      gain_per_k: '1.50',
      integral_time_minutes: 30,
      min_on_seconds: 120,
      min_off_seconds: 120,
      eligible: true,
      eligibility_reason: 'Alle Aktoren sind schaltbar.',
      limits: {},
    },
    window_temp_drop_detection_enabled: false,
    sensor_failure: {
      own_enabled: true,
      own_profile_id: null,
      own_emergency_setpoint_c: null,
      effective_profile_id: 1,
      effective_profile_name: 'Standard',
      profile_source: 'plant',
      effective_emergency_setpoint_c: '18.0',
      setpoint_source: 'plant',
      profiles: [{ id: 1, name: 'Standard' }],
    },
    backup_offsets: [{ assignment_id: 9, device_name: 'Ersatzfühler', offset_k: '0.3' }],
    assumed_relay_lifetime_operations: 100000,
    ...over,
  };
}

export function actuation(over: Partial<OperationActuation> = {}): OperationActuation {
  return {
    armed: false,
    sending_allowed: false,
    cluster_standby: false,
    live: false,
    dry_run: true,
    restart_required: false,
    state: 'dry_run',
    ...over,
  };
}

export const ARMED_NO_RESTART = actuation({ armed: true, restart_required: true, state: 'armed_restart_missing' });
export const LIVE = actuation({ armed: true, sending_allowed: true, live: true, dry_run: false, state: 'live' });

export function settings(over: Partial<Settings> = {}): Settings {
  const field = (name: string, value: string, integer = false) => ({ name, label: name, value, minimum: '0', maximum: '9999', integer });
  return {
    may_edit: true,
    defaults: [
      field('default_hysteresis_k', '0.5'),
      field('default_min_on_seconds', '300', true),
      field('default_solar_setback_max_k', '1.0'),
      field('solar_setback_lookahead_hours', '3', true),
    ],
    timezone: 'Europe/Berlin',
    solar: { enabled: false, latitude: null, longitude: null },
    window_alarm: [field('window_alarm_open_minutes', '30', true)],
    window_temp_drop: [],
    sensor_failure: {
      profile_id: 1,
      profile_name: 'Standard',
      fixed_on_seconds: 60,
      fixed_off_seconds: 300,
      recovery_seconds: 600,
      recovery_samples: 3,
      warm_restart_hysteresis_k: '0.5',
      curve_points: [{ outdoor_c: '-5.0', on_seconds: 120, off_seconds: 240 }],
      emergency_setpoint_c: '18.0',
    },
    outdoor: { source_device_id: null, source_device_name: null, reading: { status: 'keine_quelle', temperature_c: null, measured_at: null }, devices: [] },
    notifications: {
      sensor_faults: true, bridge_faults: true, command_failures: true, stuck_sensor: false, window_alarm: false, tenant_reports: false,
      last_attempt_at: null, last_ok: null, last_error: null, webhook_configured: true,
    },
    ...over,
  };
}
