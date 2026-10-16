// Ansichtswahl des Kiosks: `auto`, `panel` oder `tafel` wie am Alt-Kiosk
// (`thermoctl/web/kiosk_views.py`, `kiosk_panel.js`).
//
//  - `tafel`: alle Räume als große, scrollende Karten (die bisherige Darstellung).
//  - `panel`: kompakte Kacheln in einem Raster; ein Tipp öffnet den Raum mit allen Bedienelementen
//    flächendeckend, nach 45 s ohne Bedienung springt die Ansicht zurück zur Übersicht.
//  - `auto`: unter 600 px Breite `panel`, sonst `tafel`.
//
// Das Alt-Kiosk-Cookie `thermoctl_kiosk_ansicht` ist HttpOnly und gehört dem Server; die
// Oberfläche liest und schreibt es nicht. Die Wahl liegt als reine Anzeigevorliebe im Browser
// (nie ein Zugangsmerkmal) und kommt wie am Alt-Kiosk aus der Adresse (`?ansicht=panel`).

export type KioskView = 'auto' | 'panel' | 'tafel';
export type ResolvedView = 'panel' | 'tafel';

export const KIOSK_VIEWS: readonly { id: KioskView; label: string; hint: string }[] = [
  { id: 'auto', label: 'Automatisch', hint: 'Nach Bildschirmbreite' },
  { id: 'panel', label: 'Panel', hint: 'Kacheln, Raum per Tipp öffnen' },
  { id: 'tafel', label: 'Tafel', hint: 'Alle Räume als Karten' },
];

export const VIEW_STORAGE_KEY = 'thermoctl.kiosk.ansicht';
/** Wie am Alt-Kiosk: „ab 600 px die Tafel“, `max-width: 599.98px` ist das Panel. */
export const PANEL_QUERY = '(max-width: 599.98px)';
export const PANEL_MAX_WIDTH = 599.98;
export const RETURN_MS = 45_000;

/** Unbekannte oder fehlende Werte werden `auto`, nie ein Fehler (wie der Server). */
export function normalizeView(value: string | null | undefined): KioskView {
  return value === 'panel' || value === 'tafel' || value === 'auto' ? value : 'auto';
}

export function resolveView(view: KioskView, width: number): ResolvedView {
  if (view === 'panel' || view === 'tafel') return view;
  return width <= PANEL_MAX_WIDTH ? 'panel' : 'tafel';
}

/**
 * Anfangsansicht: ein ausdrücklicher `?ansicht=` der Adresse gilt und wird gemerkt; sonst die
 * gemerkte Wahl; sonst `auto`.
 */
export function initialView(search: string, stored: string | null): { view: KioskView; remember: boolean } {
  const requested = new URLSearchParams(search).get('ansicht');
  if (requested !== null) return { view: normalizeView(requested), remember: true };
  return { view: normalizeView(stored), remember: false };
}
