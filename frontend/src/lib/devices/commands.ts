// Kennzeichnung der Zeilen im Schaltprotokoll (Quelle: `kind` je Eintrag aus dem Server).
// Ein Befehl ging an ein Gerät; eine Entscheidung (Notbetrieb) und eine Absenkung (Sonne)
// sind Protokolleinträge ohne eigenen Sendeversuch und müssen als solche erkennbar sein.

import { formatInstant } from '../rooms/logic';
import type { CommandEntry, CommandLog } from './types';

export type CommandKind = 'befehl' | 'entscheidung' | 'absenkung';

export interface CommandKindLabel {
  kind: CommandKind;
  label: string;
  hint: string | null;
}

export function commandKind(kind: string | null | undefined): CommandKindLabel {
  if (kind === 'entscheidung') {
    return { kind, label: 'Entscheidung', hint: 'Notbetriebsentscheidung, kein eigener Sendeversuch' };
  }
  if (kind === 'absenkung') {
    return { kind, label: 'Absenkung', hint: 'Sonnenabsenkung des Sollwerts, kein Sendeversuch' };
  }
  return { kind: 'befehl', label: 'Befehl', hint: null };
}

// -- Zeile als Karte (Handy) -----------------------------------------------------------------

/** Schlüssel einer Zeile. Befehl, Entscheidung und Absenkung stammen aus drei Tabellen mit eigener
 *  Zählung; dieselbe Nummer kann in einer Liste also mehrfach vorkommen. */
export function commandRowKey(e: Pick<CommandEntry, 'kind' | 'id'>): string {
  return `${e.kind}:${e.id}`;
}

export type CommandRowKey = 'raum' | 'geraet' | 'befehl' | 'quelle' | 'ergebnis' | 'fehler' | 'begruendung';

export interface CommandCardRow {
  key: CommandRowKey;
  label: string;
  value: string;
  /** Zusatzzeile unter dem Wert (die Nutzlast beim Befehl). */
  detail?: string;
  tone?: 'fehler';
}

export interface CommandCard {
  key: string;
  kind: CommandKindLabel;
  time: string;
  iso: string | null;
  simulated: boolean;
  rows: CommandCardRow[];
}

/** Dieselben Felder und Wörter wie die Tabelle des Desktops, als Liste für schmale Bildschirme.
 *  Fehler und Begründung stehen nur, wenn es sie gibt; leere Felder zeigen einen Strich. */
export function commandCard(
  e: CommandEntry,
  log: Pick<CommandLog, 'timezone' | 'outcome_labels' | 'source_labels'>,
  now: Date = new Date(),
): CommandCard {
  const text = (value: string | null | undefined) => (value ? value : '—');
  const rows: CommandCardRow[] = [
    { key: 'raum', label: 'Raum', value: text(e.zone_name) },
    { key: 'geraet', label: 'Gerät', value: text(e.device_name) },
    { key: 'befehl', label: 'Befehl', value: text(e.command), ...(e.payload ? { detail: e.payload } : {}) },
    { key: 'quelle', label: 'Quelle', value: e.source ? (log.source_labels[e.source] ?? e.source) : '—' },
    { key: 'ergebnis', label: 'Ergebnis', value: e.outcome ? (log.outcome_labels[e.outcome] ?? e.outcome) : '—' },
  ];
  if (e.error) rows.push({ key: 'fehler', label: 'Fehler', value: e.error, tone: 'fehler' });
  if (e.reason) rows.push({ key: 'begruendung', label: 'Begründung', value: e.reason });
  return {
    key: commandRowKey(e),
    kind: commandKind(e.kind),
    time: formatInstant(e.sent_at, log.timezone ?? 'UTC', now),
    iso: e.sent_at,
    simulated: e.simulated,
    rows,
  };
}
