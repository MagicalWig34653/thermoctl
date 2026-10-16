<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { ChevronDown, ChevronRight, Maximize, Minus, Plus, RotateCcw, X } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import type { MapActions } from '$lib/devices/map-actions.svelte';
  import {
    HEAD_H, MAX_ZOOM, MIN_ZOOM, NODE_W, ROW_H, anchors, autoLayout, bounds, clampZoom, curvePath, fitView,
    matchesSearch, nodeHeight, nodeRects, readableView, resolvePlacement, toWorld, visiblePorts, zoomAround,
    type Placement, type Point, type View,
  } from '$lib/devices/geometry';
  import { drawnLines, nodeDeviceId, portIndex, rejectionReason, valvesOfDevice, zoneName } from '$lib/devices/map-logic';
  import { EMPTY_PREFS, loadPrefs, savePrefs } from '$lib/devices/storage';
  import type { DeviceMap, MapPort } from '$lib/devices/types';

  let { map, actions }: { map: DeviceMap; actions: MapActions } = $props();

  // -- Darstellung (pro Browser) -----------------------------------------------------------
  let pos = $state<Placement>({});
  let collapsedIds = $state<string[]>([]);
  let technical = $state(false);
  let query = $state('');
  let view = $state<View>({ x: 0, y: 0, k: 1 });
  let userView = $state(false); // nur dann wird die Ansicht gemerkt; sonst passt „Einpassen“ sich an
  let size = $state({ w: 0, h: 0 });
  let ready = $state(false);
  let surface: HTMLDivElement | undefined = $state();
  let prefsLoaded = false;

  const collapsed = $derived(new Set(collapsedIds));
  const ports = $derived(portIndex(map));
  const shown = $derived(map.nodes.filter((n) => matchesSearch(n, query)));
  const placement = $derived(resolvePlacement(autoLayout(map.nodes, collapsed, technical), pos));
  const anchorOf = $derived(anchors(shown, placement, collapsed, technical));
  const lines = $derived(drawnLines(map, new Set(Object.keys(anchorOf)), ports));
  const box = $derived(bounds(nodeRects(shown, placement, collapsed, technical)));
  const editable = $derived(map.may_edit);

  function persist() {
    if (!prefsLoaded) return;
    savePrefs({ pos, view: userView ? view : null, collapsed: collapsedIds, technical });
  }

  let fitAll = false; // „Einpassen“ zeigt alles, auch wenn es klein wird; die Startansicht bleibt lesbar
  function refit() {
    const next = fitAll ? fitView(box, size) : readableView(box, size);
    if (next) view = next;
  }

  onMount(() => {
    const saved = loadPrefs();
    pos = saved.pos;
    collapsedIds = saved.collapsed;
    technical = saved.technical;
    if (saved.view) {
      view = saved.view;
      userView = true;
    }
    prefsLoaded = true;
    const el = surface!;
    const measure = () => {
      size = { w: el.clientWidth, h: el.clientHeight };
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018));
      userView = true;
      view = zoomAround(view, { x: e.clientX - r.left, y: e.clientY - r.top }, factor);
      persist();
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => {
      observer.disconnect();
      el.removeEventListener('wheel', wheel);
    };
  });

  // Erst mit echten Containermaßen wird eine Ansicht gesetzt (nichts winzig oben links): bis
  // dahin bleibt die Welt unsichtbar. Ohne eigene Ansicht passt sich „Einpassen“ an Größe und
  // Inhalt an.
  $effect(() => {
    const w = size.w;
    const h = size.h;
    void box;
    untrack(() => {
      if (w < 40 || h < 40) return;
      if (!userView) refit();
      ready = true;
    });
  });

  // -- Zeigerbedienung ---------------------------------------------------------------------
  type Mode =
    | { kind: 'pan'; id: number; sx: number; sy: number; start: View }
    | { kind: 'pinch'; start: View; d0: number; c0: Point }
    | { kind: 'node'; id: number; node: string; sx: number; sy: number; origin: Point; moved: boolean }
    | { kind: 'wire'; id: number; from: string; sx: number; sy: number; moved: boolean };
  let mode: Mode | null = null;
  const touches = new Map<number, Point>();
  let panning = $state(false);
  let cursor = $state<Point | null>(null); // Weltkoordinaten, für die Vorschaulinie
  let picked = $state<string | null>(null); // gewählter erster Anschluss (Klick- und Tastaturweg)
  let dragFrom = $state<string | null>(null); // Anschluss, von dem gerade gezogen wird
  let hover = $state<string | null>(null);
  let selectedLine = $state<string | null>(null);
  let confirmDisconnect = $state(false);

  const origin = $derived(picked ?? dragFrom);
  const originPort = $derived(origin ? (ports.get(origin) ?? null) : null);

  function local(e: PointerEvent): Point {
    const r = surface!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function onSurfaceDown(e: PointerEvent) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const target = e.target as Element;
    const grip = target.closest<HTMLElement>('[data-grip]');
    const port = target.closest<HTMLElement>('[data-port]');
    const interactive = target.closest('.dm-collapse, .dm-wire-hit, .dm-hud-notice');
    const p = local(e);
    // Zwei Finger bedeuten immer Zoomen, auch wenn einer auf einer Schaltfläche oder Linie liegt.
    if (e.pointerType === 'touch') {
      if (e.isPrimary) touches.clear(); // der erste Finger beginnt eine neue Geste; Altes verwerfen
      touches.set(e.pointerId, p);
      if (touches.size === 2) {
        const [a, b] = [...touches.values()];
        mode = { kind: 'pinch', start: view, d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, c0: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
        picked = null;
        dragFrom = null;
        surface!.setPointerCapture(e.pointerId);
        return;
      }
    }
    if (interactive) return;
    if (grip && !port) {
      const id = grip.dataset.grip!;
      mode = { kind: 'node', id: e.pointerId, node: id, sx: p.x, sy: p.y, origin: placement[id], moved: false };
      surface!.setPointerCapture(e.pointerId);
      return;
    }
    if (port) {
      const el = port as HTMLButtonElement;
      if (el.disabled) return;
      mode = { kind: 'wire', id: e.pointerId, from: port.dataset.port!, sx: p.x, sy: p.y, moved: false };
      surface!.setPointerCapture(e.pointerId);
      return;
    }
    selectedLine = null;
    mode = { kind: 'pan', id: e.pointerId, sx: p.x, sy: p.y, start: view };
    panning = true;
    surface!.setPointerCapture(e.pointerId);
  }

  function onSurfaceMove(e: PointerEvent) {
    const p = local(e);
    cursor = toWorld(view, p);
    if (e.pointerType === 'touch' && touches.has(e.pointerId)) touches.set(e.pointerId, p);
    if (!mode) return;
    if (mode.kind === 'pinch') {
      if (touches.size < 2) return;
      const [a, b] = [...touches.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const c = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const zoomed = zoomAround(mode.start, mode.c0, d / mode.d0);
      userView = true;
      view = { k: zoomed.k, x: zoomed.x + (c.x - mode.c0.x), y: zoomed.y + (c.y - mode.c0.y) };
      return;
    }
    if (e.pointerId !== mode.id) return;
    const dx = p.x - mode.sx;
    const dy = p.y - mode.sy;
    if (mode.kind === 'pan') {
      userView = true;
      view = { ...mode.start, x: mode.start.x + dx, y: mode.start.y + dy };
    } else if (mode.kind === 'node') {
      if (!mode.moved && Math.hypot(dx, dy) < 4) return;
      mode.moved = true;
      userView = true;
      pos = { ...pos, [mode.node]: { x: mode.origin.x + dx / view.k, y: mode.origin.y + dy / view.k } };
    } else if (mode.kind === 'wire') {
      if (!mode.moved && Math.hypot(dx, dy) < 6) return;
      if (!mode.moved) {
        mode.moved = true;
        picked = null;
        dragFrom = mode.from;
        actions.notice = null;
      }
      const under = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-port]');
      hover = under?.dataset.port ?? null;
    }
  }

  async function onSurfaceUp(e: PointerEvent) {
    touches.delete(e.pointerId);
    const current = mode;
    if (!current) return;
    if (current.kind === 'pinch') {
      if (touches.size < 2) {
        mode = null;
        persist();
      }
      return;
    }
    if (e.pointerId !== current.id) return;
    mode = null;
    panning = false;
    if (current.kind === 'pan' || current.kind === 'node') {
      persist();
      return;
    }
    // Anschluss: gezogen oder angetippt.
    const from = current.from;
    dragFrom = null;
    hover = null;
    if (current.moved) {
      const target = (document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-port]'))?.dataset.port;
      if (target && target !== from) await attempt(from, target);
      return;
    }
    activate(from);
  }

  function onCancel(e: PointerEvent) {
    touches.delete(e.pointerId);
    if (mode?.kind === 'pinch' && touches.size < 2) mode = null;
    mode = null;
    panning = false;
    dragFrom = null;
    hover = null;
  }

  /** Anschluss angetippt oder mit der Tastatur bestätigt: ersten wählen, zweiten verbinden. */
  function activate(id: string) {
    if (!editable) return;
    actions.notice = null;
    if (picked === null) {
      picked = id;
      return;
    }
    if (picked === id) {
      picked = null;
      return;
    }
    const first = picked;
    void attempt(first, id);
  }

  async function attempt(a: string, b: string) {
    picked = null;
    await actions.connect(a, b);
  }

  function onPortKey(e: KeyboardEvent, id: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      activate(id);
    }
  }

  function cancelAll() {
    picked = null;
    dragFrom = null;
    mode = null;
    panning = false;
    selectedLine = null;
    confirmDisconnect = false;
    hover = null;
  }

  function onWindowKey(e: KeyboardEvent) {
    if (e.key === 'Escape') cancelAll();
  }

  // -- Knoten ------------------------------------------------------------------------------
  function toggle(id: string) {
    collapsedIds = collapsed.has(id) ? collapsedIds.filter((c) => c !== id) : [...collapsedIds, id];
    persist();
  }

  function nudge(e: KeyboardEvent, id: string) {
    const step = e.shiftKey ? 64 : 16;
    const d: Record<string, Point> = {
      ArrowLeft: { x: -step, y: 0 }, ArrowRight: { x: step, y: 0 }, ArrowUp: { x: 0, y: -step }, ArrowDown: { x: 0, y: step },
    };
    const move = d[e.key];
    if (!move) return;
    e.preventDefault();
    const at = placement[id];
    userView = true;
    pos = { ...pos, [id]: { x: at.x + move.x, y: at.y + move.y } };
    persist();
  }

  // -- Ansicht -----------------------------------------------------------------------------
  function zoomBy(factor: number) {
    userView = true;
    view = zoomAround(view, { x: size.w / 2, y: size.h / 2 }, factor);
    persist();
  }
  function fitNow() {
    userView = false;
    fitAll = true;
    refit();
    persist();
  }
  /** Nur Darstellung: gemerkte Positionen verwerfen, Anordnung neu, einpassen. Kein Serveraufruf. */
  function arrange() {
    pos = {};
    userView = false;
    fitAll = false;
    refit();
    persist();
  }
  function setTechnical(v: boolean) {
    technical = v;
    persist();
  }

  // -- Anzeige -----------------------------------------------------------------------------
  const KIND_CLASSES = new Set(['temperature', 'humidity', 'contact', 'presence', 'command']);
  const kindClass = (kind: string | null) => `dm-kind-${kind && KIND_CLASSES.has(kind) ? kind : 'unknown'}`;
  const kindOf = (id: string) => ports.get(id)?.kind ?? null;

  function portState(port: MapPort): 'active' | 'fit' | 'unfit' | 'idle' {
    if (!originPort) return 'idle';
    if (port.id === originPort.id) return 'active';
    return rejectionReason(map, originPort, port) ? 'unfit' : 'fit';
  }
  function portTitle(port: MapPort): string {
    if (originPort && port.id !== originPort.id) {
      const reason = rejectionReason(map, originPort, port);
      if (reason) return `Passt nicht: ${reason}`;
    }
    return port.blocked_reason ? `${port.tooltip} ${port.blocked_reason}`.trim() : port.tooltip;
  }
  const hoverReason = $derived.by(() => {
    if (!originPort || !hover || hover === originPort.id) return null;
    const target = ports.get(hover);
    return target ? rejectionReason(map, originPort, target) : null;
  });

  const draftPath = $derived.by(() => {
    if (!originPort || !cursor) return null;
    const a = anchorOf[originPort.id];
    if (!a) return null;
    const hit = hover ? anchorOf[hover] : undefined;
    const end = hit ?? cursor;
    return originPort.direction === 'out' ? curvePath(a, end) : curvePath(end, a);
  });

  const selected = $derived(map.connections.find((c) => c.id === selectedLine) ?? null);
  const kindLegend = $derived(Object.entries(map.kind_labels));
  const valveList = $derived(
    map.valve_assignments.map((v) => ({
      ...v,
      device: map.nodes.find((n) => nodeDeviceId(n) === v.device_id)?.name ?? `Gerät ${v.device_id}`,
      zone: zoneName(map, v.zone_id),
      description: map.valve_modes.find((m) => m.code === v.mode)?.description ?? '',
    })),
  );

  async function removeSelected() {
    if (!selected) return;
    if (!confirmDisconnect) {
      confirmDisconnect = true;
      return;
    }
    const id = selected.id;
    confirmDisconnect = false;
    selectedLine = null;
    await actions.disconnect(id);
  }
</script>

<svelte:window onkeydown={onWindowKey} />

<div class="grid grid-cols-[minmax(0,1fr)] gap-3" data-testid="device-map">
  <div class="dm-toolbar">
    <label class="sr-only" for="dm-search">Gerätekarte durchsuchen</label>
    <input
      id="dm-search"
      type="search"
      class="h-10 min-w-0 flex-1 rounded-lg border bg-background px-3 sm:max-w-xs"
      placeholder="Gerät oder Raum suchen …"
      bind:value={query}
    />
    <div class="flex flex-wrap items-center gap-1" role="group" aria-label="Ansicht">
      <Button variant="outline" size="icon" aria-label="Karte verkleinern" disabled={view.k <= MIN_ZOOM} onclick={() => zoomBy(1 / 1.25)}><Minus size={16} aria-hidden="true" /></Button>
      <span class="w-12 text-center text-sm tabular-nums" aria-live="polite" data-testid="zoom-label">{Math.round(view.k * 100)} %</span>
      <Button variant="outline" size="icon" aria-label="Karte vergrößern" disabled={view.k >= MAX_ZOOM} onclick={() => zoomBy(1.25)}><Plus size={16} aria-hidden="true" /></Button>
      <Button variant="outline" class="max-sm:size-10 max-sm:px-0" aria-label="Einpassen" title="Alles einpassen" onclick={fitNow}><Maximize size={16} aria-hidden="true" /><span class="max-sm:sr-only">Einpassen</span></Button>
      <Button variant="outline" class="max-sm:size-10 max-sm:px-0" aria-label="Automatisch anordnen" title="Ordnet die Karten neu an. Das betrifft nur die Darstellung in diesem Browser." onclick={arrange}><RotateCcw size={16} aria-hidden="true" /><span class="max-sm:sr-only">Automatisch anordnen</span></Button>
    </div>
    <label class="flex min-h-10 items-center gap-2 text-sm">
      <input type="checkbox" class="size-4" checked={technical} onchange={(e) => setTechnical(e.currentTarget.checked)} />
      technische Werte zeigen
    </label>
  </div>

  <div class="dm-legend max-sm:text-xs" aria-label="Legende">
    {#each kindLegend as [code, label] (code)}
      <span class={kindClass(code)}><i></i>{label}</span>
    {/each}
    <span>Ausgang rechts (voller Punkt) → Eingang links (hohler Punkt)</span>
  </div>

  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    bind:this={surface}
    class="dm-surface"
    role="group"
    aria-label="Gerätekarte"
    data-testid="map-surface"
    data-panning={panning}
    data-ready={ready}
    onpointerdown={onSurfaceDown}
    onpointermove={onSurfaceMove}
    onpointerup={onSurfaceUp}
    onpointercancel={onCancel}
  >
    <div class="dm-world" data-ready={ready} data-testid="map-world" style:transform={`translate(${view.x}px, ${view.y}px) scale(${view.k})`}>
      <svg class="dm-wires" aria-hidden="false" role="group" aria-label="Verbindungen">
        {#each lines as line (line.id)}
          {@const a = anchorOf[line.out]}
          {@const b = anchorOf[line.in]}
          {#if a && b}
            <g class={kindClass(line.kind)}>
              <path class="dm-wire" data-testid="wire" data-line={line.id} data-out={line.out} data-in={line.in} data-selected={selectedLine === line.id} d={curvePath(a, b)} />
              <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
              <path
                class="dm-wire-hit"
                role="button"
                tabindex="0"
                aria-label={`Verbindung ${line.title}`}
                aria-pressed={selectedLine === line.id}
                d={curvePath(a, b)}
                onclick={() => { selectedLine = line.id; confirmDisconnect = false; }}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectedLine = line.id; confirmDisconnect = false; } }}
              />
            </g>
          {/if}
        {/each}
        {#if draftPath && originPort}
          <g class={kindClass(originPort.kind)}><path class="dm-wire" data-draft="true" data-testid="draft-wire" d={draftPath} /></g>
        {/if}
      </svg>

      {#each shown as node (node.id)}
        {@const at = placement[node.id]}
        {@const list = visiblePorts(node, technical)}
        {@const shut = collapsed.has(node.id)}
        {@const valves = node.kind === 'device' ? valvesOfDevice(map, nodeDeviceId(node)) : []}
        {#if at}
          <section
            class="dm-node"
            data-testid="map-node"
            data-node={node.id}
            data-kind={node.kind}
            style:left={`${at.x}px`}
            style:top={`${at.y}px`}
            style:height={`${nodeHeight(list.length, shut)}px`}
            aria-label={node.name}
          >
            <div class="dm-node-body"></div>
            <div class="dm-head">
              <button type="button" class="dm-grip" data-grip={node.id} aria-label={`${node.name} verschieben (Pfeiltasten)`} onkeydown={(e) => nudge(e, node.id)}>
                <span class="dm-title" title={node.name}>{node.name}</span>
                <span class="dm-sub">
                  {node.kind === 'zone' ? 'Raum' : 'Gerät'}{#each valves as v (v.assignment_id)} · {v.mode_label}{/each}
                </span>
              </button>
              <button type="button" class="dm-collapse" aria-expanded={!shut} aria-label={`${node.name} ${shut ? 'ausklappen' : 'einklappen'}`} onclick={() => toggle(node.id)}>
                {#if shut}<ChevronRight size={16} aria-hidden="true" />{:else}<ChevronDown size={16} aria-hidden="true" />{/if}
              </button>
            </div>
            {#if !shut}
              {#each list as port, i (port.id)}
                {@const state = portState(port)}
                <div class="dm-row" data-dir={port.direction} data-technical={port.technical} style:top={`${HEAD_H + i * ROW_H}px`}>
                  <span class="dm-label" title={port.tooltip || port.label}>{port.label}</span>
                  <button
                    type="button"
                    class="dm-port {kindClass(port.kind)}"
                    data-port={port.id}
                    data-testid="port"
                    data-dir={port.direction}
                    data-technical={port.technical}
                    data-state={state}
                    disabled={!editable || port.technical}
                    aria-label={`${port.direction === 'out' ? 'Ausgang' : 'Eingang'} ${port.label} von ${node.name}${port.technical ? ' (technisch)' : ''}${state === 'unfit' ? ' – passt nicht' : ''}`}
                    aria-pressed={picked === port.id}
                    title={portTitle(port)}
                    onkeydown={(e) => onPortKey(e, port.id)}
                    onpointerenter={() => (hover = port.id)}
                    onpointerleave={() => (hover = null)}
                    onfocus={() => (hover = port.id)}
                    onblur={() => (hover = null)}
                  ><span class="dm-dot"></span></button>
                </div>
              {/each}
            {/if}
          </section>
        {/if}
      {/each}
    </div>
    <div class="dm-hud">
      {#if actions.notice}
        <div class="dm-hud-notice" data-testid="map-notice">
          <div class="min-w-0 flex-1"><Notice tone={actions.notice.tone}>{actions.notice.text}</Notice></div>
          <button type="button" class="dm-hud-close" aria-label="Meldung schließen" onclick={() => (actions.notice = null)}><X size={16} aria-hidden="true" /></button>
        </div>
      {/if}
      <div class="dm-hud-status" role="status" aria-live="polite" data-testid="map-status">
        {#if originPort}
          <strong>{originPort.label}</strong> gewählt.
          {#if hoverReason}<span class="text-destructive">Passt nicht: {hoverReason}</span>{:else}Zweiten Anschluss wählen oder ziehen. Esc bricht ab.{/if}
        {:else if !editable}
          Nur Ansicht: Für das Verbinden fehlt die Berechtigung.
        {:else}
          Zum Verbinden einen Anschluss ziehen oder nacheinander zwei Anschlüsse wählen.
        {/if}
      </div>
    </div>
    {#if !ready}
      <div class="dm-empty" role="status">Karte wird vorbereitet …</div>
    {:else if !shown.length}
      <div class="dm-empty">{map.nodes.length ? 'Keine Geräte oder Räume zur Suche gefunden.' : 'Es gibt noch keine Geräte oder Räume.'}</div>
    {/if}
  </div>

  {#if selected}
    <div class="grid gap-2 rounded-xl border bg-card p-3 sm:flex sm:items-center sm:justify-between" data-testid="line-panel">
      <div class="min-w-0">
        <p class="m-0 font-medium">{selected.title}</p>
        <p class="m-0 text-sm text-muted-foreground">
          {selected.editable ? 'Beim Lösen kann sich die Betriebsart eines Ventils ändern; der Server meldet das Ergebnis.' : 'Diese Verbindung kannst du nicht ändern.'}
        </p>
      </div>
      <div class="flex gap-2">
        {#if selected.editable}
          <Button variant="destructive" disabled={actions.busy} onclick={removeSelected}>{confirmDisconnect ? 'Wirklich trennen' : 'Trennen'}</Button>
        {/if}
        <Button variant="ghost" onclick={cancelAll}>Schließen</Button>
      </div>
    </div>
  {/if}

  {#if valveList.length}
    <section aria-labelledby="valves-title" class="rounded-xl border bg-card p-3">
      <h3 id="valves-title" class="m-0 text-base">Thermostatventile und ihre Betriebsart</h3>
      <p class="mb-2 text-sm text-muted-foreground">Die Betriebsart folgt aus den Linien zwischen Raum und Ventil; der Server bestimmt sie.</p>
      <ul class="m-0 grid list-none gap-1 p-0 text-sm">
        {#each valveList as v (v.assignment_id)}
          <li data-testid="valve-mode"><strong>{v.device}</strong> in {v.zone}: <span class="font-medium">{v.mode_label}</span>{#if v.description}{' – ' + v.description}{/if}</li>
        {/each}
      </ul>
    </section>
  {/if}
</div>
