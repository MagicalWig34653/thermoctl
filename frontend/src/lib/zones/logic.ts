// Reine Logik für Raumformular, Löschfolgen, Meldeformular und Glossarsuche (ohne Svelte,
// damit sie mit node:test prüfbar ist). Die Fachprüfung liegt auf dem Server; hier steht nur,
// was das Formular vor dem Senden abfängt, damit niemand auf einen vermeidbaren Fehler wartet.
import type {
  DeleteImpact,
  GlossaryEntry,
  RoomCreateBody,
  RoomForm,
  RoomPatch,
  RoomValues,
} from './types';

export interface FieldProblem {
  field: keyof RoomValues;
  message: string;
}

export interface FormLimits {
  name_limit: number;
  display_name_limit: number;
}

const SORT_LIMIT = 2 ** 31 - 1;

export function valuesOf(room: RoomForm | null, defaultMode: string): RoomValues {
  return room
    ? {
        name: room.name,
        display_name: room.display_name,
        operating_mode: room.operating_mode,
        sort_order: String(room.sort_order),
      }
    : { name: '', display_name: '', operating_mode: defaultMode, sort_order: '0' };
}

/** Das erste Problem der Eingabe oder `null`. Reihenfolge wie im Formular. */
export function validateRoomValues(values: RoomValues, limits: FormLimits): FieldProblem | null {
  const name = values.name.trim();
  if (!name) return { field: 'name', message: 'Bitte einen technischen Namen eingeben.' };
  if (name.length > limits.name_limit) {
    return { field: 'name', message: `Der technische Name darf höchstens ${limits.name_limit} Zeichen haben.` };
  }
  const display = values.display_name.trim();
  if (!display) return { field: 'display_name', message: 'Bitte einen Anzeigenamen eingeben.' };
  if (display.length > limits.display_name_limit) {
    return { field: 'display_name', message: `Der Anzeigename darf höchstens ${limits.display_name_limit} Zeichen haben.` };
  }
  if (!values.operating_mode) return { field: 'operating_mode', message: 'Bitte eine Betriebsart auswählen.' };
  const order = values.sort_order.trim();
  if (!/^-?\d+$/.test(order) || Math.abs(Number(order)) > SORT_LIMIT) {
    return { field: 'sort_order', message: 'Bitte eine ganze Zahl eingeben.' };
  }
  return null;
}

export function createBody(values: RoomValues): RoomCreateBody {
  return {
    name: values.name.trim(),
    display_name: values.display_name.trim(),
    operating_mode: values.operating_mode,
    sort_order: Number(values.sort_order.trim()),
  };
}

/** Nur geänderte Felder: ein Teil-Aufruf überschreibt nie, was die Maske nicht kennt. */
export function patchOf(values: RoomValues, original: RoomForm): RoomPatch {
  const patch: RoomPatch = {};
  const body = createBody(values);
  if (body.name !== original.name) patch.name = body.name;
  if (body.display_name !== original.display_name) patch.display_name = body.display_name;
  if (body.operating_mode !== original.operating_mode) patch.operating_mode = body.operating_mode;
  if (body.sort_order !== original.sort_order) patch.sort_order = body.sort_order;
  return patch;
}

export interface ImpactLine {
  label: string;
  count: number;
}

/** Was mit dem Raum verschwindet, nur die Posten mit Inhalt. */
export function impactLines(impact: DeleteImpact): ImpactLine[] {
  const all: ImpactLine[] = [
    { label: 'Schaltpunkte im Wochenplan', count: impact.schedule_points },
    { label: 'Zuordnungen von Geräten', count: impact.devices },
    { label: 'Temperaturen je Modus', count: impact.setpoints },
    { label: 'Übersteuerungen', count: impact.overrides },
    { label: 'Aufgezeichnete Regelentscheidungen', count: impact.shadow_decisions },
  ];
  return all.filter((line) => line.count > 0);
}

export function impactTotal(impact: DeleteImpact): number {
  return impactLines(impact).reduce((sum, line) => sum + line.count, 0);
}

/** Zeichen, die in der Notiz noch frei sind (nie negativ). */
export function noteRemaining(note: string, limit: number): number {
  return Math.max(0, limit - [...note].length);
}

// -- Glossar ---------------------------------------------------------------------------------

const fold = (text: string) => text.toLocaleLowerCase('de');

/** Treffer sind Einträge, in deren Begriff, Synonymen, Kurzfassung oder Erklärung jedes
 *  Suchwort vorkommt (Groß-/Kleinschreibung egal). Ein Bereich schränkt zusätzlich ein. */
export function filterGlossary(
  entries: readonly GlossaryEntry[],
  query: string,
  area: string | null,
): GlossaryEntry[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return entries.filter((entry) => {
    if (area !== null && entry.area !== area) return false;
    if (words.length === 0) return true;
    const text = fold([entry.term, ...entry.synonyms, entry.short, entry.explanation].join(' '));
    return words.every((word) => text.includes(word));
  });
}

export interface LetterGroup {
  letter: string;
  entries: GlossaryEntry[];
}

export function groupByLetter(entries: readonly GlossaryEntry[]): LetterGroup[] {
  const groups: LetterGroup[] = [];
  for (const entry of entries) {
    const last = groups[groups.length - 1];
    if (last && last.letter === entry.letter) last.entries.push(entry);
    else groups.push({ letter: entry.letter, entries: [entry] });
  }
  return groups;
}

export function termOf(entries: readonly GlossaryEntry[], id: string): string {
  return entries.find((entry) => entry.id === id)?.term ?? id;
}

export interface RoomShortcut {
  label: string;
  to: string;
}

/** Sprunglinks einer Raumzeile der Räume-Liste: Zeitplan, Geräte und Datenfluss, aber nur die
 *  Ansichten, die diese Person überhaupt sieht (Darstellung, jeder Endpunkt prüft erneut). */
export function roomShortcuts(
  areas: readonly { id: string; pages: readonly { id: string }[] }[],
  roomId: number,
): RoomShortcut[] {
  const has = (area: string, page: string) =>
    areas.some((a) => a.id === area && a.pages.some((p) => p.id === page));
  const out: RoomShortcut[] = [];
  if (has('zeit', 'wochenplaene')) out.push({ label: 'Zeitplan', to: `/zeit/wochenplaene?zone=${roomId}` });
  if (has('geraete', 'liste')) out.push({ label: 'Geräte', to: '/geraete/liste' });
  if (has('geraete', 'karte')) out.push({ label: 'Datenfluss', to: '/geraete/karte' });
  return out;
}
