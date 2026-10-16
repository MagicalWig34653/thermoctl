// Kennzeichnung der Zeilen im Schaltprotokoll (Quelle: `kind` je Eintrag aus dem Server).
// Ein Befehl ging an ein Gerät; eine Entscheidung (Notbetrieb) und eine Absenkung (Sonne)
// sind Protokolleinträge ohne eigenen Sendeversuch und müssen als solche erkennbar sein.

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
