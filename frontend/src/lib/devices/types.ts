// Antworten der Geräte-Schnittstelle (`/ui-api`, thermoctl/web/ui_api_devices.py).
// Nur Form der Daten; Regeln (was verbindbar ist, welche Betriebsart aus den Linien folgt)
// stehen ausschließlich im Server.

export interface DeviceFinding {
  kind: string;
  text: string;
}

export interface SurveyDevice {
  id: number;
  name: string;
  model: string | null;
  integration: string | null;
  is_group: boolean;
  capabilities: string[];
  quiet_capabilities: string[];
  zones: string[];
  last_heard: string | null;
  battery_percent: string | null;
  radio_quality: number | null;
  is_fine: boolean;
  severity: string | number | null;
  findings: DeviceFinding[];
}

export interface Survey {
  timezone: string | null;
  without_zone: number;
  devices: SurveyDevice[];
}

// -- Zuordnungskarte ----------------------------------------------------------------------

export type Direction = 'in' | 'out';

export interface MapPort {
  id: string;
  node_id: string;
  direction: Direction;
  kind: string | null;
  label: string;
  tooltip: string;
  capability: string | null;
  accepts: string[];
  accepts_text: string;
  zone_id: number | null;
  slot: string | null;
  device_id: number | null;
  property_name: string | null;
  technical: boolean;
  single: boolean;
  blocked_reason: string | null;
  editable: boolean;
  valve_channel: string | null;
  requires_actual: boolean;
  actuator_elsewhere: string[];
}

export interface MapNode {
  id: string;
  kind: 'device' | 'zone';
  name: string;
  ports: MapPort[];
}

export interface MapConnection {
  id: string;
  out_port: string | null;
  in_port: string | null;
  zone_port: string;
  device_id: number;
  device_name: string;
  text: string;
  zone_id: number;
  slot: string;
  editable: boolean;
  from_zone: boolean;
  title: string;
}

export interface ValveMode {
  code: string;
  label: string;
  description: string;
  lines: string[];
}

export interface ValveAssignment {
  assignment_id: number;
  zone_id: number;
  device_id: number;
  mode: string;
  mode_label: string;
}

export interface DeviceMap {
  may_edit: boolean;
  nodes: MapNode[];
  connections: MapConnection[];
  reasons: string[];
  rejections: Record<string, Record<string, number>>;
  same_side: Record<Direction, string>;
  technical_reason: string;
  single_reason: string;
  kind_labels: Record<string, string>;
  valve_modes: ValveMode[];
  valve_assignments: ValveAssignment[];
}

export interface CheckVerdict {
  ok: boolean;
  reason: string | null;
  out_port: string | null;
  in_port: string | null;
}

export interface ConnectResult {
  ok: boolean;
  mode: string | null;
  message?: string | null;
}

// -- Zuordnung je Raum --------------------------------------------------------------------

export interface Named {
  id: number;
  code: string;
  label: string;
}

export interface ZoneAssignment {
  id: number;
  device: { id: number; name: string };
  role: Named;
  sort_order: number;
  thermostat_mode: string | null;
  thermostat_mode_label: string | null;
  is_thermostat: boolean;
}

export interface ThermostatModeInfo {
  code: string;
  label: string;
  description: string;
}

export interface ZoneDevices {
  zone: { id: number; name: string };
  may_edit: boolean;
  temperature_source: { id: number; name: string } | null;
  assignments: ZoneAssignment[];
  devices: { id: number; name: string; capabilities: string[] }[];
  roles: Named[];
  required_capabilities: Record<string, string[]>;
  thermostat_modes: ThermostatModeInfo[];
  picture: { defects: string[] };
  /** Bediengeräte dieses Raums mit gesehenen Tasten (nur Lesen; Schreiben über `zone_button`). */
  controllers: { device: { id: number; name: string }; bindings: Binding[] }[];
  commands: Named[];
}

// -- Anlagenbild --------------------------------------------------------------------------

export interface PlantDevice {
  id: number;
  name: string;
  model: string | null;
  capabilities: string[];
  active: boolean;
  unsuitable: string | null;
  assignment_id: number | null;
}

export interface PlantZone {
  zone_id: number;
  name: string;
  temperature_source: PlantDevice | null;
  window_contacts: PlantDevice[];
  actuators: PlantDevice[];
  controllers: PlantDevice[];
  defects: string[];
}

export interface Plant {
  /** `null`: noch keine Meldung der Brücke. */
  bridge: boolean | null;
  zones: PlantZone[];
  without_zone: PlantDevice[];
}

// -- Datenfluss ---------------------------------------------------------------------------

export interface FlowNode {
  id: string;
  kind: string;
  label: string;
  detail: string | null;
  device_id: number | null;
}

export interface FlowEdge {
  source: string;
  target: string;
  kind: string;
  label: string;
  property_name: string | null;
  dashed: boolean;
  text: string;
}

export interface FlowZone {
  zone: { id: number; name: string };
  without_devices: boolean;
  nodes: FlowNode[];
  edges: FlowEdge[];
  mermaid: string;
}

// -- Bediengeräte -------------------------------------------------------------------------

export interface Binding {
  action: string;
  command_code: string | null;
  command_name: string | null;
  step_k: string | null;
  last_seen: string | null;
}

export interface ControllerProperty {
  id: number;
  name: string;
  value_type: string;
  unit: string | null;
  min_value: string | null;
  max_value: string | null;
  readable: boolean;
  writable: boolean;
  values: string[];
}

export interface ControllerChannel {
  property_name: string;
  direction: string;
  kind: string;
  zone_id: number | null;
  source_device_id: number | null;
  fixed_text: string | null;
  fixed_number: string | null;
}

export interface Controller {
  id: number;
  name: string;
  manageable: boolean;
  zones: { id: number; name: string }[];
  properties: ControllerProperty[];
  channels: ControllerChannel[];
  bindings: Binding[];
}

export interface Controllers {
  controllers: Controller[];
  zones: { id: number; name: string; manageable: boolean }[];
  source_devices: { id: number; name: string }[];
  kinds: Named[];
  commands: Named[];
}

// -- Schaltprotokoll ----------------------------------------------------------------------

export interface CommandEntry {
  id: number | string;
  sent_at: string | null;
  kind: string;
  source: string | null;
  zone_name: string | null;
  device_name: string | null;
  command: string | null;
  payload: string | null;
  outcome: string | null;
  error: string | null;
  reason: string | null;
  simulated: boolean;
}

export interface CommandLog {
  timezone: string | null;
  page: number;
  page_size: number;
  has_more: boolean;
  entries: CommandEntry[];
  zone_names: string[];
  outcomes: { code: string; label: string }[];
  outcome_labels: Record<string, string>;
  source_labels: Record<string, string>;
}
