// Abbildung der bestehenden Server-Navigationspfade (`NAVIGATION_ITEMS` in
// thermoctl/web/navigation.py) auf die sieben Bereiche der Oberfläche
// (docs/ui-migration/FUNKTIONSMATRIX.md, Abschnitt C; ENTSCHEIDUNGEN-UI-REDESIGN §2).
//
// Zwei Grundsätze:
//  - Sichtbarkeit kommt ausschließlich aus `bootstrap.navigation`. Diese Datei kennt keine
//    Rechte; ein Eintrag erscheint nur, wenn der Server den Pfad geliefert hat. Das ist
//    Darstellung, keine Autorisierung -- jeder Endpunkt prüft erneut.
//  - Solange eine Ansicht nicht migriert ist, verweist sie auf die bestehende HTML-Seite
//    (`legacyHref`), nie ins Leere.

export interface ServerNavItem {
  path: string;
  label: string;
  section: string;
}

export interface NavPage {
  /** Teil der Adresse unter dem Bereich: `/{area.id}/{id}` */
  id: string;
  label: string;
  /** Alte Server-Pfade, die in dieser Ansicht aufgehen (jeder Pfad genau einmal). */
  paths: readonly string[];
  /** Zusätzliche Suchbegriffe (Begriffe und Felder, die auf dieser Seite zu finden sind). */
  keywords: string;
}

export interface NavArea {
  id: string;
  label: string;
  group: 'Zuhause' | 'Verwaltung';
  description: string;
  pages: readonly NavPage[];
}

export const AREAS: readonly NavArea[] = [
  {
    id: 'uebersicht',
    label: 'Übersicht',
    group: 'Zuhause',
    description: 'Aktuelle Temperaturen, Räume und wichtige Hinweise.',
    pages: [
      {
        id: 'raeume',
        label: 'Räume',
        paths: ['/zones'],
        keywords: 'Zonen Raum anlegen Sollwert Temperatur Frostschutz manuell Raumeinstellungen',
      },
    ],
  },
  {
    id: 'zeit',
    label: 'Zeit & Abwesenheit',
    group: 'Zuhause',
    description: 'Wochenpläne, persönliche Abwesenheit und Anlagenurlaub.',
    pages: [
      {
        id: 'wochenplaene',
        label: 'Wochenpläne',
        paths: ['/schedule'],
        keywords: 'Zeitplan Schaltzeit Schaltpunkt Tagesplan kopieren',
      },
      {
        id: 'anlagenurlaub',
        label: 'Urlaub & Abwesenheit',
        paths: ['/vacation'],
        keywords: 'Urlaub Abwesenheit Bewohner Ausnahme gesamte Anlage',
      },
    ],
  },
  {
    id: 'auswertung',
    label: 'Auswertung',
    group: 'Zuhause',
    description: 'Temperaturen, Heizzeiten, Ereignisse und Schaltspiele.',
    pages: [
      {
        id: 'verlauf',
        label: 'Temperatur & Heizzeit',
        paths: ['/statistics', '/zones/history', '/heating-time', '/temperature-history'],
        keywords: 'Verlauf Historie Statistik Heizstatistik Zonenverläufe CSV',
      },
      {
        id: 'ereignisse',
        label: 'Ereignisse',
        paths: ['/audit', '/device-commands', '/changes'],
        keywords: 'Protokoll Audit Änderungsprotokoll Schaltprotokoll Schaltbefehle zurücknehmen',
      },
      {
        id: 'verschleiss',
        label: 'Relaisverschleiß',
        paths: ['/relay-wear'],
        keywords: 'Schaltspiele Lebensdauer Relais',
      },
    ],
  },
  {
    id: 'geraete',
    label: 'Geräte',
    group: 'Verwaltung',
    description: 'Geräte, Zuordnungen, Temperaturquellen und Bediengeräte.',
    pages: [
      {
        id: 'liste',
        label: 'Geräte & Zuordnung',
        paths: ['/devices'],
        keywords:
          'Sensor Aktor Integration Adresse Zuordnung Temperaturquelle Ersatzsensor Offset Gerät austauschen Austausch',
      },
      {
        id: 'bediengeraete',
        label: 'Bediengeräte',
        paths: ['/controllers'],
        keywords: 'Tastenbelegung Kanal Taste Ereignis',
      },
      {
        id: 'karte',
        label: 'Gerätekarte',
        paths: ['/devices/map', '/zones/data-flow'],
        keywords: 'Zuordnungskarte Datenfluss',
      },
    ],
  },
  {
    id: 'heizung',
    label: 'Heizung & Regelung',
    group: 'Verwaltung',
    description: 'Regelvorgaben, Temperaturmodi und Schutzfunktionen.',
    pages: [
      {
        id: 'regelvorgaben',
        label: 'Regelvorgaben',
        paths: ['/settings'],
        keywords: 'Hysterese PI Mindestzeiten Ventilschutz Fenster Alarm Notbetrieb Sonne Außenfühler',
      },
      {
        id: 'modi',
        label: 'Temperaturmodi',
        paths: ['/modes'],
        keywords: 'Sollwert-Modi Tag Nacht Modus',
      },
    ],
  },
  {
    id: 'system',
    label: 'System & Betrieb',
    group: 'Verwaltung',
    description: 'Freigaben, Schnittstellen, Meldungen und Verbund.',
    pages: [
      {
        id: 'betrieb',
        label: 'Betrieb & Freigaben',
        paths: ['/control'],
        keywords: 'Trockenlauf MQTT Riegel Neustart Freigabe',
      },
      {
        id: 'verbindungen',
        label: 'Verbindungen',
        paths: ['/interfaces'],
        keywords: 'Schnittstellen MQTT Zigbee2MQTT Meross Home Assistant Homebridge MCP Webhook',
      },
    ],
  },
  {
    id: 'personen',
    label: 'Personen & Zugänge',
    group: 'Verwaltung',
    description: 'Personen, Gruppenrechte, API-Tokens und Kioskfreigaben.',
    pages: [
      {
        id: 'rechte',
        label: 'Personen & Rechte',
        paths: ['/users', '/groups'],
        keywords: 'Benutzer Gruppen Berechtigungen Passwort',
      },
      {
        id: 'tokens',
        label: 'API-Tokens',
        paths: ['/tokens'],
        keywords: 'API Zugriff Schlüssel',
      },
      {
        id: 'kiosk',
        label: 'Kiosk-Zugänge',
        paths: ['/kiosk-tokens'],
        keywords: 'Kiosk-Tokens Monitor Freigabe',
      },
    ],
  },
];

/** Konto und Hilfe stehen getrennt am unteren Rand, nicht unter den sieben Bereichen. */
export const ACCOUNT_PATHS = ['/account'] as const;

/** Alte HTML-Seiten für Konto und Hilfe (nicht Teil von `bootstrap.navigation`). */
export const HELP_PATH = '/glossar';
export const PASSKEYS_PATH = '/passkeys';

/** Wohin ein Pfad, den die Abbildung nicht kennt, nach dem Server-Abschnitt gehört. */
const FALLBACK_AREA: Record<string, string> = {
  main: 'uebersicht',
  tenant: 'uebersicht',
  analysis: 'auswertung',
  system: 'system',
  access: 'personen',
};

export interface VisibleLink {
  path: string;
  label: string;
}

export interface VisiblePage extends NavPage {
  areaId: string;
  links: VisibleLink[];
}

export interface VisibleArea extends Omit<NavArea, 'pages'> {
  pages: VisiblePage[];
  /** Pfade aus dem Server, die keine Abbildung haben und hier gesammelt werden. */
  extraLinks: VisibleLink[];
}

/**
 * Baut aus der Navigation des Servers die sichtbaren Bereiche. Ein Bereich erscheint,
 * wenn mindestens eine seiner Seiten sichtbar ist; „Übersicht“ steht immer da, sie ist
 * der Einstieg jeder angemeldeten Person.
 */
export function buildVisibleAreas(items: readonly ServerNavItem[]): VisibleArea[] {
  const byPath = new Map(items.map((i) => [i.path, i]));
  const known = new Set(AREAS.flatMap((a) => a.pages.flatMap((p) => p.paths)));
  known.add('/account');
  const areas: VisibleArea[] = AREAS.map((area) => ({
    ...area,
    pages: area.pages.flatMap((page) => {
      const links = page.paths.flatMap((p) => {
        const item = byPath.get(p);
        return item ? [{ path: p, label: item.label }] : [];
      });
      return links.length ? [{ ...page, areaId: area.id, links }] : [];
    }),
    extraLinks: [],
  }));
  for (const item of items) {
    if (known.has(item.path)) continue;
    const target = areas.find((a) => a.id === (FALLBACK_AREA[item.section] ?? 'uebersicht'));
    target?.extraLinks.push({ path: item.path, label: item.label });
  }
  return areas.filter((a) => a.id === 'uebersicht' || a.pages.length > 0 || a.extraLinks.length > 0);
}

export interface SearchEntry {
  areaId: string;
  areaLabel: string;
  pageId: string | null;
  label: string;
  to: string;
}

/** Einstellungssuche über Bereiche, Seiten und Begriffe -- nur im sichtbaren Teil. */
export function searchNavigation(areas: readonly VisibleArea[], query: string): SearchEntry[] {
  const needle = query.trim().toLocaleLowerCase('de');
  if (!needle) return [];
  const words = needle.split(/\s+/);
  const out: SearchEntry[] = [];
  for (const area of areas) {
    for (const page of area.pages) {
      const hay = `${area.label} ${page.label} ${page.keywords}`.toLocaleLowerCase('de');
      if (words.every((w) => hay.includes(w))) {
        out.push({
          areaId: area.id,
          areaLabel: area.label,
          pageId: page.id,
          label: page.label,
          to: `/${area.id}/${page.id}`,
        });
      }
    }
  }
  return out;
}

export function areaPath(areaId: string): string {
  return areaId === 'uebersicht' ? '/' : `/${areaId}`;
}
