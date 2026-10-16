// Beschriftungen des Verlaufs in zwei Fassungen, wie in der Alt-Oberfläche
// (`domain/zone_history_chart.py`: TECHNICAL und TENANT). Beide sagen dasselbe und belegen
// dasselbe; die Wohnungssicht lässt nur die Fachsprache weg. Keine Fassung behauptet eine Wirkung
// im Raum: „Heizanforderung“ bleibt eine Anforderung.
//
// Bewusst nicht „Zielwert laut Zeitplan“: die Spalte hält den Sollwert VOR der Sonnenabsenkung,
// und der folgt außer dem Zeitplan auch einer Übersteuerung, dem Urlaub und der Betriebsart Aus.

import type { SeriesFlags } from './series';

export type SeriesKey = keyof SeriesFlags;

export interface HistoryWording {
  tenant: boolean;
  /** Lange Namen: Schalter, Legende, Wertezeile. */
  names: Record<SeriesKey, string>;
  /** Kurze Namen: Tooltip und Tabellenkopf. */
  short: Record<SeriesKey, string>;
  descriptions: Record<SeriesKey, string>;
  /** Einheit der Absenkung im Text. */
  kUnit: string;
  /** Ein Fach ohne Aussage über die Absenkung. */
  setbackUnknown: string;
  legendSolar: string;
  /** Dreieck über dem Diagramm: Absenkung, die als Fläche zu schmal wäre. */
  legendShortMark: string;
  /** Trockenlauf-Hinweis oberhalb der Verläufe (nur wenn die Anlage nicht scharf ist). */
  dryRun: { title: string; text: string };
  /** Erklärung der Seite; nur die Wohnungssicht trägt sie. */
  help: { intro: string; bullets: readonly string[] } | null;
}

export const TECHNICAL: HistoryWording = {
  tenant: false,
  names: {
    actual: 'Messwert',
    range: 'Min./Max.',
    scheduled: 'Geplant',
    effective: 'Wirksam',
    heating: 'Heizanforderung',
    solar: 'Sonnenabsenkung',
  },
  short: {
    actual: 'Messwert',
    range: 'Min./Max.',
    scheduled: 'Geplant',
    effective: 'Wirksam',
    heating: 'Heizen',
    solar: 'Sonne',
  },
  descriptions: {
    actual: 'Mittel der gemessenen Raumtemperatur je Zeitabschnitt.',
    range: 'Kleinster und größter Messwert je Zeitabschnitt.',
    scheduled: 'Sollwert aus dem Wochenplan.',
    effective: 'Sollwert, der tatsächlich galt (nach Übersteuerung, Urlaub, Sonnenabsenkung).',
    heating: 'Anteil der Regelentscheidungen mit Heizanforderung.',
    solar: 'Zeiten, in denen der Sollwert wegen Sonneneinstrahlung gesenkt wurde.',
  },
  kUnit: 'K',
  setbackUnknown: 'unbekannt',
  legendSolar: 'Sonnenabsenkung',
  legendShortMark: 'Dreieck oben: kurze Absenkung, im Bild nur als schmaler Streifen oder gar nicht als Band erkennbar',
  dryRun: {
    title: 'Die Anlage steuert derzeit nichts.',
    text: 'Sollwert und Heizanforderung sind Entscheidungen der Regelung, keine Aufzeichnung dessen, was in den Räumen geschah.',
  },
  help: null,
};

export const TENANT: HistoryWording = {
  tenant: true,
  names: {
    actual: 'Gemessen',
    range: 'Niedrigster bis höchster Messwert',
    scheduled: 'Zielwert ohne Sonnenabsenkung',
    effective: 'Tatsächlicher Zielwert',
    heating: 'Heizanforderung',
    solar: 'Sonnenabsenkung',
  },
  short: {
    actual: 'Gemessen',
    range: 'Niedrigster/höchster',
    scheduled: 'Zielwert ohne Sonnenabsenkung',
    effective: 'Tatsächlicher Zielwert',
    heating: 'Heizanforderung',
    solar: 'Sonnenabsenkung',
  },
  descriptions: {
    actual: 'Durchschnitt der gemessenen Temperatur je Abschnitt.',
    range: 'Niedrigster und höchster Messwert je Abschnitt.',
    scheduled: 'Der Zielwert, wie ihn Zeitplan, Abwesenheit, Urlaub, die Betriebsart Aus oder eine Änderung von Hand vorgeben.',
    effective: 'Der Zielwert, nach dem die Regelung gearbeitet hat.',
    heating: 'Anteil der Zeit mit Heizanforderung. Das sagt nichts darüber, wie es im Raum war.',
    solar: 'Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken.',
  },
  kUnit: 'Grad',
  setbackUnknown: 'keine Angabe',
  legendSolar:
    'Sonnenabsenkung: Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken; der Frostschutz soll dabei nicht unterschritten werden',
  legendShortMark: 'Dreieck oben: kurze Sonnenabsenkung, im Bild nur als schmaler Streifen oder gar nicht zu sehen',
  dryRun: {
    title: 'Die Anlage steuert derzeit nichts.',
    text: 'Der Verlauf zeigt, was die Regelung entschieden hätte: Der tatsächliche Zielwert und die Heizanforderung sind Entscheidungen, keine Aufzeichnung dessen, was in den Räumen geschah.',
  },
  help: {
    intro:
      'So hat sich die Temperatur in deinen Räumen entwickelt – und welchen Zielwert die Regelung dafür verwendet hat. Der gewählte Zeitraum gilt für alle Räume.',
    bullets: [
      'Die Linie ist die gemessene Temperatur. Die Stufenlinien zeigen den Zielwert: ohne Sonnenabsenkung (so, wie ihn der Zeitplan, eine Abwesenheit, ein Urlaub, die Betriebsart Aus oder eine Änderung von Hand vorgeben) und den tatsächlichen Zielwert, nach dem die Regelung gearbeitet hat.',
      'Liegt der tatsächliche Zielwert darunter, war die Sonnenabsenkung im Spiel (Band oben im Diagramm). Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken. Das kann erklären, warum die Regelung noch keine Heizanforderung hatte, obwohl die Temperatur unter dem Zielwert ohne Sonnenabsenkung lag.',
      'Die schmale Spur unter dem Diagramm zeigt die Heizanforderung. Das sagt nichts darüber, wie es im Raum war. Mit der Maus über ein Diagramm fahren oder es antippen zeigt die Werte; „keine Daten“ heißt: für diese Zeit gibt es keine Aufzeichnung.',
    ],
  },
};

export const historyWording = (tenant: boolean): HistoryWording => (tenant ? TENANT : TECHNICAL);

/** Reihen in der Reihenfolge des Schalters, mit den Namen der gewählten Fassung. */
export function seriesList(w: HistoryWording): { key: SeriesKey; label: string; description: string }[] {
  return (Object.keys(w.names) as SeriesKey[]).map((key) => ({ key, label: w.names[key], description: w.descriptions[key] }));
}

// -- Heizzeit ----------------------------------------------------------------------------

/** Dieselbe Wortwahl wie die Alt-Seite „Heizzeit“: Heizdauer, kein Energie- oder Kostenmesser. */
export const HEATING_TIME_SCOPE = 'Das ist Heizdauer – kein Energie- oder Kostenmesser.';
export const HEATING_TIME_DISCLAIMER =
  'Die Balken zeigen nur Heizzeit. Außentemperatur, Gebäudezustand und Lüften beeinflussen die Werte; ein genauer Verbrauch lässt sich daraus nicht ableiten.';
export const HEATING_TIME_DRY_RUN = {
  title: 'Die Anlage schaltet derzeit nicht.',
  text: 'Die Balken zeigen, wann sie geheizt hätte.',
};
