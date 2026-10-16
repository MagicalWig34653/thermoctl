// Zustand des Wochenplan-Editors für einen Raum.
//
// Der Server ist die Quelle der Wahrheit. Während ein Schreibaufruf läuft, zeigt die Ansicht die
// Absicht nur als Ghost; erst die Antwort ersetzt den Plan. Fehler verwerfen den Ghost und lesen
// den Serverstand neu (Lesen ist gefahrlos) -- außer beim Versionskonflikt, den die Person
// bewusst per „Neu laden“ auflöst. Schreibaufrufe laufen einzeln und werden nie wiederholt.
//
// Rücknahme geht über die Marke des Servers (nur die jüngste Geste, solange niemand danach etwas
// ändert); „Wiederholen“ sendet dieselbe Absicht erneut, ebenfalls gegen die aktuelle Version.

import { baseModeId, blockCovering, blocksOf, findBlock, sameRef, type Block, type BlockRef } from './geometry';
import { scheduleFailure } from './errors';
import { describeIntent, ghostOf, isNoop, selectionAfter, type Ghost, type Intent } from './intents';
import type { ScheduleClient } from './client';
import type { Plan, Preview } from './types';

export type NoticeTone = 'error' | 'warn' | 'info' | 'success';
export interface EditorNotice {
  tone: NoticeTone;
  text: string;
}

interface UndoEntry {
  intent: Intent;
  tokens: string[];
}

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
  let undoEntry = $state<UndoEntry | null>(null);
  let redoIntent = $state<Intent | null>(null);
  let preview = $state<Preview | null>(null);
  let previewError = $state<string | null>(null);
  let generation = 0;

  const base = $derived(plan ? baseModeId(plan.modes) : null);
  const blocks = $derived<Block[]>(plan ? blocksOf(plan.segments, base) : []);
  const selectedBlock = $derived(findBlock(blocks, selected));
  const canEdit = $derived(!!plan && plan.can_edit && !conflict);
  const fullEditor = $derived(canEdit && !!plan?.full_editor);

  function take(next: Plan, prefer: BlockRef | null = null) {
    plan = next;
    const nextBlocks = blocksOf(next.segments, baseModeId(next.modes));
    if (prefer) {
      const hit = blockCovering(nextBlocks, prefer.weekday, prefer.start, prefer.end);
      selected = hit ? { weekday: hit.weekday, start: hit.start, end: hit.end } : null;
    } else if (selected && !findBlock(nextBlocks, selected)) {
      selected = null;
    }
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

  async function reload() {
    notice = null;
    undoEntry = null;
    redoIntent = null;
    await load();
  }

  async function submit(intent: Intent): Promise<boolean> {
    if (!plan || busy || conflict || isNoop(intent)) return false;
    busy = true;
    ghost = ghostOf(intent);
    notice = null;
    try {
      const result = await client.send(zoneId, intent, plan.version);
      generation += 1;
      take(result, selectionAfter(intent));
      if (!result.changed) {
        status = 'Keine Änderung: Der Plan sah schon so aus.';
        notice = { tone: 'info', text: status };
      } else {
        status = describeIntent(intent);
        if (result.undo_tokens.length) undoEntry = { intent, tokens: result.undo_tokens };
        else undoEntry = null;
        redoIntent = null;
      }
      void refreshPreview();
      return true;
    } catch (error) {
      const failure = scheduleFailure(error);
      ghost = null;
      if (failure.kind === 'conflict') {
        conflict = true;
        notice = { tone: 'error', text: failure.message };
      } else {
        notice = { tone: 'error', text: failure.message };
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
    const entry = undoEntry;
    if (!plan || busy || conflict || !entry) return false;
    busy = true;
    notice = null;
    try {
      let result: Plan | null = null;
      for (const token of entry.tokens) result = await client.undo(zoneId, token);
      if (result) take(result);
      undoEntry = null;
      redoIntent = entry.intent;
      status = 'Letzte Änderung zurückgenommen.';
      void refreshPreview();
      return true;
    } catch (error) {
      const failure = scheduleFailure(error);
      undoEntry = null;
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
    const intent = redoIntent;
    if (!intent) return false;
    const ok = await submit(intent);
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
      return !!undoEntry && !busy && !conflict;
    },
    get canRedo() {
      return !!redoIntent && !busy && !conflict;
    },
    get preview() {
      return preview;
    },
    get previewError() {
      return previewError;
    },
    get base() {
      return base;
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
