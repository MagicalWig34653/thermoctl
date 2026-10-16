// Server-Vertrag von `/ui-api/dashboard` (thermoctl/web/ui_api_dashboard.py,
// thermoctl/db/models/dashboard.py). Gespeichert wird nur die Anordnung; welche Daten eine
// Kachel zeigt, kommt aus den Lese-Endpunkten.

export type TileKind =
  | 'room'
  | 'chart'
  | 'metric'
  | 'agenda'
  | 'schedule'
  | 'heating'
  | 'devices'
  | 'system'
  | 'note'
  | 'shortcut';
export type Metric = 'rooms' | 'heating' | 'issues' | 'outdoor';
export type Destination =
  | 'schedule'
  | 'absence'
  | 'history'
  | 'devices'
  | 'mapping'
  | 'solar'
  | 'operation'
  | 'account';
export type Width = 3 | 4 | 6 | 8 | 12;
export type PeriodDays = 1 | 7 | 30 | 90;

/** Schranken wie der Server (`db/models/dashboard.py`). */
export const MAX_SECTIONS = 12;
export const MAX_TILES_PER_SECTION = 30;
export const MAX_TITLE_LENGTH = 80;
export const MAX_NOTE_LENGTH = 500;

export const TILE_KINDS: readonly TileKind[] = [
  'room',
  'chart',
  'metric',
  'agenda',
  'schedule',
  'heating',
  'devices',
  'system',
  'note',
  'shortcut',
];
export const METRICS: readonly Metric[] = ['rooms', 'heating', 'issues', 'outdoor'];
export const DESTINATIONS: readonly Destination[] = [
  'schedule',
  'absence',
  'history',
  'devices',
  'mapping',
  'solar',
  'operation',
  'account',
];
export const WIDTHS: readonly Width[] = [3, 4, 6, 8, 12];
export const PERIODS_DAYS: readonly PeriodDays[] = [1, 7, 30, 90];

export interface Tile {
  kind: TileKind;
  zone_id: number | null;
  title: string;
  period_days: PeriodDays;
  width: Width;
  metric: Metric | null;
  destination: Destination | null;
  note: string;
}

export interface Section {
  title: string;
  tiles: Tile[];
}

export interface Layout {
  version: number;
  customized: boolean;
  sections: Section[];
}

/** Entwurfsform: Schlüssel nur im Browser (stabile Identität beim Umsortieren), nie gesendet. */
export interface DraftTile extends Tile {
  key: string;
}
export interface DraftSection {
  key: string;
  title: string;
  tiles: DraftTile[];
}
