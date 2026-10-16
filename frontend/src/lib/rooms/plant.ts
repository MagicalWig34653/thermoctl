// Dauerhafte Anlagenhinweise der Übersicht. Reine Darstellung der Serverwerte
// (`plant`, `home`, `actuation` aus /ui-api/rooms); nichts davon wird im Browser entschieden.
// Die drei Zustände – Aktorfreigabe, MQTT-Ausgabe, Verbundrolle – bleiben getrennte Aussagen:
// keine folgt aus einer anderen, und keine darf eine andere verdecken.

import { formatAge } from './logic';
import type { Actuation, ActuationState, Home, Plant, Room, RoomRef } from './types';

export type Tone = 'ok' | 'warn' | 'neutral';

/** Der wirksame Zustand der Aktorfreigabe. Widersprechen sich `state` und die beiden Riegel,
 *  gilt der vorsichtigere: „schaltet“ wird nur gezeigt, wenn alle drei Angaben es sagen. */
export function effectiveState(a: Actuation): ActuationState {
  if (!a.control_armed) return 'dry_run';
  if (a.sending_allowed && a.state === 'live') return 'live';
  return 'armed_restart_missing';
}

/** Schaltet die Anlage tatsächlich? Nie im Trockenlauf, nie ohne Neustart, nie in Bereitschaft. */
export function isSwitching(a: Actuation): boolean {
  return effectiveState(a) === 'live' && !a.cluster_standby;
}

/** Nach dem Speichern der Freigabe fehlt noch der Neustart. */
export function restartRequired(a: Actuation): boolean {
  return effectiveState(a) === 'armed_restart_missing';
}

export interface Latch {
  id: 'actuation' | 'mqtt' | 'role';
  title: string;
  value: string;
  tone: Tone;
  text: string;
  help: string;
}

/** Die drei Riegel mit den Texten der Alt-Startseite (`start.html`). */
export function latches(a: Actuation): Latch[] {
  const state = effectiveState(a);
  const actuation: Latch =
    state === 'live'
      ? {
          id: 'actuation',
          title: 'Aktorfreigabe',
          value: 'Scharf und neu gestartet',
          tone: 'ok',
          text: 'Ausgabe freigegeben – alle zugeordneten Aktoren werden angesteuert.',
          help: 'control_armed',
        }
      : state === 'armed_restart_missing'
        ? {
            id: 'actuation',
            title: 'Aktorfreigabe',
            value: 'Scharf, Neustart fehlt',
            tone: 'warn',
            text: 'Der beim Start gebaute MQTT-Riegel ist noch zu. Bis zum Neustart gehen weder Sollwerte noch Ein/Aus-Befehle an Aktoren.',
            help: 'control_armed',
          }
        : {
            id: 'actuation',
            title: 'Aktorfreigabe',
            value: 'Trockenlauf',
            tone: 'neutral',
            text: 'Regelentscheidungen werden protokolliert. Weder Sollwerte noch Ein/Aus-Befehle gehen an Aktoren.',
            help: 'control_armed',
          };
  const mqtt: Latch = {
    id: 'mqtt',
    title: 'MQTT-Ausgabe',
    value: a.sending_allowed ? 'frei' : 'gesperrt',
    tone: a.sending_allowed ? 'ok' : a.control_armed ? 'warn' : 'neutral',
    text: 'Dieser Riegel entsteht beim Prozessstart. Eine Änderung an der gespeicherten Freigabe wirkt hier erst nach einem Neustart.',
    help: 'sending_allowed',
  };
  const role: Latch = a.cluster_standby
    ? {
        id: 'role',
        title: 'Rolle im Verbund',
        value: 'Bereitschaft',
        tone: 'warn',
        text: 'Diese Instanz steht in Bereitschaft: Sie regelt nicht und schaltet nichts, unabhängig von den beiden anderen Riegeln.',
        help: 'cluster_standby',
      }
    : {
        id: 'role',
        title: 'Rolle im Verbund',
        value: 'Führend oder Alleinbetrieb',
        tone: 'ok',
        text: 'Diese Instanz regelt. Entweder ist kein Verbund eingerichtet, oder sie führt ihn gerade.',
        help: 'cluster_standby',
      };
  return [actuation, mqtt, role];
}

export interface Chip {
  id: 'bridge' | 'silent' | 'stuck' | 'window' | 'vacation';
  tone: 'warn' | 'quiet' | 'warm';
  text: string;
  title?: string;
  rooms: RoomRef[];
  /** Zielseite (nach `appHref`), sonst keine Verlinkung. */
  to?: string;
}

const names = (rooms: RoomRef[]) => rooms.map((r) => r.name).join(', ');

/** Sammelchips; ein Chip erscheint nur, wenn sein Grund anliegt. Der Urlaub zählt laufend wie geplant. */
export function plantChips(plant: Plant, formatDate: (iso: string) => string): Chip[] {
  const out: Chip[] = [];
  if (plant.bridge_reachable === false) {
    out.push({ id: 'bridge', tone: 'warn', text: 'Brücke nicht erreichbar', rooms: [] });
  }
  if (plant.silent_sensor_rooms.length > 0) {
    out.push({ id: 'silent', tone: 'warn', text: `Kein Messwert: ${names(plant.silent_sensor_rooms)}`, rooms: plant.silent_sensor_rooms });
  }
  if (plant.stuck_sensor_rooms.length > 0) {
    out.push({
      id: 'stuck',
      tone: 'quiet',
      text: `Messwert unverändert, Regelung läuft normal weiter: ${names(plant.stuck_sensor_rooms)}`,
      title: 'Der Messwert bewegt sich seit längerem nicht. Der Raum regelt normal weiter, es ist kein Ausfall.',
      rooms: plant.stuck_sensor_rooms,
    });
  }
  if (plant.window_alarm_rooms.length > 0) {
    out.push({
      id: 'window',
      tone: 'warn',
      text: `Fenster vergessen: ${names(plant.window_alarm_rooms)}`,
      title: 'Ein Fenster steht seit Längerem offen, während es draußen kalt genug ist.',
      rooms: plant.window_alarm_rooms,
    });
  }
  if (plant.vacation) {
    const v = plant.vacation;
    out.push({
      id: 'vacation',
      tone: v.running ? 'warm' : 'quiet',
      text: v.running ? 'Urlaub läuft' : 'Urlaub geplant',
      title: `${formatDate(v.starts_at)} – ${formatDate(v.ends_at)}`,
      rooms: [],
    });
  }
  return out;
}

/** Räume mit laufendem Notbetrieb (nur die sichtbaren; die Liste kommt gefiltert vom Server). */
export function emergencyRooms(rooms: Room[]): { room: Room; headline: string; detail: string }[] {
  return rooms.filter((r) => r.emergency !== null).map((room) => ({ room, headline: room.emergency!.headline, detail: room.emergency!.detail }));
}

/** Leerzustand: keine sichtbare Zone. Der Knopf nur mit dem Recht, eine anzulegen. */
export function emptyState(rooms: Room[], plant: Plant): { show: boolean; canCreate: boolean } {
  return { show: rooms.length === 0, canCreate: plant.may_create_zone };
}

/** Wohnungssicht: Titel und Text des Hinweises, oder die Beruhigung. Beruhigt wird nur,
 *  wenn der Server `calm` sagt UND kein Hinweis anliegt. */
export function homeLine(home: Home): { calm: boolean; title: string; text: string } {
  const notice = home.notice;
  if (home.calm && notice === null) {
    return { calm: true, title: 'Heizung läuft normal', text: 'Alle Räume werden wie geplant geregelt.' };
  }
  if (notice === null) {
    return { calm: false, title: 'Hinweis zur Heizung', text: 'Bitte die Räume unten prüfen.' };
  }
  if (notice.kind === 'sensor') {
    const age = notice.measured_age_seconds;
    const since = age === null ? 'Es gibt noch keine Temperatur.' : `Die letzte Temperatur kam ${formatAge(age)}.`;
    return {
      calm: false,
      title: `${notice.zone_name}: Messwert möglicherweise nicht aktuell`,
      text: `${since} Die Heizung regelt weiter, aber der angezeigte Wert kann veraltet sein.`,
    };
  }
  if (notice.kind === 'notbetrieb') {
    return { calm: false, title: `${notice.zone_name} – ${notice.stage_label ?? 'Notbetrieb'}`, text: notice.detail ?? '' };
  }
  return {
    calm: false,
    title: `${notice.zone_name}: Fenster steht offen`,
    text: 'Solange ein Fenster offen steht, wird in diesem Raum nicht geheizt.',
  };
}
