<script lang="ts">
  // Die gemeinsame Verlaufskomponente (Auswertung, später Übersicht und Raumdetail).
  // Alles kommt vom Server (`/ui-api/history`): Rohfächer mit ausdrücklichen Lücken. Hier wird
  // nur gezeichnet -- nichts interpoliert, geglättet oder erfunden. Reine Logik steht in
  // `$lib/history/*` und ist dort getestet.
  import { onMount, untrack } from 'svelte';
  import { Popover } from 'bits-ui';
  import {
    ChartNoAxesCombined,
    Download,
    Maximize2,
    Minimize2,
    SlidersHorizontal,
    Table2,
    Undo2,
    ZoomOut,
  } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Switch } from '$lib/components/ui/switch';
  import HelpTip from '$lib/components/rooms/HelpTip.svelte';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createPoller } from '$lib/rooms/poller';
  import { toFailure, type Failure } from '$lib/rooms/logic';
  import { formatCelsius } from '$lib/rooms/decimal';
  import type { History } from '$lib/history/types';
  import {
    buildPoints,
    clipSpans,
    DEFAULT_FLAGS,
    gapSpans,
    lastIndexInView,
    moveIndex,
    nearestIndex,
    runs,
    SERIES,
    temperatureValues,
    viewRange,
    type Point,
    type SeriesFlags,
  } from '$lib/history/series';
  import { formatMoment, temperatureScale, temperatureTickLabel, timeTicks } from '$lib/history/scales';
  import { tooltipPlacement } from '$lib/history/tooltip';
  import {
    back,
    cancelDrag,
    currentView,
    dragSelection,
    endDrag,
    initialZoom,
    moveDrag,
    reset,
    setKey,
    startDrag,
    type PointerInfo,
    type ZoomState,
  } from '$lib/history/zoom';
  import {
    describeBucket,
    formatHeat,
    formatRange,
    formatResolution,
    formatSetback,
    PERIODS,
    periodLabel,
    periodMs,
    type PeriodId,
  } from '$lib/history/format';
  import { buildQuery } from '$lib/history/reports';
  import { historyLimiter } from '$lib/history/shared';

  let {
    zones,
    zoneId = $bindable(),
    period = $bindable('24h'),
    filters = true,
    compact = false,
    lazy = false,
    heading = 'h2',
    title = 'Temperaturverlauf',
    onLoaded,
  }: {
    zones: { id: number; name: string }[];
    zoneId: number;
    period?: PeriodId;
    /** Raum- und Zeitraumwahl im Kartenkopf zeigen (aus: der Aufrufer steuert beides). */
    filters?: boolean;
    /** Flache Variante für Kacheln: niedriger, ohne Legende und Zusatzwerte. */
    compact?: boolean;
    /** Erst laden, wenn die Karte sichtbar wird (viele Karten auf einer Seite). */
    lazy?: boolean;
    heading?: 'h2' | 'h3';
    title?: string;
    onLoaded?: (ms: number) => void;
  } = $props();

  const { api, prefix } = useApp();
  const uid = $props.id();

  // -- Größe und Eingabeart -----------------------------------------------------------------
  let host: HTMLElement;
  let measure: HTMLElement;
  let surface = $state<HTMLDivElement | null>(null);
  let width = $state(720);
  let finePointer = $state(true);
  let enabled = $state(!untrack(() => lazy));
  let viewportHeight = $state(800);

  onMount(() => {
    const observer = new ResizeObserver(([entry]) => {
      width = Math.max(260, Math.round(entry.contentRect.width));
    });
    observer.observe(measure);
    const media = window.matchMedia('(pointer: fine)');
    finePointer = media.matches;
    const onMedia = () => (finePointer = media.matches);
    media.addEventListener('change', onMedia);
    viewportHeight = window.innerHeight;

    let seen: IntersectionObserver | null = null;
    if (lazy && !enabled) {
      seen = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) {
            enabled = true;
            seen?.disconnect();
          }
        },
        { rootMargin: '300px' },
      );
      seen.observe(host);
    }
    return () => {
      observer.disconnect();
      media.removeEventListener('change', onMedia);
      seen?.disconnect();
    };
  });

  // -- Zeitraum, Zoom, Laden -----------------------------------------------------------------
  const minute = (t: number) => Math.floor(t / 60_000) * 60_000;
  let anchor = $state(minute(Date.now()));
  const base = $derived({ from: anchor - periodMs(period), to: anchor });
  const zoomKey = $derived(`${zoneId}|${period}`);
  let zoom = $state<ZoomState>(initialZoom());
  $effect.pre(() => {
    const next = setKey(untrack(() => zoom), zoomKey);
    if (next !== untrack(() => zoom)) zoom = next;
  });
  const view = $derived(currentView(zoom, base));
  const zoomed = $derived(zoom.stack.length > 0);

  let history = $state<History | null>(null);
  let failure = $state<Failure | null>(null);
  let loading = $state(false);
  let generation = 0;
  let firstDone = false;
  const t0 = performance.now();

  async function load(range: { from: number; to: number }, zone: number) {
    const mine = ++generation;
    loading = true;
    try {
      const query = buildQuery({
        zone_id: zone,
        from: new Date(range.from).toISOString(),
        to: new Date(range.to).toISOString(),
      });
      const data = await historyLimiter.run(() => api.get<History>(`/ui-api/history${query}`));
      if (mine !== generation) return;
      history = data;
      failure = null;
      selected = null;
      if (!firstDone) {
        firstDone = true;
        onLoaded?.(Math.round(performance.now() - t0));
      }
    } catch (error) {
      if (mine !== generation) return;
      failure = toFailure(error);
    } finally {
      if (mine === generation) loading = false;
    }
  }

  // Raum, Zeitraum oder Zoom geändert (oder der Takt hat das Ende verschoben): neu lesen.
  $effect(() => {
    if (!enabled) return;
    const range = { from: view.from, to: view.to };
    const zone = zoneId;
    untrack(() => void load(range, zone));
  });

  // Takt: nur der ungezoomte Zeitraum wandert mit; ein gewählter Ausschnitt bleibt stehen.
  const poller = createPoller({
    run: async () => {
      if (!zoomed) anchor = minute(Date.now());
    },
    intervalMs: 60_000,
    isHidden: () => document.hidden,
  });
  onMount(() => {
    poller.start();
    const onVisibility = () => poller.visibilityChanged();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      poller.stop();
      document.removeEventListener('visibilitychange', onVisibility);
      generation += 1;
    };
  });

  const timezone = $derived(history?.timezone ?? 'UTC');
  const zoneName = $derived(history?.zone_id === zoneId ? history.zone_name : (zones.find((z) => z.id === zoneId)?.name ?? 'Raum'));
  const points = $derived<Point[]>(history ? buildPoints(history) : []);
  const gaps = $derived(history ? gapSpans(history.gaps) : []);
  const visibleGaps = $derived(clipSpans(gaps, { start: view.from, end: view.to }));
  const hasMeasurements = $derived(points.some((p) => p.temp !== null));
  const minSpanMs = $derived(Math.max(5, 5 * ((history?.limits.min_resolution_seconds ?? 60) / 60)) * 60_000);

  // -- Reihen ------------------------------------------------------------------------------
  let flags = $state<SeriesFlags>({ ...DEFAULT_FLAGS });

  // -- Geometrie ---------------------------------------------------------------------------
  let expanded = $state(false);
  const narrow = $derived(width < 520);
  const M = $derived({ l: narrow ? 52 : 60, r: 14, t: 12, b: 28 });
  const height = $derived(expanded ? Math.max(320, viewportHeight - 330) : compact ? 190 : narrow ? 260 : 320);
  const plotW = $derived(Math.max(10, width - M.l - M.r));
  const plotH = $derived(Math.max(10, height - M.t - M.b));
  const span = $derived(Math.max(1, view.to - view.from));
  const xOf = (t: number) => M.l + ((t - view.from) / span) * plotW;
  const tOf = (x: number) => view.from + Math.max(0, Math.min(1, (x - M.l) / plotW)) * span;

  const scale = $derived(temperatureScale(temperatureValues(points, { start: view.from, end: view.to }, flags), compact ? 3 : 5));
  const yOf = (v: number) => M.t + (1 - (v - scale.low) / (scale.high - scale.low)) * plotH;
  const xTicks = $derived(timeTicks(view.from, view.to, timezone, narrow ? 4 : compact ? 5 : 7));
  const heatH = $derived(plotH * 0.16);

  const tempRuns = $derived(runs(points, (p) => p.temp !== null, gaps));
  const effRuns = $derived(runs(points, (p) => p.effMax !== null && p.effMin !== null, []));
  const schedRuns = $derived(runs(points, (p) => p.schedMax !== null && p.schedMin !== null, []));

  const r1 = (n: number) => Math.round(n * 10) / 10;

  function linePath(idx: number[]): string {
    return idx.map((i, k) => `${k ? 'L' : 'M'}${r1(xOf(points[i].t))},${r1(yOf(points[i].temp as number))}`).join('');
  }
  function areaPath(idx: number[]): string {
    if (idx.length < 2) return '';
    const bottom = M.t + plotH;
    return `${linePath(idx)}L${r1(xOf(points[idx[idx.length - 1]].t))},${bottom}L${r1(xOf(points[idx[0]].t))},${bottom}Z`;
  }
  function bandPath(idx: number[]): string {
    if (idx.length < 2) return '';
    const upper = idx.map((i, k) => `${k ? 'L' : 'M'}${r1(xOf(points[i].t))},${r1(yOf(points[i].tempMax as number))}`).join('');
    const lower = [...idx]
      .reverse()
      .map((i) => `L${r1(xOf(points[i].t))},${r1(yOf(points[i].tempMin as number))}`)
      .join('');
    return `${upper}${lower}Z`;
  }
  /** Sollwerte sind Stufen: je Fach waagerecht von Anfang bis Ende, dazwischen senkrecht. */
  function stepPath(idx: number[], key: 'effMin' | 'effMax' | 'schedMin' | 'schedMax'): string {
    return idx
      .map((i, k) => {
        const p = points[i];
        const y = r1(yOf(p[key] as number));
        return `${k ? `V${y}` : `M${r1(xOf(p.start))},${y}`}H${r1(xOf(p.end))}`;
      })
      .join('');
  }
  const stepDiffers = (idx: number[], lo: 'effMin' | 'schedMin', hi: 'effMax' | 'schedMax') =>
    idx.some((i) => points[i][lo] !== points[i][hi]);

  // Fächer ohne Zeile, die keine gemeldete Lücke abdeckt (kürzer als die Lückenschwelle).
  const emptyBuckets = $derived(
    points.filter((p) => p.rows === 0 && !gaps.some((g) => g.start <= p.t && g.end >= p.t)),
  );
  const rect = (a: number, b: number) => {
    const x1 = Math.max(M.l, xOf(a));
    const x2 = Math.min(M.l + plotW, xOf(b));
    return { x: r1(x1), w: Math.max(0, r1(x2 - x1)) };
  };

  // -- Auswahl, Readout, Tooltip ----------------------------------------------------------
  let selected = $state<number | null>(null);
  let keyboard = $state(false);
  const lastIdx = $derived(lastIndexInView(points, { start: view.from, end: view.to }));
  const readIdx = $derived(selected !== null && points[selected] ? selected : lastIdx);
  const reading = $derived(readIdx >= 0 ? points[readIdx] : null);
  const picked = $derived(selected !== null && points[selected] ? points[selected] : null);

  const TIP_W = $derived(narrow ? 184 : 214);
  const tip = $derived(
    picked ? tooltipPlacement(xOf(picked.t), TIP_W, { left: 0, right: width }) : null,
  );
  const whenOf = (p: Point) => formatMoment(p.t, timezone);

  function selectAt(x: number) {
    const idx = nearestIndex(points, tOf(x));
    selected = idx >= 0 ? idx : null;
  }

  // -- Zeiger ------------------------------------------------------------------------------
  const localX = (e: MouseEvent) => e.clientX - (surface?.getBoundingClientRect().left ?? 0);
  const info = (e: PointerEvent): PointerInfo => ({
    pointerId: e.pointerId,
    pointerType: e.pointerType,
    isPrimary: e.isPrimary,
    button: e.button,
    finePointer,
  });

  function onDown(e: PointerEvent) {
    keyboard = false;
    const x = localX(e);
    if (x < M.l - 4 || x > M.l + plotW + 4) return;
    zoom = startDrag(zoom, info(e), x, tOf(x));
    if (zoom.drag) {
      try {
        surface?.setPointerCapture(e.pointerId);
      } catch {
        /* Zeiger schon weg */
      }
    } else if (e.pointerType !== 'mouse') {
      selectAt(x); // Touch/Stift: Tippen wählt einen Punkt, zoomt aber nie.
    }
  }
  function onMove(e: PointerEvent) {
    const x = localX(e);
    if (zoom.drag) zoom = moveDrag(zoom, info(e), x, tOf(x));
    if (x >= M.l - 4 && x <= M.l + plotW + 4) selectAt(x);
  }
  function onUp(e: PointerEvent) {
    if (!zoom.drag) return;
    const before = zoom.stack.length;
    zoom = endDrag(zoom, info(e), view, minSpanMs);
    if (surface?.hasPointerCapture?.(e.pointerId)) surface.releasePointerCapture(e.pointerId);
    if (zoom.stack.length !== before) selected = null;
  }
  function onLeave(e: PointerEvent) {
    if (e.pointerType === 'mouse' && !zoom.drag && !keyboard) selected = null;
  }
  function onCancel() {
    zoom = cancelDrag(zoom);
  }
  function onDouble(e: MouseEvent) {
    if (e.button === 0 && finePointer) {
      zoom = reset(zoom);
      selected = null;
    }
  }

  function onKey(e: KeyboardEvent) {
    const range = { start: view.from, end: view.to };
    const map: Record<string, Parameters<typeof moveIndex>[3]> = {
      ArrowLeft: e.shiftKey ? 'far-left' : 'left',
      ArrowRight: e.shiftKey ? 'far-right' : 'right',
      Home: 'first',
      End: 'last',
    };
    const action = map[e.key];
    if (action) {
      e.preventDefault();
      keyboard = true;
      const next = moveIndex(points, range, selected ?? lastIdx, action);
      selected = next >= 0 ? next : null;
    } else if (e.key === 'Escape') {
      if (zoom.drag) {
        e.preventDefault();
        zoom = cancelDrag(zoom);
      } else if (selected !== null) {
        e.preventDefault();
        selected = null;
      }
    }
  }

  function onWindowKey(e: KeyboardEvent) {
    if (e.key !== 'Escape') return;
    if (zoom.drag) {
      zoom = cancelDrag(zoom);
    } else if (expanded) {
      expanded = false;
    }
  }

  // Vollbild: Seite dahinter nicht scrollen, Fokus auf die Schaltfläche.
  let fullButton = $state<HTMLButtonElement | null>(null);
  $effect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const size = () => (viewportHeight = window.innerHeight);
    size();
    window.addEventListener('resize', size);
    fullButton?.focus();
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener('resize', size);
    };
  });

  // -- Tabelle, CSV -------------------------------------------------------------------------
  let showTable = $state(false);
  const rows = $derived.by(() => {
    const r = viewRange(points, { start: view.from, end: view.to });
    return r ? points.slice(r[0], r[1] + 1) : [];
  });
  const csvHref = $derived(
    `${prefix}/ui-api/history.csv${buildQuery({
      zone_id: zoneId,
      from: new Date(view.from).toISOString(),
      to: new Date(view.to).toISOString(),
    })}`,
  );

  const selection = $derived(dragSelection(zoom, view));
  const rangeText = $derived(`${formatMoment(view.from, timezone)} bis ${formatMoment(view.to, timezone)}`);
  const hint = $derived(
    finePointer
      ? 'Zum Zoomen mit gedrückter Maustaste einen Bereich aufziehen (auch rückwärts), Doppelklick zeigt den Gesamtzeitraum, Esc bricht ab. Mit Pfeiltasten, Pos1 und Ende einzelne Punkte wählen.'
      : 'Tippen oder über das Diagramm wischen wählt einen Punkt, die Seite lässt sich dabei normal scrollen. Zum Eingrenzen den Zeitraum oben wählen.',
  );
  const announce = $derived(selected !== null && keyboard && points[selected] ? describeBucket(points[selected].raw, whenOf(points[selected])) : '');
</script>

<svelte:window onkeydown={onWindowKey} />

<section
  bind:this={host}
  class="rounded-xl border bg-card {expanded ? 'hist-full' : ''} {compact ? 'p-3' : 'p-4 sm:p-5'}"
  aria-labelledby="{uid}-title"
  aria-busy={loading}
  role={expanded ? 'dialog' : undefined}
  aria-modal={expanded ? 'true' : undefined}
>
  <!-- Kartenkopf -->
  <div class="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
    <div class="flex min-w-0 items-start gap-3">
      <span class="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
        <ChartNoAxesCombined size={18} aria-hidden="true" />
      </span>
      <div class="min-w-0">
        <svelte:element this={heading} id="{uid}-title" class="m-0 text-lg">{title}: {zoneName}</svelte:element>
        <p class="m-0 text-sm text-muted-foreground">
          {zoomed ? 'Eigener Ausschnitt' : periodLabel(period)}{history ? ` · ${rangeText}` : ''}
        </p>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-1.5">
      <Popover.Root>
        <Popover.Trigger
          class="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-2.5 text-sm hover:bg-muted"
          aria-label="Datenreihen einstellen"
        >
          <SlidersHorizontal size={14} aria-hidden="true" /><span>Datenreihen</span>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            sideOffset={8}
            collisionPadding={12}
            class="z-50 w-80 max-w-[calc(100vw-24px)] rounded-xl border bg-popover p-4 text-sm text-popover-foreground shadow-lg"
          >
            <h3 class="mb-1 text-sm">Diagramm anpassen</h3>
            <p class="mb-3 text-muted-foreground">Wähle die sichtbaren Datenreihen.</p>
            <ul class="m-0 flex list-none flex-col gap-3 p-0">
              {#each SERIES as s (s.key)}
                <li class="flex items-center justify-between gap-3">
                  <div class="min-w-0">
                    <strong class="block text-sm font-medium">{s.label}</strong>
                    <small class="text-muted-foreground">{s.description}</small>
                  </div>
                  <Switch
                    aria-label="{s.label} anzeigen"
                    checked={flags[s.key]}
                    onCheckedChange={(v) => (flags[s.key] = v)}
                  />
                </li>
              {/each}
            </ul>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <Button variant="outline" size="sm" aria-pressed={showTable} onclick={() => (showTable = !showTable)}>
        <Table2 size={14} aria-hidden="true" />Datentabelle
      </Button>
      <a
        class="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-2.5 text-sm hover:bg-muted"
        href={csvHref}
        download
      >
        <Download size={14} aria-hidden="true" />CSV
      </a>
      <Button
        bind:ref={fullButton}
        variant="outline"
        size="sm"
        onclick={() => (expanded = !expanded)}
        aria-label={expanded ? 'Vollbild schließen' : 'Vollbild'}
      >
        {#if expanded}<Minimize2 size={14} aria-hidden="true" />{:else}<Maximize2 size={14} aria-hidden="true" />{/if}
        <span class="hidden sm:inline">{expanded ? 'Schließen' : 'Vollbild'}</span>
      </Button>
    </div>
  </div>

  {#if filters}
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <label class="flex items-center gap-2 text-sm">
        <span class="text-muted-foreground">Raum</span>
        <select
          class="h-9 max-w-[14rem] rounded-lg border bg-background px-2 text-sm"
          value={zoneId}
          onchange={(e) => (zoneId = Number((e.currentTarget as HTMLSelectElement).value))}
        >
          {#each zones as z (z.id)}<option value={z.id}>{z.name}</option>{/each}
        </select>
      </label>
      <div class="flex gap-1" role="group" aria-label="Zeitraum">
        {#each PERIODS as p (p.id)}
          <Button
            variant={period === p.id ? 'secondary' : 'outline'}
            size="sm"
            class="min-h-9"
            aria-pressed={period === p.id}
            onclick={() => (period = p.id)}>{p.label}</Button
          >
        {/each}
      </div>
    </div>
  {/if}

<div bind:this={measure}>
  {#if failure && !history}
    <div class="space-y-3">
      <Notice tone="error">{failure.message}</Notice>
      <Button variant="outline" size="sm" onclick={() => load(view, zoneId)}>Erneut versuchen</Button>
    </div>
  {:else if !history}
    <p class="m-0 py-10 text-center text-muted-foreground" role="status">
      {enabled ? 'Verlauf wird geladen …' : 'Verlauf wird geladen, sobald die Karte sichtbar ist.'}
    </p>
  {:else}
    {#if failure}
      <div class="mb-3">
        <Notice tone="warn">
          Aktualisieren fehlgeschlagen: {failure.message} Angezeigt ist der zuletzt geladene Stand.
        </Notice>
      </div>
    {/if}

    <!-- Große aktuelle Messung und die Werte des gewählten bzw. letzten Punkts -->
    <div class="mb-3 flex flex-wrap items-end gap-x-8 gap-y-2" data-testid="readout">
      <div>
        <div class="text-sm text-muted-foreground">
          {#if reading}
            {picked ? 'Gewählter Punkt' : 'Letzter Wert im Ausschnitt'} · {whenOf(reading)}
          {:else}
            Keine Werte im Ausschnitt
          {/if}
        </div>
        <div class="flex items-baseline gap-2">
          <span class="text-4xl leading-none font-semibold tabular-nums" data-testid="big-value">
            {#if reading && reading.rows === 0}Messlücke{:else if reading}{formatCelsius(reading.raw.temperature_c)}{:else}—{/if}
          </span>
          {#if reading && reading.rows > 0 && reading.measurements > 1}
            <span class="text-xs text-muted-foreground">Mittel aus {reading.measurements} Messungen</span>
          {/if}
        </div>
        <!-- Immer vorhanden (auch leer): die Höhe der Anzeige soll beim Überfahren nicht springen. -->
        <div class="min-h-4 text-xs text-muted-foreground tabular-nums">
          {#if reading && reading.rows > 0 && reading.measurements > 1}Min./Max. {formatRange(reading.raw.temperature_min_c, reading.raw.temperature_max_c)}{/if}
        </div>
      </div>
      {#if reading}
        <dl class="m-0 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <div>
            <dt class="flex items-center gap-1 text-muted-foreground">Wirksamer Sollwert<HelpTip term="setpoint" /></dt>
            <dd class="m-0 font-medium tabular-nums">{formatRange(reading.raw.effective_min_c, reading.raw.effective_max_c)}</dd>
          </div>
          <div>
            <dt class="flex items-center gap-1 text-muted-foreground">Geplanter Sollwert<HelpTip term="planned" /></dt>
            <dd class="m-0 font-medium tabular-nums">{formatRange(reading.raw.scheduled_min_c, reading.raw.scheduled_max_c)}</dd>
          </div>
          {#if !compact}
            <div>
              <dt class="flex items-center gap-1 text-muted-foreground">Heizanforderung<HelpTip term="heat_request" /></dt>
              <dd class="m-0 font-medium tabular-nums">{formatHeat(reading.raw.heat_percent)}</dd>
            </div>
            <div>
              <dt class="flex items-center gap-1 text-muted-foreground">Sonnenabsenkung<HelpTip term="solar_setback" /></dt>
              <dd class="m-0 font-medium tabular-nums">{formatSetback(reading.raw.solar_setback_k)}</dd>
            </div>
          {/if}
        </dl>
      {/if}
    </div>

    {#if zoomed}
      <div class="mb-2 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onclick={() => (zoom = back(zoom))}><Undo2 size={14} aria-hidden="true" />Zurück</Button>
        <Button variant="outline" size="sm" onclick={() => (zoom = reset(zoom))}><ZoomOut size={14} aria-hidden="true" />Zurücksetzen</Button>
        <span class="text-sm text-muted-foreground">Stufe {zoom.stack.length}</span>
      </div>
    {/if}

    <!-- Diagramm. Tastatur: Pfeiltasten, Pos1, Ende. Die Datentabelle ist die Textalternative. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      bind:this={surface}
      class="hist-surface relative w-full rounded-md"
      role="group"
      aria-label="Diagramm {zoneName}, {periodLabel(period)}. Mit Pfeiltasten, Pos1 und Ende einzelne Punkte wählen."
      aria-describedby="{uid}-hint"
      tabindex="0"
      style="height:{height}px"
      onpointerdown={onDown}
      onpointermove={onMove}
      onpointerup={onUp}
      onpointercancel={onCancel}
      onpointerleave={onLeave}
      ondblclick={onDouble}
      onkeydown={onKey}
      onblur={() => {
        if (keyboard) {
          keyboard = false;
          selected = null;
        }
      }}
      data-testid="chart-surface"
    >
      {#if !hasMeasurements}
        <div class="grid h-full place-items-center rounded-md border border-dashed text-center text-sm text-muted-foreground">
          <p class="m-0 px-4">Für diesen Zeitraum liegen keine Messwerte vor{visibleGaps.length ? ' (Messlücke).' : '.'}</p>
        </div>
      {:else}
        <svg class="hist-svg block" {width} {height} viewBox="0 0 {width} {height}" aria-hidden="true">
          <defs>
            <clipPath id="{uid}-clip"><rect x={M.l} y={M.t} width={plotW} height={plotH} /></clipPath>
            <pattern id="{uid}-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="7" height="7" fill="var(--muted)" opacity="0.55" />
              <line x1="0" y1="0" x2="0" y2="7" stroke="var(--h-axis)" stroke-width="1.4" opacity="0.45" />
            </pattern>
          </defs>

          <!-- Raster und Achsen -->
          {#each scale.ticks as v (v)}
            <line x1={M.l} x2={M.l + plotW} y1={r1(yOf(v))} y2={r1(yOf(v))} stroke="var(--h-grid)" />
            <text x={M.l - 8} y={r1(yOf(v)) + 4} text-anchor="end">{temperatureTickLabel(v)}</text>
          {/each}
          {#each xTicks as tk (tk.t)}
            <line x1={r1(xOf(tk.t))} x2={r1(xOf(tk.t))} y1={M.t + plotH} y2={M.t + plotH + 4} stroke="var(--h-axis)" />
            <text x={r1(xOf(tk.t))} y={M.t + plotH + 18} text-anchor="middle">{tk.label}</text>
          {/each}

          <g clip-path="url(#{uid}-clip)">
            <!-- Messlücken: schraffiert, nie überbrückt -->
            {#each visibleGaps as g (g.start)}
              {@const r = rect(g.start, g.end)}
              <rect class="hist-gap" x={r.x} y={M.t} width={r.w} height={plotH} fill="url(#{uid}-hatch)" data-testid="gap" />
            {/each}
            {#each emptyBuckets as p (p.start)}
              {@const r = rect(p.start, p.end)}
              <rect class="hist-gap" x={r.x} y={M.t} width={Math.max(1, r.w)} height={plotH} fill="url(#{uid}-hatch)" data-testid="gap" />
            {/each}

            {#if flags.solar}
              {#each points as p (p.start)}
                {#if p.solar !== null && p.solar > 0}
                  {@const r = rect(p.start, p.end)}
                  <rect x={r.x} y={M.t} width={Math.max(1, r.w)} height={plotH} fill="var(--h-solar)" opacity="0.12" />
                  <rect x={r.x} y={M.t} width={Math.max(1, r.w)} height="4" fill="var(--h-solar)" />
                {/if}
              {/each}
            {/if}

            {#if flags.heating}
              {#each points as p (p.start)}
                {#if p.heat !== null && p.heat > 0}
                  {@const r = rect(p.start, p.end)}
                  {@const h = Math.max(1.5, (p.heat / 100) * heatH)}
                  <rect x={r.x} y={M.t + plotH - h} width={Math.max(1, r.w - 0.5)} height={h} fill="var(--h-heat)" opacity="0.55" />
                {/if}
              {/each}
            {/if}

            {#if flags.range}
              {#each tempRuns as run (run[0])}
                {#if run.length > 1}
                  <path d={bandPath(run)} fill="var(--h-temp)" opacity="0.16" />
                {:else}
                  {@const p = points[run[0]]}
                  <line x1={xOf(p.t)} x2={xOf(p.t)} y1={yOf(p.tempMax as number)} y2={yOf(p.tempMin as number)} stroke="var(--h-temp)" stroke-width="3" opacity="0.3" />
                {/if}
              {/each}
            {/if}

            {#if flags.actual}
              {#each tempRuns as run (run[0])}
                {#if run.length > 1}
                  <path d={areaPath(run)} fill="var(--h-temp)" opacity="0.07" />
                  <path d={linePath(run)} fill="none" stroke="var(--h-temp)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" data-testid="temp-line" />
                {:else}
                  <circle cx={xOf(points[run[0]].t)} cy={yOf(points[run[0]].temp as number)} r="2.6" fill="var(--h-temp)" />
                {/if}
              {/each}
            {/if}

            {#if flags.scheduled}
              {#each schedRuns as run (run[0])}
                <path d={stepPath(run, 'schedMax')} fill="none" stroke="var(--h-sched)" stroke-width="1.6" stroke-dasharray="6 4" />
                {#if stepDiffers(run, 'schedMin', 'schedMax')}
                  <path d={stepPath(run, 'schedMin')} fill="none" stroke="var(--h-sched)" stroke-width="1.2" stroke-dasharray="2 4" />
                {/if}
              {/each}
            {/if}

            {#if flags.effective}
              {#each effRuns as run (run[0])}
                <path d={stepPath(run, 'effMax')} fill="none" stroke="var(--h-eff)" stroke-width="2" />
                {#if stepDiffers(run, 'effMin', 'effMax')}
                  <path d={stepPath(run, 'effMin')} fill="none" stroke="var(--h-eff)" stroke-width="1.2" stroke-dasharray="2 3" />
                {/if}
              {/each}
            {/if}

            <!-- Zoom-Auswahl -->
            {#if selection}
              <rect
                x={r1(xOf(selection.from))}
                y={M.t}
                width={Math.max(1, r1(xOf(selection.to) - xOf(selection.from)))}
                height={plotH}
                fill="var(--h-temp)"
                opacity="0.14"
                stroke="var(--h-temp)"
                data-testid="zoom-selection"
              />
            {/if}
          </g>

          <line x1={M.l} x2={M.l + plotW} y1={M.t + plotH} y2={M.t + plotH} stroke="var(--h-axis)" opacity="0.5" />

          {#if picked}
            <line x1={r1(xOf(picked.t))} x2={r1(xOf(picked.t))} y1={M.t} y2={M.t + plotH} stroke="var(--foreground)" opacity="0.45" stroke-dasharray="3 3" />
            {#if picked.temp !== null && flags.actual}
              <circle cx={xOf(picked.t)} cy={yOf(picked.temp)} r="4.5" fill="var(--background)" stroke="var(--h-temp)" stroke-width="2" />
            {/if}
            {#if picked.effMax !== null && flags.effective}
              <circle cx={xOf(picked.t)} cy={yOf(picked.effMax)} r="3.5" fill="var(--h-eff)" />
            {/if}
          {/if}
        </svg>

        {#if picked && tip}
          <div
            class="hist-tip pointer-events-none absolute top-2 rounded-lg px-3 py-2 text-xs shadow-md"
            style="left:{tip.left}px;width:{TIP_W}px"
            data-testid="tooltip"
            data-side={tip.side}
          >
            <div class="mb-1 font-semibold">{whenOf(picked)}</div>
            {#if picked.rows === 0}
              <div>Messlücke – keine Messwerte.</div>
            {:else}
              <table class="w-full border-collapse"><tbody>
                {#if flags.actual}<tr><td>Messwert</td><td class="text-right font-medium tabular-nums">{formatCelsius(picked.raw.temperature_c)}</td></tr>{/if}
                {#if flags.range}<tr><td>Min./Max.</td><td class="text-right tabular-nums">{formatRange(picked.raw.temperature_min_c, picked.raw.temperature_max_c)}</td></tr>{/if}
                {#if flags.effective}<tr><td>Wirksam</td><td class="text-right tabular-nums">{formatRange(picked.raw.effective_min_c, picked.raw.effective_max_c)}</td></tr>{/if}
                {#if flags.scheduled}<tr><td>Geplant</td><td class="text-right tabular-nums">{formatRange(picked.raw.scheduled_min_c, picked.raw.scheduled_max_c)}</td></tr>{/if}
                {#if flags.heating}<tr><td>Heizen</td><td class="text-right tabular-nums">{formatHeat(picked.raw.heat_percent)}</td></tr>{/if}
                {#if flags.solar}<tr><td>Sonne</td><td class="text-right tabular-nums">{formatSetback(picked.raw.solar_setback_k)}</td></tr>{/if}
              </tbody></table>
            {/if}
          </div>
        {/if}
      {/if}
    </div>

    <!-- Kompakte Legende, schaltbar -->
    {#if !compact}
      <ul class="m-0 mt-2 flex list-none flex-wrap gap-x-1 gap-y-1 p-0" aria-label="Legende">
        {#each SERIES as s (s.key)}
          <li>
            <button
              type="button"
              class="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 text-xs hover:bg-muted {flags[s.key] ? '' : 'text-muted-foreground line-through opacity-70'}"
              aria-pressed={flags[s.key]}
              onclick={() => (flags[s.key] = !flags[s.key])}
            >
              {#if s.key === 'actual'}<span class="hist-sw" style="color:var(--h-temp)"></span>
              {:else if s.key === 'range'}<span class="hist-sw-box" style="background:var(--h-temp);opacity:.25"></span>
              {:else if s.key === 'scheduled'}<span class="hist-sw" style="color:var(--h-sched);border-top-style:dashed"></span>
              {:else if s.key === 'effective'}<span class="hist-sw" style="color:var(--h-eff)"></span>
              {:else if s.key === 'heating'}<span class="hist-sw-box" style="background:var(--h-heat);opacity:.6"></span>
              {:else}<span class="hist-sw-box" style="background:var(--h-solar)"></span>{/if}
              {s.label}
            </button>
          </li>
        {/each}
        <li class="inline-flex min-h-8 items-center gap-1.5 px-2 text-xs text-muted-foreground">
          <span class="hist-sw-box" style="background:repeating-linear-gradient(45deg,var(--muted),var(--muted) 3px,var(--h-axis) 3px,var(--h-axis) 4px)"></span>
          Messlücke<HelpTip term="gap" />
        </li>
      </ul>
    {/if}

    <p id="{uid}-hint" class="m-0 mt-2 text-xs text-muted-foreground" data-testid="hint" data-input={finePointer ? 'mouse' : 'touch'}>
      {hint}
    </p>
    <p class="m-0 mt-1 text-xs text-muted-foreground">
      {history.rows} Messzeilen · Abschnitte à {formatResolution(history.resolution_seconds)} · Zeitzone {history.timezone}
    </p>
    <div class="sr-only" aria-live="polite">{announce}</div>

    {#if showTable}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
        class="mt-3 max-h-80 overflow-auto rounded-lg border"
        role="region"
        aria-label="Datentabelle {zoneName}"
        tabindex="0"
      >
        <table class="w-full border-collapse text-xs tabular-nums" data-testid="data-table">
          <caption class="sr-only">
            Verlaufsdaten {zoneName}, {rangeText}, {rows.length} Abschnitte à {formatResolution(history.resolution_seconds)}
          </caption>
          <thead class="sticky top-0 bg-card">
            <tr class="text-left">
              <th scope="col" class="px-2 py-1.5">Zeit</th>
              <th scope="col" class="px-2 py-1.5">Messwert</th>
              <th scope="col" class="px-2 py-1.5">Min./Max.</th>
              <th scope="col" class="px-2 py-1.5">Geplant</th>
              <th scope="col" class="px-2 py-1.5">Wirksam</th>
              <th scope="col" class="px-2 py-1.5">Heizen</th>
              <th scope="col" class="px-2 py-1.5">Sonne</th>
            </tr>
          </thead>
          <tbody>
            {#each rows as p (p.start)}
              <tr class="border-t">
                <th scope="row" class="px-2 py-1 text-left font-normal whitespace-nowrap">{whenOf(p)}</th>
                {#if p.rows === 0}
                  <td colspan="6" class="px-2 py-1 text-muted-foreground">Messlücke – keine Messwerte</td>
                {:else}
                  <td class="px-2 py-1">{formatCelsius(p.raw.temperature_c)}</td>
                  <td class="px-2 py-1">{formatRange(p.raw.temperature_min_c, p.raw.temperature_max_c)}</td>
                  <td class="px-2 py-1">{formatRange(p.raw.scheduled_min_c, p.raw.scheduled_max_c)}</td>
                  <td class="px-2 py-1">{formatRange(p.raw.effective_min_c, p.raw.effective_max_c)}</td>
                  <td class="px-2 py-1">{formatHeat(p.raw.heat_percent)}</td>
                  <td class="px-2 py-1">{formatSetback(p.raw.solar_setback_k)}</td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
</div>
</section>
