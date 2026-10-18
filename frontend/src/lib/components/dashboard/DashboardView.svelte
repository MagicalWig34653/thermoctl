<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ArrowDown, ArrowUp, GripVertical, Pencil, Plus, Redo2, RotateCcw, Settings2, Trash2, Undo2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { createResource } from '$lib/rooms/resource.svelte';
  import { formatInstant } from '$lib/rooms/logic';
  import type { Rooms } from '$lib/rooms/types';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import PlantStatus from '$lib/components/rooms/PlantStatus.svelte';
  import { historyKey } from '$lib/schedule/keyboard';
  import { createDashboard, type DashboardTarget } from '$lib/dashboard/editor.svelte';
  import { createDrag } from '$lib/dashboard/drag.svelte';
  import { entryOf } from '$lib/dashboard/catalog';
  import {
    addSection,
    addTile,
    canAddSection,
    findTile,
    makeTile,
    moveSectionBefore,
    moveSectionBy,
    moveTile,
    moveTileBefore,
    moveTileBy,
    positionOf,
    removeSection,
    removeTile,
    renameSection,
    updateTile,
    type TilePatch,
  } from '$lib/dashboard/draft';
  import { sectionMove, tileMove } from '$lib/dashboard/keyboard';
  import { tileTitle } from '$lib/dashboard/tiles';
  import { MAX_SECTIONS, MAX_TITLE_LENGTH, type DraftSection, type DraftTile, type TileKind } from '$lib/dashboard/types';
  import Gallery from './Gallery.svelte';
  import TileBody from './TileBody.svelte';
  import TileSettings from './TileSettings.svelte';

  // Ohne Angaben ist das die eigene Übersicht. Mit `target` pflegt ein Administrator die Übersicht
  // eines Benutzers oder eine Vorlage: andere Überschrift, Anpassen startet sofort, keine Anlagenhinweise.
  let {
    target,
    heading = 'Übersicht',
    lead = 'Dein persönliches Dashboard: Räume, Verläufe und Anlagenzustand.',
    autoEdit = false,
    readonly = false,
  }: { target?: DashboardTarget; heading?: string; lead?: string; autoEdit?: boolean; readonly?: boolean } = $props();
  // Das Ziel gilt für die Lebensdauer der Ansicht (der Aufrufer setzt sie per `{#key}` neu).
  // svelte-ignore state_referenced_locally
  const foreign = target !== undefined;

  const { api, session } = useApp();
  const rooms = createResource(() => api.get<Rooms>('/ui-api/rooms'));
  // svelte-ignore state_referenced_locally
  const dash = createDashboard(api, target);
  const layout = dash.layout;
  onMount(() => {
    const stopRooms = rooms.start();
    const stopLayout = layout.start();
    return () => {
      stopRooms();
      stopLayout();
    };
  });

  let announcement = $state('');
  let confirm = $state<'reset' | 'cancel' | null>(null);
  let galleryOpen = $state(false);
  let galleryTarget = $state('');
  let settingsOpen = $state(false);
  let settingsKey = $state<string | null>(null);
  let highlight = $state<string | null>(null);
  let barEl = $state<HTMLElement | null>(null);

  const data = $derived(rooms.data);
  // Räume aus der frischen Raumliste (Rechte gelten bei jedem Abruf), nicht aus dem Bootstrap.
  const zones = $derived((data?.rooms ?? []).map((r) => ({ id: r.id, name: r.name })));
  const zoneIds = $derived(new Set(zones.map((z) => z.id)));
  const ZONE_KINDS = new Set<TileKind>(['room', 'chart', 'schedule', 'heating', 'control']);

  const shown = $derived<readonly DraftSection[]>(
    dash.editing
      ? dash.draft
      : (layout.data?.sections ?? []).map((s, i) => ({
          key: `v${i}`,
          title: s.title,
          tiles: s.tiles.map((t, j) => ({ ...t, key: `v${i}-${j}` })),
        })),
  );
  // Ansicht: was der Server nicht (mehr) liefert, fehlt einfach -- auch leere Abschnitte.
  const visible = $derived(
    dash.editing
      ? shown
      : shown
          .map((s) => ({ ...s, tiles: s.tiles.filter((t) => !ZONE_KINDS.has(t.kind) || (t.zone_id !== null && zoneIds.has(t.zone_id))) }))
          .filter((s) => s.tiles.length > 0),
  );

  const label = (tile: DraftTile) => {
    const room = tile.zone_id === null ? null : zones.find((z) => z.id === tile.zone_id);
    return room ? `${tileTitle(tile)} – ${room.name}` : tileTitle(tile);
  };
  const sectionName = (key: string) => dash.draft.find((s) => s.key === key)?.title || 'Ohne Titel';

  const drag = createDrag({
    moveTile: (tileKey, sectionKey, beforeKey) => dash.apply((s) => moveTileBefore(s, tileKey, sectionKey, beforeKey)),
    moveSection: (key, beforeKey) => dash.apply((s) => moveSectionBefore(s, key, beforeKey)),
    announce: (t) => (announcement = t),
    label: (kind, key) => {
      if (kind === 'section') return `Abschnitt „${sectionName(key)}“`;
      const at = findTile(dash.draft, key);
      return at ? `Kachel „${label(dash.draft[at.section].tiles[at.index])}“` : 'Kachel';
    },
    sectionLabel: (key) => `„${sectionName(key)}“`,
  });

  function dropMark(tile: DraftTile, section: DraftSection): 'before' | 'after' | undefined {
    const d = drag.drop;
    if (!d || d.sectionKey !== section.key || drag.active?.kind !== 'tile') return undefined;
    if (d.beforeKey === tile.key) return 'before';
    if (d.beforeKey === null) {
      const last = [...section.tiles].reverse().find((t) => t.key !== drag.active?.key);
      if (last?.key === tile.key) return 'after';
    }
    return undefined;
  }
  function sectionMark(section: DraftSection, index: number): string | undefined {
    if (drag.active?.kind === 'tile' && drag.drop?.sectionKey === section.key && drag.drop.beforeKey === null) {
      return section.tiles.filter((t) => t.key !== drag.active?.key).length === 0 ? 'end' : undefined;
    }
    if (drag.active?.kind === 'section' && drag.sectionDrop) {
      if (drag.sectionDrop.beforeKey === section.key) return 'section-before';
      if (drag.sectionDrop.beforeKey === null && index === dash.draft.length - 1 && drag.active.key !== section.key) return 'section-after';
    }
    return undefined;
  }

  async function focusHandle(selector: string) {
    await tick();
    document.querySelector<HTMLElement>(selector)?.focus();
  }

  function onTileKey(e: KeyboardEvent, tile: DraftTile) {
    const dir = tileMove(e);
    if (dir === null) return;
    e.preventDefault();
    const before = findTile(dash.draft, tile.key);
    dash.apply((s) => moveTileBy(s, tile.key, dir));
    const pos = positionOf(dash.draft, tile.key);
    const moved = !!pos && !!before && (pos.sectionIndex !== before.section || pos.index !== before.index);
    announcement = !pos ? '' : moved ? `${label(tile)}: Position ${pos.index + 1} von ${pos.count} in „${pos.sectionTitle || 'Ohne Titel'}“.` : `${label(tile)} steht schon ganz ${dir < 0 ? 'vorn' : 'hinten'}.`;
    void focusHandle(`[data-handle-tile="${tile.key}"]`);
  }
  function onSectionKey(e: KeyboardEvent, section: DraftSection) {
    const dir = sectionMove(e);
    if (dir === null) return;
    e.preventDefault();
    moveSectionStep(section, dir);
  }
  function moveSectionStep(section: DraftSection, dir: -1 | 1) {
    dash.apply((s) => moveSectionBy(s, section.key, dir));
    const index = dash.draft.findIndex((s) => s.key === section.key);
    announcement = `Abschnitt „${section.title || 'Ohne Titel'}“: Position ${index + 1} von ${dash.draft.length}.`;
    void focusHandle(`[data-handle-section="${section.key}"]`);
  }

  function removeTileAt(tile: DraftTile) {
    dash.apply((s) => removeTile(s, tile.key));
    const extra = tile.kind === 'room' ? ' Der Raum selbst bleibt bestehen.' : '';
    announcement = `Kachel „${label(tile)}“ entfernt.${extra} Mit Rückgängig wiederherstellen.`;
    void focusHandle('[data-testid="undo"]');
  }
  function removeSectionAt(section: DraftSection) {
    dash.apply((s) => removeSection(s, section.key));
    announcement = `Abschnitt „${section.title || 'Ohne Titel'}“ entfernt. Räume bleiben bestehen. Mit Rückgängig wiederherstellen.`;
    void focusHandle('[data-testid="undo"]');
  }
  async function newSection() {
    let key = '';
    dash.apply((s) => {
      const next = addSection(s, 'Neuer Abschnitt');
      key = next.length > s.length ? next[next.length - 1].key : '';
      return next;
    });
    if (key) {
      announcement = 'Neuer Abschnitt hinzugefügt.';
      await tick();
      const input = document.querySelector<HTMLInputElement>(`[data-title-section="${key}"]`);
      input?.focus();
      input?.select();
    }
  }

  function addFromGallery(kind: TileKind, sectionKey: string | 'new', zoneId: number | null): string | null {
    const entry = entryOf(kind);
    if (entry.needsZone && zoneId === null) return null;
    const tile = makeTile(entry, zoneId);
    let target = sectionKey;
    let done = false;
    dash.apply((s) => {
      let base = s;
      if (sectionKey === 'new') {
        base = addSection(s, 'Neuer Abschnitt');
        if (base === s) return s;
        target = base[base.length - 1].key;
      }
      const next = addTile(base, target, tile);
      done = next !== base;
      return done ? next : s;
    });
    if (!done) return null;
    galleryTarget = target;
    highlight = tile.key;
    const pos = positionOf(dash.draft, tile.key);
    const text = `„${tileTitle(tile)}“ in „${pos?.sectionTitle || 'Ohne Titel'}“ als Kachel ${(pos?.index ?? 0) + 1} hinzugefügt.`;
    announcement = text;
    return text;
  }

  async function afterGallery() {
    if (!highlight) return;
    const key = highlight;
    await tick();
    const el = document.querySelector<HTMLElement>(`[data-tile-key="${key}"]`);
    el?.scrollIntoView({ block: 'center', behavior: 'auto' });
    el?.classList.add('dash-flash');
    window.setTimeout(() => el?.classList.remove('dash-flash'), 1800);
    highlight = null;
  }
  $effect(() => {
    if (!galleryOpen) void afterGallery();
  });

  function openGallery(sectionKey = '') {
    galleryTarget = sectionKey || dash.draft[0]?.key || '';
    galleryOpen = true;
  }
  function openSettings(tile: DraftTile) {
    settingsKey = tile.key;
    settingsOpen = true;
  }
  const settingsTile = $derived(settingsKey ? (dash.draft.flatMap((s) => s.tiles).find((t) => t.key === settingsKey) ?? null) : null);
  const onPatch = (key: string, patch: TilePatch, field: string) => dash.apply((s) => updateTile(s, key, patch), `${key}:${field}`);
  const onMoveToSection = (key: string, sectionKey: string) =>
    dash.apply((s) => moveTile(s, key, sectionKey, Number.MAX_SAFE_INTEGER));

  let autoStarted = false;
  $effect(() => {
    if (autoEdit && !autoStarted && layout.data && data && !dash.editing) {
      autoStarted = true;
      void startEdit();
    }
  });

  async function startEdit() {
    if (!dash.enter()) return;
    announcement = 'Anpassungsmodus. Kacheln lassen sich ziehen oder mit den Pfeiltasten am Griff verschieben.';
    await tick();
    barEl?.focus();
  }
  async function save() {
    confirm = null;
    if (await dash.save()) {
      announcement = 'Layout gespeichert.';
      await tick();
      document.querySelector<HTMLElement>('[data-testid="edit-layout"]')?.focus();
    }
  }
  async function cancel() {
    if (dash.dirty && confirm !== 'cancel') {
      confirm = 'cancel';
      return;
    }
    confirm = null;
    dash.cancel();
    announcement = 'Anpassung abgebrochen.';
    await tick();
    document.querySelector<HTMLElement>('[data-testid="edit-layout"]')?.focus();
  }
  async function reset() {
    if (confirm !== 'reset') {
      confirm = 'reset';
      return;
    }
    confirm = null;
    if (await dash.reset()) {
      announcement = 'Standardlayout wiederhergestellt.';
      await tick();
      document.querySelector<HTMLElement>('[data-testid="edit-layout"]')?.focus();
    }
  }

  function onWindowKey(e: KeyboardEvent) {
    if (!dash.editing || galleryOpen || settingsOpen) return;
    const action = historyKey({ key: e.key, altKey: e.altKey, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, target: e.target as Element | null });
    if (!action) return;
    e.preventDefault();
    if (action === 'undo') dash.undo();
    else dash.redo();
  }
  function onBeforeUnload(e: BeforeUnloadEvent) {
    if (!dash.dirty) return;
    e.preventDefault();
    e.returnValue = '';
  }

  const loadingAll = $derived((rooms.loading && !data) || (layout.loading && !layout.data));
  const failedAll = $derived(!data || !layout.data);
  const handleCls = 'dash-handle inline-grid size-9 shrink-0 place-items-center rounded-lg border bg-background text-muted-foreground hover:bg-muted hover:text-foreground';
</script>

<svelte:window onkeydown={onWindowKey} onbeforeunload={onBeforeUnload} />

<header class="mb-6 flex flex-wrap items-start justify-between gap-3">
  <div class="min-w-0">
    <h1>{heading}</h1>
    <p class="text-muted-foreground">{lead}</p>
  </div>
  {#if layout.data && data && !dash.editing && !readonly}
    <Button variant="outline" onclick={startEdit} data-testid="edit-layout"><Pencil aria-hidden="true" />Anpassen</Button>
  {/if}
</header>

<p class="sr-only" role="status" aria-live="polite" data-testid="announcer">{announcement}</p>
<p id="dash-move-hint" class="sr-only">Zum Verschieben ziehen oder mit den Pfeiltasten bewegen.</p>

{#if loadingAll}
  <p class="text-muted-foreground" role="status">Dashboard wird geladen …</p>
{:else if failedAll}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{layout.failure?.message ?? rooms.failure?.message ?? 'Das Dashboard konnte nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => { void rooms.refresh(); void layout.refresh(); }}>Erneut versuchen</Button>
  </div>
{:else if data && layout.data}
  <div class="space-y-6">
    {#if rooms.failure || layout.failure}
      <Notice tone="warn">
        Aktualisieren fehlgeschlagen: {(rooms.failure ?? layout.failure)?.message}
        {#if rooms.updatedAt}Angezeigt ist der Stand von {formatInstant(new Date(rooms.updatedAt).toISOString(), data.timezone)}.{/if}
      </Notice>
    {/if}

    {#if !foreign}<PlantStatus {data} />{/if}

    {#if dash.editing}
      <div class="dash-bar p-2" bind:this={barEl} tabindex="-1" role="toolbar" aria-label="Dashboard anpassen" data-testid="edit-bar">
        <div class="flex flex-wrap items-center gap-2">
          <span class="px-2 text-sm font-medium">Anpassen{dash.dirty ? ' · ungespeicherte Änderungen' : ''}</span>
          <div class="ml-auto flex flex-wrap items-center gap-1">
            <Button variant="outline" size="icon" aria-label="Rückgängig" title="Rückgängig (Strg+Z)" disabled={!dash.canUndo || dash.saving} onclick={() => dash.undo()} data-testid="undo"><Undo2 aria-hidden="true" /></Button>
            <Button variant="outline" size="icon" aria-label="Wiederholen" title="Wiederholen (Strg+Umschalt+Z)" disabled={!dash.canRedo || dash.saving} onclick={() => dash.redo()} data-testid="redo"><Redo2 aria-hidden="true" /></Button>
            <Button variant="outline" onclick={() => openGallery()} disabled={dash.saving} data-testid="open-gallery"><Plus aria-hidden="true" />Kachel hinzufügen</Button>
            {#if dash.canReset}<Button variant="outline" onclick={reset} disabled={dash.saving || !layout.data.customized} title={layout.data.customized ? undefined : 'Das Standardlayout ist schon aktiv.'} data-testid="reset"><RotateCcw aria-hidden="true" />Standard wiederherstellen</Button>{/if}
            <Button variant="outline" onclick={cancel} disabled={dash.saving} data-testid="cancel">Abbrechen</Button>
            <Button onclick={save} disabled={dash.saving || !dash.dirty} data-testid="save">{dash.saving ? 'Speichert …' : 'Speichern'}</Button>
          </div>
        </div>
        <p class="m-0 mt-1 px-2 text-xs text-muted-foreground">Entfernen nimmt nur die Kachel aus der Übersicht; Räume bleiben unverändert.</p>

        {#if confirm === 'cancel'}
          <div class="mt-2"><Notice tone="warn">
            Nicht gespeicherte Änderungen verwerfen?
            <span class="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="destructive" onclick={cancel} data-testid="confirm-cancel">Verwerfen</Button>
              <Button size="sm" variant="outline" onclick={() => (confirm = null)}>Weiter bearbeiten</Button>
            </span>
          </Notice></div>
        {:else if confirm === 'reset'}
          <div class="mt-2"><Notice tone="warn">
            {foreign ? 'Die gespeicherte Anordnung dieser Übersicht' : 'Deine gespeicherte Anordnung'} wird gelöscht und das Standardlayout gilt sofort. Räume bleiben unverändert.
            <span class="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="destructive" onclick={reset} data-testid="confirm-reset">Standard wiederherstellen</Button>
              <Button size="sm" variant="outline" onclick={() => (confirm = null)}>Zurück</Button>
            </span>
          </Notice></div>
        {/if}

        {#if dash.failure}
          <div class="mt-2" data-testid="dash-failure">
            <Notice tone={dash.failure.kind === 'conflict' ? 'warn' : 'error'}>
              {dash.failure.message}
              {#if dash.conflict}
                <span class="mt-1 block text-xs">Jemand hat das Layout gespeichert, seit du mit dem Anpassen begonnen hast. Dein Entwurf ist noch da.</span>
                <span class="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onclick={() => dash.reloadKeepingDraft()} data-testid="conflict-reload">Neu laden, Entwurf behalten</Button>
                  <Button size="sm" variant="outline" onclick={() => dash.discardForServer()} data-testid="conflict-discard">Entwurf verwerfen</Button>
                </span>
              {/if}
            </Notice>
          </div>
        {/if}
        {#if dash.problems.length > 1}
          <ul class="mx-2 mt-2 mb-0 list-disc pl-5 text-sm text-destructive" data-testid="problems">
            {#each dash.problems as p, i (i)}<li>{p.message}</li>{/each}
          </ul>
        {/if}
        {#if dash.info}<div class="mt-2"><Notice tone="info">{dash.info}</Notice></div>{/if}
      </div>
    {/if}

    {#if visible.length === 0}
      <div class="rounded-xl border bg-card p-6" data-testid="dash-empty">
        <p class="m-0 text-muted-foreground">
          {data.rooms.length === 0 ? 'Für dein Konto sind keine Räume sichtbar.' : 'Dein Dashboard ist leer.'}
          {#if !dash.editing && data.rooms.length > 0}Mit „Anpassen“ kannst du Kacheln hinzufügen.{/if}
        </p>
      </div>
    {/if}

    {#each visible as section, si (section.key)}
      <section
        class="dash-section"
        class:dash-dragging={drag.active?.kind === 'section' && drag.active.key === section.key}
        aria-labelledby={`dash-s-${section.key}`}
        data-section-key={section.key}
        data-drop={sectionMark(section, si)}
        data-testid="dash-section"
      >
        {#if dash.editing}
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <h2 id={`dash-s-${section.key}`} class="sr-only">{section.title || 'Abschnitt ohne Titel'}</h2>
            <button
              type="button"
              class={handleCls}
              aria-label={`Abschnitt „${section.title || 'Ohne Titel'}“ verschieben`}
              aria-describedby="dash-move-hint"
              data-handle-section={section.key}
              data-testid="section-handle"
              onpointerdown={(e) => drag.start(e, 'section', section.key)}
              onkeydown={(e) => onSectionKey(e, section)}
            ><GripVertical size={18} aria-hidden="true" /></button>
            <input
              type="text"
              class="h-10 min-w-0 flex-1 rounded-lg border bg-background px-3 text-lg font-semibold"
              maxlength={MAX_TITLE_LENGTH}
              value={section.title}
              aria-label={`Titel von Abschnitt ${si + 1}`}
              aria-invalid={dash.problems.some((p) => p.key === section.key)}
              data-title-section={section.key}
              oninput={(e) => dash.apply((s) => renameSection(s, section.key, e.currentTarget.value), `${section.key}:title`)}
            />
            <Button variant="outline" size="icon" aria-label="Abschnitt nach oben" title="Abschnitt nach oben" disabled={si === 0} onclick={() => moveSectionStep(section, -1)}><ArrowUp aria-hidden="true" /></Button>
            <Button variant="outline" size="icon" aria-label="Abschnitt nach unten" title="Abschnitt nach unten" disabled={si === dash.draft.length - 1} onclick={() => moveSectionStep(section, 1)}><ArrowDown aria-hidden="true" /></Button>
            <Button variant="outline" size="icon" aria-label={`Abschnitt „${section.title || 'Ohne Titel'}“ entfernen (Räume bleiben bestehen)`} title="Abschnitt entfernen" onclick={() => removeSectionAt(section)} data-testid="remove-section"><Trash2 aria-hidden="true" /></Button>
          </div>
        {:else}
          <h2 id={`dash-s-${section.key}`} class="mb-3">{section.title}</h2>
        {/if}

        <ul class="dash-grid">
          {#each section.tiles as tile (tile.key)}
            <li
              class="dash-tile"
              class:dash-dragging={drag.active?.kind === 'tile' && drag.active.key === tile.key}
              data-w={tile.width}
              data-tile-key={tile.key}
              data-kind={tile.kind}
              data-drop={dropMark(tile, section)}
            >
              {#if dash.editing}
                <div class="dash-edit-frame flex h-full flex-col gap-2 p-2" data-testid="edit-tile">
                  <div class="flex items-center gap-1">
                    <button
                      type="button"
                      class={handleCls}
                      aria-label={`Kachel „${label(tile)}“ verschieben`}
                      aria-describedby="dash-move-hint"
                      data-handle-tile={tile.key}
                      data-testid="tile-handle"
                      onpointerdown={(e) => drag.start(e, 'tile', tile.key)}
                      onkeydown={(e) => onTileKey(e, tile)}
                    ><GripVertical size={18} aria-hidden="true" /></button>
                    <span class="min-w-0 flex-1 truncate text-sm font-medium" title={label(tile)}>{label(tile)}</span>
                    <Button variant="outline" size="icon" aria-label={`Kachel „${label(tile)}“ anpassen`} title="Anpassen" onclick={() => openSettings(tile)} data-testid="tile-settings-open"><Settings2 aria-hidden="true" /></Button>
                    <Button variant="outline" size="icon" aria-label={`Kachel „${label(tile)}“ entfernen${tile.kind === 'room' ? ' (der Raum bleibt bestehen)' : ''}`} title="Entfernen" onclick={() => removeTileAt(tile)} data-testid="tile-remove"><Trash2 aria-hidden="true" /></Button>
                  </div>
                  <!-- Vorschau: inert, damit weder Klick noch Fokus in die Kachel navigieren. -->
                  <div class="min-h-0 flex-1" inert data-testid="tile-preview">
                    {#if (ZONE_KINDS.has(tile.kind) && (tile.zone_id === null || !zoneIds.has(tile.zone_id)))}
                      <p class="m-0 rounded-xl border bg-card p-4 text-sm text-muted-foreground">Dieser Raum ist nicht verfügbar. Beim Speichern lehnt der Server die Kachel ab; bitte entfernen oder einen anderen Raum wählen.</p>
                    {:else}
                      <TileBody {tile} {data} {zones} />
                    {/if}
                  </div>
                </div>
              {:else if readonly}
                <!-- Vorschau: inert, damit nichts bedient werden kann. -->
                <div class="h-full" inert><TileBody {tile} {data} {zones} /></div>
              {:else}
                <TileBody {tile} {data} {zones} onchanged={() => void rooms.refresh()} />
              {/if}
            </li>
          {:else}
            <li class="col-span-12 rounded-xl border border-dashed p-4 text-sm text-muted-foreground" style="grid-column: 1 / -1">Noch keine Kacheln. Ziehe eine hierher oder füge eine hinzu.</li>
          {/each}
        </ul>
        {#if dash.editing}
          <div class="mt-3"><Button variant="outline" size="sm" class="min-h-9" onclick={() => openGallery(section.key)} data-testid="section-add-tile"><Plus aria-hidden="true" />Kachel hinzufügen</Button></div>
        {/if}
      </section>
    {/each}

    {#if dash.editing}
      <Button variant="outline" onclick={newSection} disabled={!canAddSection(dash.draft)} data-testid="add-section"><Plus aria-hidden="true" />Abschnitt hinzufügen{canAddSection(dash.draft) ? '' : ` (höchstens ${MAX_SECTIONS})`}</Button>
    {/if}
  </div>

  <Gallery bind:open={galleryOpen} sections={dash.draft} {zones} admin bind:targetKey={galleryTarget} onAdd={addFromGallery} />
  <TileSettings bind:open={settingsOpen} tile={settingsTile} sections={dash.draft} {zones} {onPatch} onMove={onMoveToSection} />
{/if}
