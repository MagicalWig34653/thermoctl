// Fehlerabbildung der Geräteansichten (rein, getestet). Der Servertext ist für Menschen
// geschrieben und wird übernommen; das Raster entscheidet nur über Art und Folgen.
// Schreibende Aufrufe werden nie wiederholt.
import { toFailure, type Failure } from '../rooms/logic';

export type DeviceFailure = Failure & {
  /** Nach diesem Fehler den Serverstand lesen (Lesen ist gefahrlos). */
  reload: boolean;
  /** Ein Konflikt mit dem Zustand (Doppelzuordnung, Aktor in anderer Zone, veraltet). */
  isConflict: boolean;
};

/** Zusatz bei unklarem Ausgang (der Text von `toFailure` sagt bereits, dass er unklar ist). */
export const UNCLEAR_WRITE = 'Die Ansicht wird neu gelesen.';

export function deviceFailure(error: unknown): DeviceFailure {
  const base = toFailure(error);
  const reload = base.kind === 'network' || base.kind === 'conflict' || base.kind === 'not_found' || base.kind === 'forbidden';
  return { ...base, reload, isConflict: base.kind === 'conflict' };
}

/** Text zum Anzeigen: bei unklarem Ausgang wird der Lesehinweis ergänzt. */
export function failureText(failure: DeviceFailure): string {
  return failure.kind === 'network' ? `${failure.message} ${UNCLEAR_WRITE}` : failure.message;
}
