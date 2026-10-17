// Anordnung der Karte zwischen Server, gemeinsamer Vorgabe und diesem Browser.
//
// Der Server liefert die persönliche Anordnung, sonst die Vorgabe des Administrators, sonst
// nichts (dann ordnet die Karte selbst an). Lokal bleibt ein Rückfall ohne Netz und der Platz
// für Änderungen, die noch nicht angekommen sind: Sie haben Vorrang, bis der Server sie
// bestätigt. Gespeichert wird entprellt und nie automatisch wiederholt; ein Konflikt (409,
// anderes Gerät war schneller) überschreibt nichts, sondern fragt.
import { ApiError } from '../api';
import type { DevicesClient } from './client';
import type { Placement, View } from './geometry';
import type { MapPrefs } from './storage';
import type { LayoutBody, LayoutNode, ServerLayout } from './types';

export const SAVE_DELAY_MS = 800;

export function prefsFromServer(layout: ServerLayout, technical: boolean): MapPrefs {
  const pos: Placement = {};
  const collapsed: string[] = [];
  for (const n of layout.nodes) {
    if (n.x !== null && n.y !== null) pos[n.key] = { x: n.x, y: n.y };
    if (n.collapsed) collapsed.push(n.key);
  }
  return { pos, view: layout.view ? { ...layout.view } : null, collapsed, technical, dirty: false };
}

/** Ungesendete lokale Änderungen schlagen den Server; sonst gilt, was er liefert
 *  (persönlich vor Vorgabe vor leer). `technical` bleibt immer lokal. */
export function mergeLayout(server: ServerLayout, local: MapPrefs): { prefs: MapPrefs; push: boolean } {
  if (local.dirty) return { prefs: { ...local, pos: { ...local.pos }, collapsed: [...local.collapsed] }, push: true };
  return { prefs: prefsFromServer(server, local.technical), push: false };
}

/** Körper für den Server: nur Knoten, die es auf der Karte gibt (ein gelöschtes Gerät aus
 *  dem lokalen Speicher würde sonst als „nicht verfügbar“ abgelehnt). */
export function layoutBody(prefs: MapPrefs, version: number, known: readonly string[]): LayoutBody {
  const folded = new Set(prefs.collapsed);
  const nodes: LayoutNode[] = [];
  for (const key of known) {
    const at = prefs.pos[key];
    const shut = folded.has(key);
    if (!at && !shut) continue;
    nodes.push({ key, x: at ? at.x : null, y: at ? at.y : null, collapsed: shut });
  }
  const view: View | null = prefs.view;
  return { version, nodes, view: view ? { x: view.x, y: view.y, k: view.k } : null };
}

export type SyncStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'offline' | 'conflict' | 'error';

export interface SyncState {
  status: SyncStatus;
  /** Woher die gezeigte Anordnung stammt; `null`, solange der Server nicht geantwortet hat. */
  source: ServerLayout['source'] | null;
  hasDefault: boolean;
  mayDefault: boolean;
  message: string | null;
}

export interface Timer {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

const realTimer: Timer = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

type Client = Pick<DevicesClient, 'layout' | 'saveLayout' | 'resetLayout' | 'saveDefaultLayout' | 'removeDefaultLayout'>;

export interface SyncDeps {
  client: Client;
  /** Kennungen der Knoten, die die Karte gerade zeigt. */
  ids: () => readonly string[];
  onState: (state: SyncState) => void;
  /** Der Server hat den lokalen Stand bestätigt: `dirty` darf lokal fallen. */
  onClean: () => void;
  timer?: Timer;
  delay?: number;
}

function offlineOrMessage(error: unknown): { status: SyncStatus; message: string } {
  if (error instanceof ApiError && error.kind === 'network') {
    return { status: 'offline', message: 'Der Server ist nicht erreichbar. Die Anordnung bleibt in diesem Browser gespeichert.' };
  }
  const message = error instanceof ApiError && error.message ? error.message : 'Die Anordnung konnte nicht gespeichert werden.';
  return { status: 'error', message };
}

export class LayoutSync {
  private version = 0;
  private defaultVersion = 0;
  private latest: MapPrefs | null = null;
  private handle: unknown = null;
  private running = false;
  private again = false;
  private rev = 0;
  state: SyncState = { status: 'idle', source: null, hasDefault: false, mayDefault: false, message: null };

  constructor(private readonly deps: SyncDeps) {}

  private get conflicted(): boolean {
    return this.state.status === 'conflict';
  }

  private set(patch: Partial<SyncState>) {
    this.state = { ...this.state, ...patch };
    this.deps.onState(this.state);
  }

  private take(layout: ServerLayout) {
    this.version = layout.version;
    this.defaultVersion = layout.default_version;
    this.set({ source: layout.source, hasDefault: layout.has_default, mayDefault: layout.may_set_default });
  }

  /** Beim Öffnen: Serverstand lesen und mit dem lokalen zusammenführen. Ohne Netz (oder bei
   *  einem Fehler) bleibt es beim lokalen Stand (`null`). */
  async load(local: MapPrefs): Promise<MapPrefs | null> {
    let layout: ServerLayout;
    try {
      layout = await this.deps.client.layout();
    } catch (error) {
      this.set(offlineOrMessage(error));
      return null;
    }
    this.take(layout);
    const merged = mergeLayout(layout, local);
    if (merged.push) {
      this.latest = merged.prefs;
      this.schedule(0);
    }
    return merged.prefs;
  }

  /** Eine Änderung ist geschehen: entprellt zum Server. */
  changed(prefs: MapPrefs) {
    this.latest = prefs;
    this.rev += 1;
    if (this.conflicted) return; // bleibt vorgemerkt, bis die Person entschieden hat
    this.set({ status: 'pending', message: null });
    this.schedule(this.deps.delay ?? SAVE_DELAY_MS);
  }

  private schedule(ms: number) {
    const timer = this.deps.timer ?? realTimer;
    if (this.handle !== null) timer.clear(this.handle);
    this.handle = timer.set(() => {
      this.handle = null;
      void this.flush();
    }, ms);
  }

  /** Sofort speichern (Timer abgelaufen, Seite wird verlassen). */
  async flush(): Promise<void> {
    if (!this.latest) return;
    if (this.conflicted) return; // erst entscheidet die Person
    if (this.running) {
      this.again = true;
      return;
    }
    this.running = true;
    const sent = this.rev;
    const prefs = this.latest;
    this.set({ status: 'saving', message: null });
    try {
      const saved = await this.deps.client.saveLayout(layoutBody(prefs, this.version, this.deps.ids()));
      this.take(saved);
      if (this.rev === sent) {
        this.latest = null;
        this.deps.onClean();
        this.set({ status: 'saved', message: null });
      } else {
        this.again = true;
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        this.set({
          status: 'conflict',
          message: 'Die Anordnung wurde inzwischen auf einem anderen Gerät geändert. Ihre Änderung ist hier noch zu sehen und nicht verloren.',
        });
      } else {
        this.set(offlineOrMessage(error));
      }
    } finally {
      this.running = false;
    }
    if (this.again && !this.conflicted) {
      this.again = false;
      this.schedule(this.deps.delay ?? SAVE_DELAY_MS);
    }
  }

  /** Nach einem Konflikt: Serverstand übernehmen, die lokale Änderung wird verworfen. */
  async adoptServer(technical: boolean): Promise<MapPrefs | null> {
    try {
      const layout = await this.deps.client.layout();
      this.take(layout);
      this.latest = null;
      this.set({ status: 'idle', message: null });
      this.deps.onClean();
      return prefsFromServer(layout, technical);
    } catch (error) {
      this.set(offlineOrMessage(error));
      return null;
    }
  }

  /** Nach einem Konflikt: die eigene Anordnung behalten und über den neuen Stand legen. */
  async keepMine(): Promise<void> {
    try {
      this.take(await this.deps.client.layout());
    } catch (error) {
      this.set(offlineOrMessage(error));
      return;
    }
    this.set({ status: 'pending', message: null });
    await this.flush();
  }

  /** Persönliche Anordnung löschen: zurück zur Vorgabe oder zur automatischen Anordnung. */
  async reset(technical: boolean): Promise<MapPrefs | null> {
    const timer = this.deps.timer ?? realTimer;
    if (this.handle !== null) timer.clear(this.handle);
    this.handle = null;
    this.latest = null;
    try {
      const layout = await this.deps.client.resetLayout();
      this.take(layout);
      this.set({ status: 'idle', message: null });
      this.deps.onClean();
      return prefsFromServer(layout, technical);
    } catch (error) {
      this.set(offlineOrMessage(error));
      return null;
    }
  }

  /** Die eigene Anordnung als Vorgabe für alle (nur mit anlagenweitem Recht). */
  async saveDefault(prefs: MapPrefs): Promise<boolean> {
    try {
      this.take(await this.deps.client.saveDefaultLayout(layoutBody(prefs, this.defaultVersion, this.deps.ids())));
      this.set({ status: 'idle', message: null });
      return true;
    } catch (error) {
      const failure = error instanceof ApiError && error.status === 409
        ? { status: 'error' as const, message: 'Die Vorgabe wurde inzwischen geändert. Bitte die Seite neu laden und noch einmal versuchen.' }
        : offlineOrMessage(error);
      this.set(failure);
      return false;
    }
  }

  async removeDefault(): Promise<boolean> {
    try {
      this.take(await this.deps.client.removeDefaultLayout());
      this.set({ status: 'idle', message: null });
      return true;
    } catch (error) {
      this.set(offlineOrMessage(error));
      return false;
    }
  }

  /** Beim Verlassen: ausstehende Änderung nicht dem Timer überlassen. */
  dispose() {
    const timer = this.deps.timer ?? realTimer;
    if (this.handle !== null) timer.clear(this.handle);
    this.handle = null;
    if (this.latest && !this.conflicted) void this.flush();
  }
}
