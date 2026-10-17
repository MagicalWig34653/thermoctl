// Reine Darstellungslogik der Raumansichten (ohne Svelte, mit node:test geprüft).
// Keine Fachregel: Sollwert, Entscheidung, Rechte und Tagesverlauf kommen fertig vom Server.

import { ApiError } from '../api';
import { roleLabel } from '../cluster-role';
import { sameTemperature } from './decimal';
import type { Actions, Actuation, DaySegment, Room, RoomDetail } from './types';

// -- Zeit ----------------------------------------------------------------------------------

function safeZone(timeZone: string): string | undefined {
  try {
    new Intl.DateTimeFormat('de-DE', { timeZone });
    return timeZone;
  } catch {
    return undefined;
  }
}

/** Zeitpunkt in der Zeitzone der Anlage (vom Server geliefert), nicht der des Browsers.
 *  Heute: nur die Uhrzeit, sonst mit Wochentag und Datum. */
export function formatInstant(iso: string | null, timeZone: string, now: Date = new Date()): string {
  if (iso === null) return '—';
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return '—';
  const zone = safeZone(timeZone);
  const day = (d: Date) =>
    new Intl.DateTimeFormat('de-DE', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  const clock = new Intl.DateTimeFormat('de-DE', { timeZone: zone, hour: '2-digit', minute: '2-digit' }).format(moment);
  if (day(moment) === day(now)) return `${clock} Uhr`;
  const date = new Intl.DateTimeFormat('de-DE', {
    timeZone: zone,
    weekday: 'short',
    day: '2-digit',
    month: '2-digit',
  }).format(moment);
  return `${date}, ${clock} Uhr`;
}

/** „vor 5 Min.“ aus dem vom Server gelieferten Alter. */
export function formatAge(seconds: number | null): string {
  if (seconds === null) return 'noch kein Messwert';
  if (seconds < 60) return 'gerade eben';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `vor ${hours} Std.`;
  return `vor ${Math.floor(hours / 24)} Tagen`;
}

/** 390 wird zu „06:30“; 1440 zu „24:00“. */
export function formatMinute(minute: number): string {
  const m = Math.max(0, Math.min(1440, Math.round(minute)));
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** Breite und Lage eines Tagesabschnitts in Prozent des Tages. */
export function segmentBox(segment: DaySegment): { left: number; width: number } {
  const start = Math.max(0, Math.min(1440, segment.start_minute));
  const end = Math.max(start, Math.min(1440, segment.end_minute));
  return { left: (start / 1440) * 100, width: ((end - start) / 1440) * 100 };
}

/** `now_fraction` des Servers heißt so, ist aber ein Prozentwert (0 bis 100, nicht 0 bis 1). */
export function nowPercent(nowFraction: number): number {
  if (!Number.isFinite(nowFraction)) return 0;
  return Math.max(0, Math.min(100, nowFraction));
}

// -- Zustand eines Raums ---------------------------------------------------------------------

export type RoomStatusKind = 'sensor' | 'window' | 'off' | 'heating' | 'idle' | 'unknown';

export interface RoomStatus {
  kind: RoomStatusKind;
  label: string;
  /** Warnzustand (Störung, Fenster): wird auffälliger gezeichnet. */
  attention: boolean;
}

/** Neutraler Hinweis bei veraltetem Messwert: der Effekt, nicht die Ursache (kein Gerät, kein Sensorcode). */
export const STALE_MEASUREMENT_NOTICE = 'Messwert möglicherweise nicht aktuell – die Heizung regelt weiter.';

/** Ein Wort für die Karte. Reihenfolge: Sensorstörung vor Fenster vor Aus vor Heizanforderung.
 *  „Heizanforderung“ ist eine Entscheidung (`decision.would_heat`), kein gemeldeter Schaltzustand.
 *  In der Wohnungssicht (`tenant`) ist ein veralteter Messwert keine Störung der Karte: Er steht
 *  als neutraler Hinweis daneben (`measurementLine`), die Entscheidung bleibt sichtbar. */
export function roomStatus(room: Room, tenant = false): RoomStatus {
  const neutralStale = tenant && room.measurement_stale && room.temperature_c !== null;
  if (!neutralStale && (room.temperature_c === null || room.sensor.status_code !== 'ok')) {
    return { kind: 'sensor', label: room.sensor.status_code === 'ok' ? 'Kein Messwert' : room.sensor.status_label, attention: true };
  }
  if (room.sensor.window_alarm) {
    return { kind: 'window', label: 'Fenster vergessen', attention: true };
  }
  if (room.sensor.window_open) {
    return { kind: 'window', label: 'Fenster offen', attention: true };
  }
  if (room.operating_mode.code === 'off') return { kind: 'off', label: room.operating_mode.label, attention: false };
  if (room.decision === null) return { kind: 'unknown', label: 'Keine Entscheidung', attention: false };
  return room.decision.would_heat
    ? { kind: 'heating', label: 'Heizanforderung', attention: false }
    : { kind: 'idle', label: 'Im Ziel', attention: false };
}

/** Zeile unter der Ist-Temperatur. Wohnungssicht und veralteter Wert: neutraler Hinweis statt
 *  Gerätezustand. Sonst wie bisher: Alter der Messung bzw. der Zustand des Fühlers. */
export function measurementLine(room: Room, tenant = false): { text: string; problem: boolean } {
  if (room.temperature_c === null) return { text: room.sensor.status_label, problem: true };
  if (tenant && room.measurement_stale) {
    return { text: `${STALE_MEASUREMENT_NOTICE} (${formatAge(room.measured_age_seconds)})`, problem: false };
  }
  const ok = room.sensor.status_code === 'ok';
  return { text: `${ok ? 'Messung' : room.sensor.status_label} ${formatAge(room.measured_age_seconds)}`, problem: !ok };
}

/** Liegt der wirksame Sollwert neben dem geplanten (Übersteuerung, Urlaub, Betriebsart)? */
export function plannedDiffers(room: Room): boolean {
  const planned = room.decision?.planned_setpoint_c ?? null;
  if (planned === null) return false;
  return !sameTemperature(planned, room.setpoint.temperature_c);
}

// -- Suche und Filter ------------------------------------------------------------------------

export type RoomFilter = 'all' | 'heating' | 'override' | 'attention';

export const FILTERS: readonly { id: RoomFilter; label: string }[] = [
  { id: 'all', label: 'Alle' },
  { id: 'heating', label: 'Heizt' },
  { id: 'override', label: 'Übersteuert' },
  { id: 'attention', label: 'Störung' },
];

export function filterRooms(rooms: readonly Room[], query: string, filter: RoomFilter): Room[] {
  const needle = query.trim().toLocaleLowerCase('de');
  return rooms.filter((room) => {
    if (needle && !room.name.toLocaleLowerCase('de').includes(needle)) return false;
    const status = roomStatus(room);
    switch (filter) {
      case 'heating':
        return status.kind === 'heating';
      case 'override':
        return room.override !== null;
      case 'attention':
        return status.attention || room.emergency !== null;
      default:
        return true;
    }
  });
}

// -- Anlagenstatus: drei getrennte Zustände ---------------------------------------------------

export interface ActuationRow {
  id: 'armed' | 'sending' | 'role';
  title: string;
  value: string;
  tone: 'ok' | 'warn' | 'neutral';
  help: string;
}

/** Gespeicherte Freigabe, Sendefreigabe und Führungsrolle sind getrennte Tatsachen;
 *  keine davon folgt aus einer anderen. */
export function actuationRows(actuation: Actuation): ActuationRow[] {
  return [
    {
      id: 'armed',
      title: 'Gespeicherte Freigabe',
      value: actuation.control_armed ? 'Scharf gestellt' : 'Nicht freigegeben (Trockenlauf)',
      tone: actuation.control_armed ? 'ok' : 'neutral',
      help: 'control_armed',
    },
    {
      id: 'sending',
      title: 'Sendefreigabe (MQTT und Meross)',
      value: actuation.sending_allowed ? 'Beim Start freigegeben' : 'Beim Start nicht freigegeben',
      tone: actuation.sending_allowed ? 'ok' : actuation.control_armed ? 'warn' : 'neutral',
      help: 'sending_allowed',
    },
    {
      id: 'role',
      title: 'Führung',
      value: roleLabel(actuation).value,
      tone: roleLabel(actuation).tone,
      help: 'cluster_standby',
    },
  ];
}

export function actuationHeadline(actuation: Actuation): { text: string; tone: 'ok' | 'warn' | 'neutral' } {
  switch (actuation.state) {
    case 'live':
      return { text: 'Die Anlage schaltet.', tone: 'ok' };
    case 'armed_restart_missing':
      return { text: 'Freigabe gespeichert, aber der Neustart fehlt – es wird noch nichts geschaltet.', tone: 'warn' };
    default:
      return { text: 'Trockenlauf – es wird nichts geschaltet.', tone: 'neutral' };
  }
}

// -- Aktionen --------------------------------------------------------------------------------

export interface ActionUi {
  /** Bedienelement zeigen. Nur Darstellung: der Server prüft jede Aktion erneut. */
  show: boolean;
  /** Gezeigt, aber gerade nicht bedienbar; `reason` erklärt, warum. */
  disabled: boolean;
  reason: string | null;
}

export interface RoomActionsUi {
  step: ActionUi;
  createOverride: ActionUi;
  cancelOverride: ActionUi;
  jumpNext: ActionUi;
  modeSetpoints: ActionUi;
  operatingMode: ActionUi;
}

const hidden: ActionUi = { show: false, disabled: true, reason: null };
const on: ActionUi = { show: true, disabled: false, reason: null };
const off = (reason: string): ActionUi => ({ show: true, disabled: true, reason });

/** Aus den Aktionsflags des Servers und dem Raumzustand wird der Zustand der Bedienelemente.
 *  Ein fehlendes Flag blendet aus, setzt aber nichts durch; ein gesetztes Flag erlaubt nichts,
 *  was der Server nicht auch prüft. */
export function roomActionsUi(room: RoomDetail | Room, nextSwitchAvailable: boolean): RoomActionsUi {
  const a: Actions = room.actions;
  const switchedOff = room.operating_mode.code === 'off';

  let step = hidden;
  if (a.edit_setpoint_step) {
    if (switchedOff) step = off('Betriebsart „Aus“: der Raum hält den Frostschutz.');
    else if (room.override) step = off('Eine Übersteuerung läuft – erst beenden, dann den Sollwert ändern.');
    else if (room.setpoint.mode_id === null) step = off('Gerade gilt kein Temperaturmodus, dessen Sollwert sich ändern ließe.');
    else step = on;
  }

  let createOverride = hidden;
  if (a.override) {
    createOverride = room.override ? off('Es läuft bereits eine Übersteuerung.') : switchedOff ? off('Betriebsart „Aus“.') : on;
  }

  let jumpNext = hidden;
  if (a.jump_next) {
    jumpNext = !nextSwitchAvailable ? off('Für diesen Raum gibt es keine nächste Phase.') : switchedOff ? off('Betriebsart „Aus“.') : on;
  }

  return {
    step,
    createOverride,
    cancelOverride: a.cancel_override && room.override ? on : hidden,
    jumpNext,
    modeSetpoints: a.edit_mode_setpoints ? on : hidden,
    operatingMode: a.set_operating_mode ? on : hidden,
  };
}

// -- Fehler ----------------------------------------------------------------------------------

export type FailureKind =
  | 'validation'
  | 'conflict'
  | 'forbidden'
  | 'not_found'
  | 'session'
  | 'network'
  | 'server';

export interface Failure {
  kind: FailureKind;
  message: string;
  /** Feld, an dem der Server den Fehler festmacht (`temperature_c`, `end`, `duration_minutes` …). */
  field: string | null;
  /** Serverseitiger Fehlercode, z. B. `override_running`. */
  code: string | null;
}

/** Bildet jeden Fehler eines Aufrufs auf einen sichtbaren Zustand ab. Der Servertext wird
 *  übernommen (er ist für Menschen geschrieben); das Raster darunter entscheidet nur über
 *  Art und Ton. Es gibt keine automatische Wiederholung. */
export function toFailure(error: unknown): Failure {
  if (error instanceof ApiError) {
    const base = { message: error.message, field: error.field, code: error.code };
    if (error.kind === 'unauthorized' || error.stale) {
      return { ...base, kind: 'session', message: 'Die Sitzung ist abgelaufen. Bitte erneut anmelden – die Eingabe wurde nicht gesendet.' };
    }
    if (error.kind === 'network') {
      return {
        ...base,
        kind: 'network',
        message: 'Der Server ist nicht erreichbar. Ob die Änderung angekommen ist, ist unklar – bitte den Zustand prüfen, bevor du es erneut versuchst.',
      };
    }
    if (error.kind === 'client') return { ...base, kind: 'server' };
    if (error.kind === 'forbidden') return { ...base, kind: 'forbidden', message: error.message || 'Dafür fehlt die Berechtigung.' };
    if (error.status === 404) return { ...base, kind: 'not_found' };
    if (error.status === 409) return { ...base, kind: 'conflict' };
    if (error.status === 422) return { ...base, kind: 'validation' };
    return { ...base, kind: 'server', message: error.message || `Der Server meldet einen Fehler (${error.status}).` };
  }
  return { kind: 'server', message: 'Unerwarteter Fehler.', field: null, code: null };
}

/** Der Server nennt das Temperaturfeld je nach Weg `temperature_c` (Eingabeprüfung der API) oder
 *  `temperatur` (Domänenprüfung des Bereichs); beides meint dieselbe Eingabe. */
export function isTemperatureField(field: string | null): boolean {
  return field === 'temperature_c' || field === 'temperatur';
}

/** Fehler, bei denen die Seite neu gelesen werden sollte (der Zustand hat sich geändert). */
export function shouldReload(failure: Failure): boolean {
  return failure.kind === 'conflict' || failure.kind === 'not_found' || failure.kind === 'forbidden';
}
