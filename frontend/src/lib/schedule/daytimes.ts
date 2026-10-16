// Vereinfachte Tagesbearbeitung („Warm ab“ / „Nacht ab“) und Einrichten eines leeren Plans.
// Der Server entscheidet, welche zwei Modi ein leerer Tag bekommt (`empty_day_modes`) und ob
// eine Eingabe gilt; hier wird nur beschrieben, was ein Tag zeigt, und Text in Minuten
// übersetzt. Zeiten sind Minuten ab Mitternacht (00:00 ist gültig).

import { DAY_NAMES } from './geometry';
import { parseClock } from './inspector';
import type { Intent } from './intents';
import type { Plan } from './types';

/** Vorgeschlagene Zeiten für einen leeren Tag: 06:30 und 22:00. */
export const SUGGESTED_MINUTES: readonly [number, number] = [6 * 60 + 30, 22 * 60];

export type DayView =
  | { kind: 'empty'; fields: [DayField, DayField] }
  | { kind: 'two'; fields: [DayField, DayField] }
  /** Leerer Tag, aber der Raum hat keine zwei brauchbaren Temperaturen. */
  | { kind: 'unavailable' }
  /** Eine oder mehr als zwei Schaltzeiten: nicht vereinfacht bearbeitbar (der Editor bleibt). */
  | { kind: 'other'; count: number };

export interface DayField {
  label: string;
  minute: number;
}

export function dayView(plan: Pick<Plan, 'points' | 'modes' | 'empty_day_modes'>, weekday: number): DayView {
  const day = plan.points.filter((p) => p.weekday === weekday).sort((a, b) => a.minute_of_day - b.minute_of_day);
  if (day.length === 0) {
    const modes = plan.empty_day_modes;
    if (!modes || modes.length < 2) return { kind: 'unavailable' };
    return {
      kind: 'empty',
      fields: [
        { label: `${modes[0].name} ab`, minute: SUGGESTED_MINUTES[0] },
        { label: `${modes[1].name} ab`, minute: SUGGESTED_MINUTES[1] },
      ],
    };
  }
  if (day.length !== 2) return { kind: 'other', count: day.length };
  const name = (modeId: number) => plan.modes.find((m) => m.id === modeId)?.name ?? 'Abschnitt';
  return {
    kind: 'two',
    fields: [
      { label: `${name(day[0].mode_id)} ab`, minute: day[0].minute_of_day },
      { label: `${name(day[1].mode_id)} ab`, minute: day[1].minute_of_day },
    ],
  };
}

/** Ein Plan ganz ohne Schaltzeit: ein frisch angelegter Raum. */
export const planIsEmpty = (plan: Pick<Plan, 'points'>): boolean => plan.points.length === 0;

export type DayPlanned = { intent: Intent } | { error: string };

/** Zwei Zeitfelder („HH:MM“) als Absicht; ob die Zeiten zulässig sind, entscheidet der Server. */
export function dayTimesIntent(weekday: number, firstText: string, secondText: string): DayPlanned {
  const first = parseClock(firstText);
  const second = parseClock(secondText);
  if (first === null || second === null) return { error: 'Bitte gültige Uhrzeiten eingeben.' };
  return { intent: { type: 'dayTimes', weekday, first, second } };
}

export const dayName = (weekday: number): string => DAY_NAMES[weekday] ?? '';
