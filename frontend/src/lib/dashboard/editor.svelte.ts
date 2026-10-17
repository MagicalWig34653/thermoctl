// Zustand des Dashboards: gelesenes Layout (im Takt), Entwurf mit Rücknahme, Speichern.
//
//  - Der Entwurf ist Browserzustand; erst `save` schreibt (PUT mit der gelesenen Version).
//  - Schreibende Aufrufe laufen höchstens einmal gleichzeitig und werden nie wiederholt.
//  - Bei 409 bleibt der Entwurf stehen; „Neu laden“ holt Version und Serverstand, ohne ihn
//    zu verwerfen. Erst das Speichern danach überschreibt bewusst die andere Änderung.

import type { Api } from '../api';
import { createResource } from '../rooms/resource.svelte';
import {
  commit,
  createHistory,
  isDirty,
  redo as redoHistory,
  toDraft,
  toPayload,
  undo as undoHistory,
  validate,
  type History,
  type Problem,
  type Sections,
} from './draft';
import { dashboardFailure, type DashboardFailure } from './errors';
import type { Layout } from './types';

/** Woher das Layout kommt und wohin es geht. Vorgabe: die eigene Übersicht; Administratoren
 *  setzen die Übersicht eines Benutzers oder eine Vorlage ein. `reset` fehlt, wo es keinen
 *  „Standard“ gibt (Vorlage). */
export interface DashboardTarget {
  load(): Promise<Layout>;
  save(payload: ReturnType<typeof toPayload>): Promise<Layout>;
  reset?(): Promise<Layout>;
}

export const ownTarget = (api: Api): DashboardTarget => ({
  load: () => api.get<Layout>('/ui-api/dashboard'),
  save: (payload) => api.put<Layout>('/ui-api/dashboard', payload),
  reset: () => api.delete<Layout>('/ui-api/dashboard'),
});

export function createDashboard(api: Api, target: DashboardTarget = ownTarget(api)) {
  const layout = createResource(() => target.load());

  let editing = $state(false);
  let history = $state.raw<History>(createHistory([]));
  let base = $state.raw<Layout | null>(null);
  let saving = $state(false);
  let failure = $state<DashboardFailure | null>(null);
  let problems = $state.raw<Problem[]>([]);
  let conflict = $state(false);
  let info = $state<string | null>(null);

  const snapshot = (value: Layout): Layout => JSON.parse(JSON.stringify(value)) as Layout;

  function clearMessages() {
    failure = null;
    problems = [];
    info = null;
  }

  return {
    layout,
    get editing() {
      return editing;
    },
    get draft(): Sections {
      return history.present;
    },
    get base() {
      return base;
    },
    get dirty() {
      return editing && isDirty(base, history.present);
    },
    get canUndo() {
      return history.past.length > 0;
    },
    get canRedo() {
      return history.future.length > 0;
    },
    get canReset() {
      return target.reset !== undefined;
    },
    get saving() {
      return saving;
    },
    get failure() {
      return failure;
    },
    get problems() {
      return problems;
    },
    get conflict() {
      return conflict;
    },
    get info() {
      return info;
    },

    enter(): boolean {
      const data = layout.data;
      if (!data || editing) return false;
      base = snapshot(data);
      history = createHistory(toDraft(base.sections));
      conflict = false;
      clearMessages();
      editing = true;
      return true;
    },

    /** Änderung am Entwurf; `coalesce` fasst aufeinanderfolgende Eingaben in ein Feld zusammen. */
    apply(change: (s: Sections) => Sections, coalesce: string | null = null) {
      if (!editing) return;
      const next = change(history.present);
      if (next === history.present) return;
      history = commit(history, next, coalesce);
      failure = failure?.kind === 'conflict' ? failure : null;
      problems = [];
      info = null;
    },
    undo() {
      history = undoHistory(history);
    },
    redo() {
      history = redoHistory(history);
    },

    cancel() {
      editing = false;
      conflict = false;
      clearMessages();
    },

    async save(): Promise<boolean> {
      if (!editing || saving || !base) return false;
      const found = validate(history.present);
      if (found.length > 0) {
        problems = found;
        failure = { kind: 'validation', message: found[0].message, field: found[0].field };
        return false;
      }
      saving = true;
      clearMessages();
      try {
        const saved = await target.save(toPayload(base.version, history.present));
        layout.adopt(saved);
        editing = false;
        conflict = false;
        return true;
      } catch (error) {
        const f = dashboardFailure(error);
        failure = f;
        conflict = f.kind === 'conflict';
        return false;
      } finally {
        saving = false;
      }
    },

    /** Standard wiederherstellen: löscht die gespeicherte Anordnung (DELETE), nie einen Raum. */
    async reset(): Promise<boolean> {
      if (saving || !target.reset) return false;
      saving = true;
      clearMessages();
      try {
        const fresh = await target.reset();
        layout.adopt(fresh);
        editing = false;
        conflict = false;
        return true;
      } catch (error) {
        failure = dashboardFailure(error);
        return false;
      } finally {
        saving = false;
      }
    },

    /** Nach 409: Serverstand und Version holen, den Entwurf behalten. */
    async reloadKeepingDraft(): Promise<boolean> {
      try {
        const fresh = await target.load();
        layout.adopt(fresh);
        base = snapshot(fresh);
        conflict = false;
        failure = null;
        info = 'Der Serverstand ist geladen. Dein Entwurf ist unverändert; Speichern ersetzt jetzt die andere Änderung.';
        return true;
      } catch (error) {
        failure = dashboardFailure(error);
        return false;
      }
    },

    /** Nach 409: Entwurf verwerfen und den Serverstand zeigen. */
    async discardForServer(): Promise<boolean> {
      try {
        const fresh = await target.load();
        layout.adopt(fresh);
        editing = false;
        conflict = false;
        clearMessages();
        return true;
      } catch (error) {
        failure = dashboardFailure(error);
        return false;
      }
    },
  };
}

export type Dashboard = ReturnType<typeof createDashboard>;
