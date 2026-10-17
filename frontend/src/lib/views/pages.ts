// Seiten werden erst beim Aufruf nachgeladen (Route-Level Code-Splitting): Anmeldung und Shell
// bleiben im Hauptbündel, jede Seite ist ein eigener Chunk. Der Schlüssel ist `<bereich>/<seite>`
// wie in `navigation.ts`; was fehlt, ist „nicht gefunden“. Sichtbarkeit und Rechte entscheidet
// weiterhin der Server, diese Tabelle lädt nur Code.

import type { Component } from 'svelte';

export type PageModule = { default: Component<any> };
export type PageLoader = () => Promise<PageModule>;

export const PAGE_LOADERS: Record<string, PageLoader> = {
  'uebersicht/raeume': () => import('$lib/components/zones/RoomsPage.svelte'),
  'auswertung/verlauf': () => import('$lib/components/history/HistoryPage.svelte'),
  'auswertung/ereignisse': () => import('$lib/components/history/EventsPage.svelte'),
  'auswertung/verschleiss': () => import('$lib/components/history/WearPage.svelte'),
  'geraete/liste': () => import('$lib/components/devices/DevicesPage.svelte'),
  'geraete/bediengeraete': () => import('$lib/components/devices/ControllersPage.svelte'),
  'geraete/anlagenbild': () => import('$lib/components/devices/PlantPage.svelte'),
  'geraete/karte': () => import('$lib/components/devices/MapPage.svelte'),
  'personen/rechte': () => import('$lib/components/access/PeoplePage.svelte'),
  'personen/tokens': () => import('$lib/components/access/TokensPage.svelte'),
  'personen/kiosk': () => import('$lib/components/access/KioskPage.svelte'),
  'heizung/regelvorgaben': () => import('$lib/components/operations/RegelvorgabenPage.svelte'),
  'heizung/fenster': () => import('$lib/components/operations/WindowPage.svelte'),
  'heizung/notbetrieb': () => import('$lib/components/operations/EmergencyPage.svelte'),
  'heizung/sonne': () => import('$lib/components/operations/SolarPage.svelte'),
  'heizung/aussenfuehler': () => import('$lib/components/operations/OutdoorPage.svelte'),
  'heizung/modi': () => import('$lib/components/schedule/ModesPage.svelte'),
  'system/betrieb': () => import('$lib/components/operations/BetriebPage.svelte'),
  'system/verbindungen': () => import('$lib/components/operations/ConnectionsPage.svelte'),
  'system/meldungen': () => import('$lib/components/operations/NotificationsPage.svelte'),
  'system/verbund': () => import('$lib/components/operations/ClusterPage.svelte'),
  'zeit/wochenplaene': () => import('$lib/components/schedule/ScheduleView.svelte'),
  'zeit/anlagenurlaub': () => import('$lib/components/schedule/VacationPage.svelte'),
};

/** Lädt den Code einer Seite im Voraus (Hover oder Leerlauf); Fehler bleiben still, die Seite
 *  versucht es beim echten Aufruf noch einmal. */
export function preloadPage(key: string): void {
  const loader = PAGE_LOADERS[key];
  if (loader) void loader().catch(() => undefined);
}
