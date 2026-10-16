// Eingaben des Formulars „Neue Zeit anlegen“ (Schaltpunkt bzw. Zeitraum) als Absichten -- rein und
// getestet. Es sind dieselben Absichten wie bei der Pinselgeste (`paint`) und beim Alt-Formular
// „Schaltpunkt anlegen“ (`point`); ob sie zulässig sind (Überdeckung, Mindestlänge), entscheidet der Server.

import { DAY_END } from './geometry';
import { parseClock } from './inspector';
import type { Intent } from './intents';

export type NewTimeField = 'time' | 'start' | 'end' | 'mode';
export type NewTimePlanned = { intent: Intent } | { error: string; field: NewTimeField };

const NO_MODE = 'Bitte einen Modus wählen.';
const BAD_TIME = 'Bitte eine gültige Uhrzeit eingeben (zum Beispiel 06:30).';

/** Ein einzelner Schaltpunkt: Tag, Uhrzeit, Modus. */
export function pointIntentOf(weekday: number, timeText: string, modeId: number | null): NewTimePlanned {
  if (modeId === null) return { error: NO_MODE, field: 'mode' };
  const minute = parseClock(timeText);
  if (minute === null) return { error: BAD_TIME, field: 'time' };
  return { intent: { type: 'point', weekday, minute, modeId } };
}

/** Ein Zeitraum innerhalb eines Tages; `toDayEnd` setzt das Ende auf 24:00. */
export function rangeIntentOf(
  weekday: number,
  startText: string,
  endText: string,
  toDayEnd: boolean,
  modeId: number | null,
): NewTimePlanned {
  if (modeId === null) return { error: NO_MODE, field: 'mode' };
  const start = parseClock(startText);
  if (start === null) return { error: 'Bitte einen gültigen Beginn eingeben (zum Beispiel 06:30).', field: 'start' };
  let end: number | null = DAY_END;
  if (!toDayEnd) {
    end = parseClock(endText);
    if (end === null) return { error: 'Bitte ein gültiges Ende eingeben (zum Beispiel 08:00).', field: 'end' };
  }
  if (end <= start) {
    return {
      error: toDayEnd ? 'Der Beginn muss vor 24:00 liegen.' : 'Das Ende muss nach dem Beginn liegen. Für „bis Mitternacht“ „Bis Tagesende“ ankreuzen.',
      field: toDayEnd ? 'start' : 'end',
    };
  }
  return { intent: { type: 'paint', weekday, start, end, modeId } };
}
