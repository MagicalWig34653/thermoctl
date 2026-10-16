// Antwortformen von `/kiosk-api` (thermoctl/web/kiosk_api.py). Temperaturen bleiben
// Zeichenketten wie überall in der Oberfläche; Zeitangaben sind ISO-8601 mit Zeitzone.

export interface KioskActions {
  setpoint: boolean;
  boost: boolean;
  cancel_override: boolean;
}

export interface KioskRoom {
  id: number;
  name: string;
  mode_label: string;
  temperature: string | null;
  measured_at: string | null;
  /** Nur gesetzt, wenn der Messwert nicht in Ordnung ist. */
  sensor: { code: string; label: string } | null;
  sensor_stuck: boolean;
  setpoint: { temperature: string; reason: string };
  would_heat: boolean | null;
  override: { ends_at: string | null; temperature: string | null } | null;
  emergency: { headline: string; detail: string } | null;
  actions: KioskActions;
}

export interface KioskState {
  now: string;
  timezone: string;
  rooms: KioskRoom[];
}
