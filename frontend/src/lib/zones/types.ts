// Formen der Raum-, Melde- und Glossarschnittstelle (`/ui-api/room-form`, `/ui-api/rooms`,
// `/ui-api/report-options`, `/ui-api/glossary`). Spiegel von `thermoctl/web/ui_api_zones.py`.

export interface NamedOption {
  code: string;
  label: string;
}

export interface RoomForm {
  id: number;
  name: string;
  display_name: string;
  sort_order: number;
  operating_mode: string;
  temperature_source_device_id: number | null;
}

export interface RoomFormOptions {
  operating_modes: NamedOption[];
  devices: { id: number; name: string }[];
  name_limit: number;
  display_name_limit: number;
  /** `null` beim Anlegen. */
  room: RoomForm | null;
}

/** Eingaben des Formulars als Text, wie sie im Feld stehen. */
export interface RoomValues {
  name: string;
  display_name: string;
  operating_mode: string;
  sort_order: string;
}

/** `GET /ui-api/room-orders`: Sortierwert je sichtbarem Raum. */
export interface RoomOrders {
  rooms: { id: number; sort_order: number }[];
}

export interface RoomPatch {
  name?: string;
  display_name?: string;
  operating_mode?: string;
  sort_order?: number;
}

export interface RoomCreateBody {
  name: string;
  display_name: string;
  operating_mode: string;
  sort_order: number;
}

export interface DeleteImpact {
  id: number;
  name: string;
  display_name: string;
  schedule_points: number;
  devices: number;
  setpoints: number;
  overrides: number;
  shadow_decisions: number;
}

export interface Deleted {
  id: number;
  deleted: boolean;
}

export interface ReportOptions {
  kinds: NamedOption[];
  note_limit: number;
  rooms: { id: number; name: string }[];
}

export interface ReportResult {
  message: string;
}

export interface GlossaryEntry {
  id: string;
  term: string;
  short: string;
  explanation: string;
  area: string;
  letter: string;
  synonyms: string[];
  see_also: string[];
}

export interface Glossary {
  areas: string[];
  letters: string[];
  entries: GlossaryEntry[];
}
