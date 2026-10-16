// Zustand des Wochenplan-Editors für einen Raum.
//
// Der Server ist die Quelle der Wahrheit. Während ein Schreibaufruf läuft, zeigt die Ansicht die
// Absicht nur als Ghost; erst die Antwort ersetzt den Plan. Fehler verwerfen den Ghost und lesen
// den Serverstand neu (Lesen ist gefahrlos) -- außer beim Versionskonflikt, den die Person
// bewusst per „Neu laden“ auflöst. Schreibaufrufe laufen einzeln und werden nie wiederholt.
//
// Rücknahme geht über die Marken-Kette des Servers: jede Schreibanfrage trägt die zuletzt erhaltene
// Marke als `previous_undo_token`, `undo` liefert die Marke für den nächsten Schritt (oder null).
// So sind mehrere Schritte zurück möglich, solange niemand sonst etwas ändert. „Wiederholen“ sendet
// die zurückgenommene Absicht erneut, ebenfalls gegen die aktuelle Version.

import {
  blockStartingAt,
  blocksOf,
  findBlock,
  sameRef,
  type Block,
  type BlockRef,
} from './geometry';
import { scheduleFailure } from './errors';
import { describeIntent, ghostOf, isNoop, selectionAfter, type Ghost, type Intent } from './intents';
import type { ScheduleClient } from './client';
import type { Plan, Preview } from './types';

export type NoticeTone = 'error' | 'warn' | 'info' | 'success';
export interface EditorNotice {
  tone: NoticeTone;
  text: string;
}

/** So viele Schritte hält der Server in einer Kette (`MAX_UNDO_STEPS`); älteres zeigt die Ansicht nicht mehr an. */
const CHAIN = 20;

export function createEditor(client: ScheduleClient, zoneId: number) {
  let plan = $state<Plan | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let busy = $state(false);
  let ghost = $state<Ghost | null>(null);
  let selected = $state<BlockRef | null>(null);
  let notice = $state<EditorNotice | null>(null);
  let status = $state('');
  let conflict = $state(false);
  /** Marke für den nächsten Rücknahme-Schritt (null: nichts zurückzunehmen). */
  let undoToken = $state<string | null>(null);
  /** Absichten der Kette, die letzte zuletzt -- wird beim Zurücknehmen zur Wiederholen-Liste. */
  let done = $state<Intent[]>([]);
  let redoStack = $state<Intent[]>([]);
  let preview = $state<Preview | null>(null);
  let previewError = $state<string | null>(null);
  let generation = 0;

  const blocks = $derived<Block[]>(plan ? blocksOf(plan.segments) : []);
  const selectedBlock = $derived(findBlock(blocks, selected));
  const canEdit = $derived(!!plan && plan.can_edit && !conflict);
  const fullEditor = $derived(canEdit && !!plan?.full_editor);

  function take(next: Plan, prefer: BlockRef | null = null) {
    plan = next;
    const nextBlocks = blocksOf(next.segments);
    // Gesucht wird der Block, der dort beginnt: sein Ende kann der Server anders gesetzt haben.
    const keep = prefer ?? selected;
    const hit = prefer ? blockStartingAt(nextBlocks, prefer) : findBlock(nextBlocks, keep);
    selected = hit ? { weekday: hit.weekday, start: hit.start, end: hit.end } : null;
  }

  async function refreshPreview() {
    try {
      preview = await client.preview(zoneId);
      previewError = null;
    } catch (error) {
      previewError = scheduleFailure(error).message;
    }
  }

  /** Serverstand lesen (Erstladen, „Neu laden“, nach Fehlern). */
  async function load(options: { quiet?: boolean } = {}) {
    const mine = ++generation;
    if (!options.quiet) loading = true;
    try {
      const next = await client.plan(zoneId);
      if (mine !== generation) return;
      take(next);
      loadError = null;
      conflict = false;
    } catch (error) {
      if (mine !== generation) return;
      loadError = scheduleFailure(error).message;
    } finally {
      if (mine === generation) loading = false;
    }
    await refreshPreview();
  }

  function resetHistory() {
    undoToken = null;
    done = [];
    redoStack = [];
  }

  async function reload() {
    notice = null;
    resetHistory();
    await load();
  }

  async function submit(intent: Intent, redoing = false): Promise<boolean> {
    if (!plan || busy || conflict || isNoop(intent)) return false;
    busy = true;
    ghost = ghostOf(intent);
    notice = null;
    try {
      const result = await client.send(zoneId, intent, plan.version, undoToken);
      generation += 1;
      take(result, selectionAfter(intent));
      if (!result.changed) {
        status = 'Keine Änderung: Der Plan sah schon so aus.';
        notice = { tone: 'info', text: status };
      } else {
        status = describeIntent(intent);
        undoToken = result.undo_token;
        done = result.undo_token ? [...done, intent].slice(-CHAIN) : [];
        if (!redoing) redoStack = [];
      }
      void refreshPreview();
      return true;
    } catch (error) {
      const failure = scheduleFailure(error);
      ghost = null;
      if (redoing) redoStack = [];
      notice = { tone: 'error', text: failure.message };
      if (failure.kind === 'conflict') {
        conflict = true;
      } else {
        status = failure.message;
        if (failure.reload) await load({ quiet: true });
      }
      return false;
    } finally {
      ghost = null;
      busy = false;
    }
  }

  async function undo(): Promise<boolean> {
    const token = undoToken;
    if (!plan || busy || conflict || !token) return false;
    busy = true;
    notice = null;
    try {
      const result = await client.undo(zoneId, token);
      generation += 1;
      take(result);
      const undone = done[done.length - 1];
      done = result.undo_token ? done.slice(0, -1) : [];
      undoToken = result.undo_token;
      if (undone) redoStack = [...redoStack, undone];
      status = 'Letzte Änderung zurückgenommen.';
      void refreshPreview();
      return true;
    } catch (error) {
      const failure = scheduleFailure(error);
      resetHistory();
      if (failure.kind === 'conflict') conflict = true;
      notice = { tone: 'error', text: failure.message };
      status = failure.message;
      if (failure.reload) await load({ quiet: true });
      return false;
    } finally {
      busy = false;
    }
  }

  async function redo(): Promise<boolean> {
    const intent = redoStack[redoStack.length - 1];
    if (!intent) return false;
    redoStack = redoStack.slice(0, -1);
    const ok = await submit(intent, true);
    if (ok) status = `Wiederholt: ${describeIntent(intent)}`;
    return ok;
  }

  function select(ref: BlockRef | null) {
    selected = ref && selected && sameRef(ref, selected) ? selected : ref;
  }

  return {
    get plan() {
      return plan;
    },
    get loading() {
      return loading;
    },
    get loadError() {
      return loadError;
    },
    get busy() {
      return busy;
    },
    get ghost() {
      return ghost;
    },
    get selected() {
      return selected;
    },
    get selectedBlock() {
      return selectedBlock;
    },
    get notice() {
      return notice;
    },
    set notice(value: EditorNotice | null) {
      notice = value;
      if (value) status = value.text;
    },
    get status() {
      return status;
    },
    get conflict() {
      return conflict;
    },
    get canUndo() {
      return !!undoToken && !busy && !conflict;
    },
    get canRedo() {
      return redoStack.length > 0 && !busy && !conflict;
    },
    get preview() {
      return preview;
    },
    get previewError() {
      return previewError;
    },
    get blocks() {
      return blocks;
    },
    get canEdit() {
      return canEdit;
    },
    get fullEditor() {
      return fullEditor;
    },
    load,
    reload,
    submit,
    undo,
    redo,
    select,
  };
}

export type Editor = ReturnType<typeof createEditor>;
