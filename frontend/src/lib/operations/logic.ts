// Reine Darstellungs- und Aufbereitungslogik der Regelungs- und Betriebsseiten (ohne Svelte,
// mit node:test geprüft). Keine Fachregel: Zulässigkeit, Grenzen, Vererbung, Freigabe und
// Begründungen entscheidet der Server. Hier steht nur, wie Antworten angezeigt und wie
// Eingaben zu einer Anfrage zusammengesetzt werden. Zahlen bleiben Zeichenketten.

import { compareDecimal } from '../rooms/decimal';
import { roleLabel, roleOf } from '../cluster-role';
import { formatInstant, toFailure, type Failure } from '../rooms/logic';
import type {
  ArmedResult, ControlFieldName, EmergencyActuator, InterfaceDetail, NotificationTest, NumberField,
  OperationActuation, ParametersPatch, PiSwitchingModel, RoomParameters, Settings,
} from './types';

/** Der zweite Riegel ist ein Wert für den ganzen Prozess, nicht ein Teil des MQTT-Clients:
 *  Meross-Befehle gehen durch denselben beim Start eingefrorenen Wert (wie in der Alt-Oberfläche). */
export const SENDING_PATHS = 'MQTT und Meross';
export const SENDING_LATCH_NOTE = `Der zweite Riegel (Sendefreigabe) wird beim Start des Dienstes gebildet und gilt für alle Wege, auf denen Befehle hinausgehen (${SENDING_PATHS}).`;

// -- Zahlen als Text ---------------------------------------------------------------------------

/** Anzeige im Eingabefeld: Dezimalkomma. Keine Rundung, nur das Zeichen wird getauscht. */
export const toInput = (value: string): string => value.replace('.', ',');

/** Eingabe zum Server: Komma wird Punkt, Leerraum fällt weg. Der Server prüft den Inhalt. */
export const fromInput = (text: string): string => text.trim().replace(',', '.');

/** Zahlengleich (`0.5` = `0.50` = `0,5`); bei Nicht-Zahlen gilt der Textvergleich. */
export function sameNumber(a: string, b: string): boolean {
  const x = fromInput(a);
  const y = fromInput(b);
  const cmp = compareDecimal(x, y);
  return cmp === null ? x === y : cmp === 0;
}

/** Exakte Differenz `a - b` zweier Decimal-Texte, ohne `number`; `null` bei ungültiger Eingabe. */
export function subtractDecimal(a: string, b: string): string | null {
  const parse = (v: string) => {
    const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(fromInput(v));
    return m ? { neg: m[1] === '-', whole: m[2], fraction: m[3] ?? '' } : null;
  };
  const pa = parse(a);
  const pb = parse(b);
  if (!pa || !pb) return null;
  const digits = Math.max(pa.fraction.length, pb.fraction.length);
  const scale = (p: NonNullable<typeof pa>) => {
    const n = BigInt(p.whole + p.fraction.padEnd(digits, '0'));
    return p.neg ? -n : n;
  };
  const diff = scale(pa) - scale(pb);
  const negative = diff < 0n;
  const raw = (negative ? -diff : diff).toString().padStart(digits + 1, '0');
  const whole = digits === 0 ? raw : raw.slice(0, raw.length - digits);
  const fraction = digits === 0 ? '' : raw.slice(raw.length - digits);
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`;
}

// -- Fehler am Feld ----------------------------------------------------------------------------

export type OpsFailure = Failure;

/** Der Server nennt manche Felder nach der Datenbankspalte; die Seite kennt sie unter dem Namen der Eingabe. */
const FIELD_ALIASES: Record<string, string> = {
  solar_forecast_enabled: 'enabled',
  solar_forecast_latitude: 'latitude',
  solar_forecast_longitude: 'longitude',
};

/** Fehler aus einem Aufruf in die Form der Seite: gleiche Abbildung wie in den übrigen Ansichten,
 *  dazu Feldnamen der Eingabe und ein lesbarer Text ohne vorangestellten technischen Feldnamen
 *  („emergency_setpoint_c: Wert zwischen …“). Der Inhalt des Servertexts bleibt unverändert. */
export function opsFailure(error: unknown): OpsFailure {
  const base = toFailure(error);
  if (base.kind !== 'validation' || base.field === null) return base;
  const raw = base.field;
  const prefix = `${raw}: `;
  const rest = base.message.startsWith(prefix) ? base.message.slice(prefix.length) : null;
  const message = rest === null ? base.message : rest.charAt(0).toLocaleUpperCase('de') + rest.slice(1);
  return { ...base, field: FIELD_ALIASES[raw] ?? raw, message };
}

/** Meldung für genau dieses Feld (nur bei Eingabefehlern des Servers, 422 mit `field`). */
export function errorAt(failure: Failure | null, field: string): string | null {
  return failure !== null && failure.kind === 'validation' && failure.field === field ? failure.message : null;
}

/** Fehlertext für die Seitenmitte: alles, was nicht an einem sichtbaren Feld erscheint. Ein
 *  Feldfehler ohne Platz darf nie verschwinden. */
export function generalError(failure: Failure | null, isPlaced: (field: string) => boolean): string | null {
  if (failure === null) return null;
  if (failure.kind === 'validation' && failure.field !== null && isPlaced(failure.field)) return null;
  return failure.message;
}

// -- Raumtechnik: Entwurf und Teil-Update --------------------------------------------------------

export const CONTROL_FIELDS: readonly ControlFieldName[] = [
  'hysteresis_k',
  'min_on_seconds',
  'min_off_seconds',
  'sensor_timeout_seconds',
  'temperature_offset_k',
  'window_resume_delay_seconds',
  'solar_setback_max_k',
];

export const PI_FIELDS = [
  'pi_gain_per_k',
  'pi_integral_time_minutes',
  'pi_min_on_seconds',
  'pi_min_off_seconds',
] as const;

export interface ParametersDraft {
  /** Leer = erbt. Sonst der Eigenwert der Zone. */
  control: Record<ControlFieldName, string>;
  solar_gain_factor: string;
  valve: { enabled: boolean; interval_days: string; duration_minutes: string };
  pi: {
    enabled: boolean;
    confirm: boolean;
    gain_per_k: string;
    integral_time_minutes: string;
    min_on_seconds: string;
    min_off_seconds: string;
  };
  window_temp_drop: boolean;
  /** `profile_id` und `emergency_setpoint_c`: leer = erbt. */
  sensor_failure: { enabled: boolean; profile_id: string; emergency_setpoint_c: string };
  /** Leer = Ausgleichswert entfernen. */
  backup_offsets: Record<number, string>;
}

export function draftFromParameters(p: RoomParameters): ParametersDraft {
  const control = {} as Record<ControlFieldName, string>;
  for (const name of CONTROL_FIELDS) {
    const own = p.control[name]?.own ?? null;
    control[name] = own === null ? '' : toInput(own);
  }
  return {
    control,
    solar_gain_factor: toInput(p.solar_gain_factor),
    valve: {
      enabled: p.valve_protection.enabled,
      interval_days: String(p.valve_protection.interval_days),
      duration_minutes: String(p.valve_protection.duration_minutes),
    },
    pi: {
      enabled: p.pi.enabled,
      confirm: false,
      gain_per_k: toInput(p.pi.gain_per_k),
      integral_time_minutes: String(p.pi.integral_time_minutes),
      min_on_seconds: String(p.pi.min_on_seconds),
      min_off_seconds: String(p.pi.min_off_seconds),
    },
    window_temp_drop: p.window_temp_drop_detection_enabled,
    sensor_failure: {
      enabled: p.sensor_failure.own_enabled,
      profile_id: p.sensor_failure.own_profile_id === null ? '' : String(p.sensor_failure.own_profile_id),
      emergency_setpoint_c:
        p.sensor_failure.own_emergency_setpoint_c === null ? '' : toInput(p.sensor_failure.own_emergency_setpoint_c),
    },
    backup_offsets: Object.fromEntries((p.backup_offsets ?? []).map((o) => [o.assignment_id, toInput(o.offset_k)])),
  };
}

/** Setzt aus dem gespeicherten Stand und dem Entwurf genau die geänderten Felder zusammen.
 *
 *  Teil-Update-Semantik des Servers:
 *  - Fehlendes Feld = unverändert. Deshalb wird nichts mitgeschickt, was nicht geändert wurde.
 *  - `null` bei einem vererbbaren Feld (Regelwerte, Notbetriebsprofil, Notsollwert) = erben.
 *  - Bei allen anderen Feldern bedeutet ein leerer Wert „unverändert“; sie werden nur
 *    gesendet, wenn sie einen Wert tragen. */
export function buildParametersPatch(p: RoomParameters, d: ParametersDraft): ParametersPatch {
  const patch: ParametersPatch = {};
  for (const name of CONTROL_FIELDS) {
    const own = p.control[name]?.own ?? null;
    const text = d.control[name].trim();
    if (text === '') {
      if (own !== null) patch[name] = null; // Eigenwert entfernt: erben
    } else if (own === null || !sameNumber(own, text)) {
      patch[name] = fromInput(text);
    }
  }
  if (d.solar_gain_factor.trim() !== '' && !sameNumber(d.solar_gain_factor, p.solar_gain_factor)) {
    patch.solar_gain_factor = fromInput(d.solar_gain_factor);
  }
  if (d.valve.enabled !== p.valve_protection.enabled) patch.valve_protection_enabled = d.valve.enabled;
  if (d.valve.interval_days.trim() !== '' && !sameNumber(d.valve.interval_days, String(p.valve_protection.interval_days))) {
    patch.valve_protection_interval_days = fromInput(d.valve.interval_days);
  }
  if (d.valve.duration_minutes.trim() !== '' && !sameNumber(d.valve.duration_minutes, String(p.valve_protection.duration_minutes))) {
    patch.valve_protection_duration_minutes = fromInput(d.valve.duration_minutes);
  }
  if (d.pi.enabled !== p.pi.enabled) {
    patch.pi_enabled = d.pi.enabled;
    // Die Bestätigung gehört nur zum Einschalten; der Server verlangt sie genau dann.
    if (d.pi.enabled) patch.pi_confirm = d.pi.confirm;
  }
  const piNow: Record<(typeof PI_FIELDS)[number], string> = {
    pi_gain_per_k: p.pi.gain_per_k,
    pi_integral_time_minutes: String(p.pi.integral_time_minutes),
    pi_min_on_seconds: String(p.pi.min_on_seconds),
    pi_min_off_seconds: String(p.pi.min_off_seconds),
  };
  const piDraft: Record<(typeof PI_FIELDS)[number], string> = {
    pi_gain_per_k: d.pi.gain_per_k,
    pi_integral_time_minutes: d.pi.integral_time_minutes,
    pi_min_on_seconds: d.pi.min_on_seconds,
    pi_min_off_seconds: d.pi.min_off_seconds,
  };
  for (const name of PI_FIELDS) {
    if (piDraft[name].trim() !== '' && !sameNumber(piDraft[name], piNow[name])) patch[name] = fromInput(piDraft[name]);
  }
  if (d.window_temp_drop !== p.window_temp_drop_detection_enabled) {
    patch.window_temp_drop_detection_enabled = d.window_temp_drop;
  }
  const sf = p.sensor_failure;
  if (d.sensor_failure.enabled !== sf.own_enabled) patch.sensor_failure_enabled = d.sensor_failure.enabled;
  const profile = d.sensor_failure.profile_id === '' ? null : Number(d.sensor_failure.profile_id);
  if (profile !== sf.own_profile_id) patch.sensor_failure_profile_id = profile;
  const setpoint = d.sensor_failure.emergency_setpoint_c.trim();
  if (setpoint === '') {
    if (sf.own_emergency_setpoint_c !== null) patch.sensor_failure_emergency_setpoint_c = null;
  } else if (sf.own_emergency_setpoint_c === null || !sameNumber(sf.own_emergency_setpoint_c, setpoint)) {
    patch.sensor_failure_emergency_setpoint_c = fromInput(setpoint);
  }
  if (p.backup_offsets !== null) {
    const changed = p.backup_offsets.flatMap((o): { assignment_id: number; offset_k: string | null }[] => {
      const text = (d.backup_offsets[o.assignment_id] ?? toInput(o.offset_k)).trim();
      if (text === '') return [{ assignment_id: o.assignment_id, offset_k: null }];
      return sameNumber(text, o.offset_k) ? [] : [{ assignment_id: o.assignment_id, offset_k: fromInput(text) }];
    });
    if (changed.length > 0) patch.backup_offsets = changed;
  }
  return patch;
}

/** Namen der geänderten Felder (für Zähler je Gruppe und „nichts zu speichern“). */
export function patchedFields(patch: ParametersPatch): string[] {
  return Object.keys(patch).filter((k) => k !== 'pi_confirm');
}

/** Pflichtangaben, die der Server sonst stillschweigend als „unverändert“ liest. Nur Form,
 *  keine Fachregel; die Grenzen prüft der Server. */
export function localParameterErrors(p: RoomParameters, d: ParametersDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  const required: [string, string][] = [
    ['solar_gain_factor', d.solar_gain_factor],
    ['valve_protection_interval_days', d.valve.interval_days],
    ['valve_protection_duration_minutes', d.valve.duration_minutes],
    ['pi_gain_per_k', d.pi.gain_per_k],
    ['pi_integral_time_minutes', d.pi.integral_time_minutes],
    ['pi_min_on_seconds', d.pi.min_on_seconds],
    ['pi_min_off_seconds', d.pi.min_off_seconds],
  ];
  for (const [name, text] of required) if (text.trim() === '') errors[name] = 'Bitte einen Wert angeben.';
  if (d.pi.enabled && !p.pi.enabled && !d.pi.confirm) {
    errors.pi_confirm = 'Bitte bestätigen, dass dir die Folgen bewusst sind, bevor PI eingeschaltet wird.';
  }
  return errors;
}

/** Liegt der eingetragene Wert außerhalb der vom Server genannten Grenzen? Reine Warnung zur
 *  Eingabe, kein Ersatz für die Prüfung des Servers (sie lässt das Speichern nicht aus). Leere
 *  und ungültige Eingaben ergeben `false`; die Zahlen werden exakt über ihre Ziffern verglichen. */
export function outsideLimit(text: string, limit: { minimum: string; maximum: string }): boolean {
  const value = fromInput(text);
  if (value === '') return false;
  const low = compareDecimal(value, limit.minimum);
  const high = compareDecimal(value, limit.maximum);
  if (low === null || high === null) return false;
  return low < 0 || high > 0;
}

/** Wirksamer Wert samt Herkunft in einem Satz (Eigenwert gegen Vererbung). */
export function inheritanceNote(source: 'zone' | 'plant' | 'default', inherited: boolean): string {
  if (!inherited) return 'Eigenwert dieses Raums';
  return source === 'plant' ? 'Geerbt von der Anlage' : 'Vorgabe, nicht eigens gesetzt';
}

// -- Anlagenvorgaben und Schwellen -------------------------------------------------------------

/** Der Server verlangt bei den Regelvorgaben alle Felder und die Zeitzone in einem Aufruf.
 *  Was die Seite nicht bearbeitet, wird unverändert aus dem gelesenen Stand übernommen. */
export function defaultsPayload(
  settings: Settings,
  edits: Record<string, string>,
  timezone?: string,
): { values: Record<string, string>; timezone: string } {
  const values: Record<string, string> = {};
  for (const field of settings.defaults) values[field.name] = fromInput(edits[field.name] ?? field.value);
  return { values, timezone: (timezone ?? settings.timezone).trim() };
}

export function thresholdsPayload(fields: readonly NumberField[], edits: Record<string, string>): { values: Record<string, string> } {
  return { values: Object.fromEntries(fields.map((f) => [f.name, fromInput(edits[f.name] ?? f.value)])) };
}

export function draftFromFields(fields: readonly NumberField[]): Record<string, string> {
  return Object.fromEntries(fields.map((f) => [f.name, toInput(f.value)]));
}

export function dirtyFields(fields: readonly NumberField[], draft: Record<string, string>): string[] {
  return fields.filter((f) => draft[f.name] !== undefined && !sameNumber(draft[f.name], f.value)).map((f) => f.name);
}

// -- Notbetrieb der Anlage: eine Einheit ---------------------------------------------------------

export interface CurveRowDraft {
  key: number;
  outdoor_c: string;
  on_seconds: string;
  off_seconds: string;
}

export interface SensorFailureDraft {
  fixed_on_seconds: string;
  fixed_off_seconds: string;
  recovery_seconds: string;
  recovery_samples: string;
  warm_restart_hysteresis_k: string;
  emergency_setpoint_c: string;
  curve: CurveRowDraft[];
}

export function sensorFailureDraft(s: Settings, nextKey: () => number): SensorFailureDraft {
  const sf = s.sensor_failure;
  return {
    fixed_on_seconds: String(sf.fixed_on_seconds),
    fixed_off_seconds: String(sf.fixed_off_seconds),
    recovery_seconds: String(sf.recovery_seconds),
    recovery_samples: String(sf.recovery_samples),
    warm_restart_hysteresis_k: toInput(sf.warm_restart_hysteresis_k),
    emergency_setpoint_c: toInput(sf.emergency_setpoint_c),
    curve: sf.curve_points.map((p) => ({
      key: nextKey(),
      outdoor_c: toInput(p.outdoor_c),
      on_seconds: String(p.on_seconds),
      off_seconds: String(p.off_seconds),
    })),
  };
}

export type SensorFailureBodyResult =
  | { ok: true; body: import('./client').SensorFailureBody }
  | { ok: false; errors: Record<string, string> };

/** Profil, Kurve, Rückkehr und Notsollwert gehen als eine Einheit; der Server prüft alles vor
 *  dem ersten Schreiben. Lokal wird nur geprüft, ob ganze Zahlen dort stehen, wo die Anfrage
 *  Zahlen erwartet -- sonst würde der Server den Aufruf mit einem unklaren Schemafehler ablehnen. */
export function buildSensorFailureBody(d: SensorFailureDraft): SensorFailureBodyResult {
  const errors: Record<string, string> = {};
  const integer = (text: string) => /^\d+$/.test(text.trim());
  const curve = d.curve.map((row, index) => {
    for (const col of ['on_seconds', 'off_seconds'] as const) {
      if (!integer(row[col])) errors[`curve_points[${index}].${col}`] = 'Bitte eine ganze Zahl angeben.';
    }
    return { outdoor_c: fromInput(row.outdoor_c), on_seconds: Number(row.on_seconds.trim()), off_seconds: Number(row.off_seconds.trim()) };
  });
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    body: {
      fixed_on_seconds: d.fixed_on_seconds.trim(),
      fixed_off_seconds: d.fixed_off_seconds.trim(),
      recovery_seconds: d.recovery_seconds.trim(),
      recovery_samples: d.recovery_samples.trim(),
      warm_restart_hysteresis_k: fromInput(d.warm_restart_hysteresis_k),
      emergency_setpoint_c: fromInput(d.emergency_setpoint_c),
      curve_points: curve,
    },
  };
}

export function sensorFailureDirty(s: Settings, d: SensorFailureDraft): boolean {
  const sf = s.sensor_failure;
  const same =
    sameNumber(d.fixed_on_seconds, String(sf.fixed_on_seconds)) &&
    sameNumber(d.fixed_off_seconds, String(sf.fixed_off_seconds)) &&
    sameNumber(d.recovery_seconds, String(sf.recovery_seconds)) &&
    sameNumber(d.recovery_samples, String(sf.recovery_samples)) &&
    sameNumber(d.warm_restart_hysteresis_k, sf.warm_restart_hysteresis_k) &&
    sameNumber(d.emergency_setpoint_c, sf.emergency_setpoint_c) &&
    d.curve.length === sf.curve_points.length &&
    d.curve.every(
      (row, i) =>
        sameNumber(row.outdoor_c, sf.curve_points[i].outdoor_c) &&
        sameNumber(row.on_seconds, String(sf.curve_points[i].on_seconds)) &&
        sameNumber(row.off_seconds, String(sf.curve_points[i].off_seconds)),
    );
  return !same;
}

// -- Sonnenabsenkung (nur Darstellung) -----------------------------------------------------------

/** Rechenbeispiel für die Erklärung. Es ist ausdrücklich keine Wetterprognose und keine
 *  Reglerrechnung: Der Plan-Sollwert ist ein fester Beispielwert, die Differenz nur
 *  die Anzeige der eingetragenen Höchstabsenkung. */
export const SOLAR_EXAMPLE_PLAN = '21.0';

export function solarExample(maxReductionText: string): { plan: string; target: string | null } {
  return { plan: SOLAR_EXAMPLE_PLAN, target: subtractDecimal(SOLAR_EXAMPLE_PLAN, maxReductionText) };
}

// -- Betrieb: die Zustände getrennt ----------------------------------------------------------------

export type Tone = 'ok' | 'warn' | 'neutral';

export interface StateCard {
  id: 'armed' | 'sending' | 'role' | 'dry_run';
  title: string;
  value: string;
  detail: string;
  tone: Tone;
  help: string;
}

/** Gespeicherte Freigabe, Sendefreigabe, Primärrolle und Trockenlauf: vier getrennte
 *  Tatsachen aus der Serverantwort. Keine wird aus einer anderen abgeleitet oder zu einem
 *  gemeinsamen „scharf“ zusammengezogen. */
export function stateCards(a: OperationActuation): StateCard[] {
  return [
    {
      id: 'armed',
      title: 'Gespeicherte Freigabe',
      value: a.armed ? 'Freigegeben' : 'Nicht freigegeben',
      detail: a.armed
        ? 'In der Datenbank steht die Freigabe. Das allein schaltet noch nichts.'
        : 'Es gibt keine gespeicherte Freigabe.',
      tone: a.armed ? 'ok' : 'neutral',
      help: 'armed',
    },
    {
      id: 'sending',
      title: 'Sendefreigabe (MQTT und Meross)',
      value: a.sending_allowed ? 'Offen' : 'Gesperrt',
      detail: a.sending_allowed
        ? 'Der Prozess durfte beim Start senden.'
        : a.restart_required
          ? 'Beim Start war gesperrt. Der Riegel öffnet erst beim Neustart.'
          : 'Beim Start gesperrt; der Prozess sendet nichts an Aktoren.',
      tone: a.sending_allowed ? 'ok' : a.restart_required ? 'warn' : 'neutral',
      help: 'sending',
    },
    {
      id: 'role',
      title: 'Primärrolle im Verbund',
      value: roleOf(a) === 'determining' ? roleLabel(a).value : a.cluster_standby ? 'Standby' : 'Primär',
      detail:
        roleOf(a) === 'determining'
          ? 'Die Instanz ist gerade gestartet und wartet, bis der Anspruch einer eventuell abgestürzten Vorgängerin abläuft. Sie schaltet währenddessen nichts.'
          : a.cluster_standby
            ? 'Diese Instanz wartet. Ein Standby schaltet nicht.'
            : 'Diese Instanz ist primär und darf schalten, sofern Freigabe und Sendefreigabe stehen.',
      tone: roleLabel(a).tone,
      help: 'role',
    },
    {
      id: 'dry_run',
      title: 'Trockenlauf',
      value: a.dry_run ? 'Ja' : 'Nein',
      detail: a.dry_run
        ? 'Entscheidungen werden protokolliert, an Aktoren geht nichts.'
        : 'Freigabe und Sendefreigabe stehen: Entscheidungen werden ausgeführt.',
      tone: a.dry_run ? 'neutral' : 'ok',
      help: 'dry_run',
    },
  ];
}

/** Ein Satz zur Lage, getrennt von den Karten. Aus `state` und `cluster_standby` des Servers. */
export function operationHeadline(a: OperationActuation): { text: string; tone: Tone } {
  if (a.state === 'armed_restart_missing') {
    return {
      text: 'Freigabe gespeichert, aber der Sende-Riegel (MQTT und Meross) öffnet erst beim Neustart. Bis dahin wird nichts geschaltet.',
      tone: 'warn',
    };
  }
  if (a.state === 'live') {
    return a.cluster_standby
      ? {
          text:
            roleOf(a) === 'determining'
              ? 'Freigabe und Sendefreigabe stehen, aber die Primärrolle wird noch ermittelt: Diese Instanz schaltet noch nichts.'
              : 'Freigabe und Sendefreigabe stehen, aber diese Instanz ist Standby und schaltet nicht.',
          tone: 'warn',
        }
      : { text: 'Freigabe und Sendefreigabe stehen: Entscheidungen werden an Aktoren gesendet.', tone: 'ok' };
  }
  return { text: 'Trockenlauf: Entscheidungen werden protokolliert, es wird nichts geschaltet.', tone: 'neutral' };
}

/** Wird eine Entscheidung gerade ausgeführt? Nur wenn der Server `live` meldet und diese
 *  Instanz primär ist. Bei ausstehendem Neustart oder Standby nie. */
export function decisionEffect(a: OperationActuation): { executes: boolean; text: string } {
  if (a.restart_required) return { executes: false, text: 'Wird nicht ausgeführt: Der Sende-Riegel (MQTT und Meross) öffnet erst beim Neustart.' };
  if (a.dry_run) return { executes: false, text: 'Wird nicht ausgeführt: Trockenlauf.' };
  if (a.cluster_standby) {
    return {
      executes: false,
      text: roleOf(a) === 'determining' ? 'Wird nicht ausgeführt: Die Primärrolle wird noch ermittelt.' : 'Wird nicht ausgeführt: Diese Instanz ist Standby.',
    };
  }
  return { executes: true, text: 'Wird an die Aktoren gesendet.' };
}

export interface ArmOutcome {
  tone: 'success' | 'warn' | 'info';
  text: string;
}

/** Ergebnis einer Freigabeänderung, ausschließlich aus der Serverantwort: nicht aus dem,
 *  was angefragt wurde. `restart_required` wird nie als schaltend dargestellt. */
export function armOutcome(requested: boolean, result: ArmedResult): ArmOutcome {
  const a = result.actuation;
  if (requested) {
    if (!a.armed) {
      return { tone: 'warn', text: 'Der Server meldet die Freigabe weiterhin als nicht gespeichert. Bitte den Zustand prüfen.' };
    }
    const lead = result.changed ? 'Freigabe gespeichert.' : 'Keine Änderung: Die Freigabe war bereits gespeichert.';
    if (a.restart_required) {
      return {
        tone: 'warn',
        text: `${lead} Der Sende-Riegel (MQTT und Meross) öffnet aber erst beim Neustart – bis dahin wird nichts geschaltet.`,
      };
    }
    if (a.cluster_standby) {
      return {
        tone: 'info',
        text: `${lead} ${roleOf(a) === 'determining' ? 'Die Primärrolle wird noch ermittelt, bis dahin schaltet diese Instanz nichts.' : 'Diese Instanz ist Standby und schaltet nicht.'}`,
      };
    }
    return a.live
      ? { tone: 'success', text: `${lead} Freigabe und Sendefreigabe stehen: Entscheidungen werden ausgeführt.` }
      : { tone: 'info', text: `${lead} Der Server meldet: ${operationHeadline(a).text}` };
  }
  if (a.armed) {
    return { tone: 'warn', text: 'Der Server meldet die Freigabe weiterhin als gespeichert. Bitte den Zustand prüfen.' };
  }
  return {
    tone: 'success',
    text: result.changed
      ? 'Freigabe zurückgenommen. Trockenlauf: Es wird nichts mehr geschaltet.'
      : 'Keine Änderung: Es war bereits keine Freigabe gespeichert.',
  };
}

// -- Meldungen: Test-Versand -----------------------------------------------------------------------

export interface TestOutcome {
  tone: 'success' | 'error' | 'warn' | 'info';
  text: string;
  /** Wurde tatsächlich etwas gesendet? */
  sent: boolean;
}

export function testOutcomeFromResult(result: NotificationTest): TestOutcome {
  const seconds = result.duration_seconds.toLocaleString('de-DE', { maximumFractionDigits: 2 });
  if (result.ok) {
    return { tone: 'success', sent: true, text: `Testmeldung zugestellt (${seconds} s${result.status_code !== null ? `, Status ${result.status_code}` : ''}).` };
  }
  const detail = result.error ? ` ${result.error}` : '';
  const named = result.error !== null && result.status_code !== null && result.error.includes(String(result.status_code));
  const status = result.status_code !== null && !named ? ` Status ${result.status_code}.` : '';
  return { tone: 'error', sent: true, text: `Versand fehlgeschlagen.${detail}${status}` };
}

export function testOutcomeFromFailure(failure: Failure): TestOutcome {
  if (failure.code === 'no_webhook') {
    return { tone: 'info', sent: false, text: 'Es ist kein Webhook hinterlegt. Es wurde nichts gesendet.' };
  }
  if (failure.code === 'cooldown') {
    return { tone: 'warn', sent: false, text: 'Bitte kurz warten: Der letzte Versuch war gerade eben. Es wurde nichts gesendet.' };
  }
  return { tone: 'error', sent: false, text: failure.message };
}

// -- Schnittstellen ----------------------------------------------------------------------------------

const SECRET_NAME = /PASS|TOKEN|SECRET|KEY|CREDENTIAL/i;
const PLACEHOLDERS = new Set(['hinterlegt', 'nicht hinterlegt', '—', '']);

/** Geheimnisse werden nie angezeigt: Der Server liefert sie bereits als „hinterlegt“; hier wird
 *  zusätzlich jeder Wert hinter einem Geheimnis-Variablennamen auf „hinterlegt“ gesetzt, falls
 *  er doch etwas anderes enthielte. */
export function displayDetailValue(detail: InterfaceDetail): string {
  if (detail.variable !== null && SECRET_NAME.test(detail.variable) && !PLACEHOLDERS.has(detail.value.trim())) {
    return 'hinterlegt';
  }
  return detail.value;
}


// -- PI-Warnung: Schalttabelle ------------------------------------------------------------------

const SWITCHING_LABELS: Record<string, string> = {
  pi_worst_case: 'PI, ungünstigster Fall (Modell)',
  pi_30_percent_duty: 'PI bei 30 % Tastgrad (Modell)',
  hysteresis_max: 'Hysterese, mit Vorgabewerten höchstens (Modell)',
};

const COUNT = new Intl.NumberFormat('de-DE', { useGrouping: true });

/** Ganze Zahl mit Tausenderpunkt (nur Darstellung; die Werte rechnet der Server). */
export function formatCount(value: number): string {
  return COUNT.format(value);
}

export interface SwitchingTableRow {
  id: string;
  label: string;
  perHour: string;
  perDay: string;
  perYear: string;
}

/** Zeilen der Modellrechnung zur Anzeige; es wird nichts gerechnet, nur beschriftet und formatiert. */
export function switchingTable(model: PiSwitchingModel): SwitchingTableRow[] {
  return model.rows.map((row) => ({
    id: row.id,
    label: SWITCHING_LABELS[row.id] ?? row.id,
    perHour: formatCount(row.per_hour),
    perDay: formatCount(row.per_day),
    perYear: formatCount(row.per_year),
  }));
}

/** „rund 0,53 solcher Relaislebensdauern im Jahr“, mit dem vom Server gelieferten Verhältnis. */
export function wearRatioSentence(model: PiSwitchingModel): string {
  return `Mit der in den Regelvorgaben hinterlegten, austauschbaren Annahme von derzeit ${formatCount(model.assumed_lifetime_operations)} elektrischen Betätigungen eines vergleichbaren 16-A-Leistungsrelais (keine Meross-Herstellerangabe – öffentliche Daten dazu fehlen) entspricht der ungünstigste Fall rund ${toInput(model.worst_case_ratio)} solcher Relaislebensdauern im Jahr.`;
}

// -- Notbetrieb: Aktortabelle ----------------------------------------------------------------------

export interface ActuatorCycle {
  /** „Ein“ / „Aus“, `null` ohne laufenden Takt. */
  phase: string | null;
  /** „Kennlinie“ (Takt aus der Außentemperatur) oder „Festtakt“, `null` ohne Quelle. */
  source: string | null;
  /** Ende der Takt-Phase in der Anlagenzeit, `null` ohne Frist. */
  deadline: string | null;
  /** „120 s an / 480 s aus“, `null`, wenn der Takt nicht vollständig bekannt ist. */
  cycle: string | null;
}

/** Wortlaut wie die Alt-Ansicht (`control.html`): Phase Ein/Aus, Takt aus Kennlinie oder Festtakt, Frist. */
export function actuatorCycle(a: EmergencyActuator, timezone: string): ActuatorCycle {
  return {
    phase: a.phase === null ? null : a.phase === 'ein' ? 'Ein' : 'Aus',
    source: a.cycle_source === null ? null : a.cycle_source === 'kennlinie' ? 'Kennlinie' : 'Festtakt',
    deadline: a.phase_deadline_at === null ? null : formatInstant(a.phase_deadline_at, timezone),
    cycle: a.on_seconds !== null && a.off_seconds !== null ? `${a.on_seconds} s an / ${a.off_seconds} s aus` : null,
  };
}

/** Sensor-Timeout eines Raums („90 s“); wie die Alt-Ansicht nur, wenn der Server einen Wert liefert (nicht 0). */
export function sensorTimeoutText(seconds: number | null): string | null {
  return seconds ? `${seconds} s` : null;
}
