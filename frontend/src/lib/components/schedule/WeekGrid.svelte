<script lang="ts">
  // Kompakte Wochenübersicht mit sieben Tageszeilen. Die Komponente zeichnet und liest Gesten;
  // sie entscheidet nichts Fachliches: jede Geste wird zu einer Absicht (`onintent`), die der
  // Server prüft. Vorschau während der Geste und der Ghost während des Speicherns sind reine Darstellung.
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatMinute } from '$lib/rooms/logic';
  import {
    DAY_NAMES,
    DAY_SHORT,
    WEEKDAYS,
    dayAt,
    drawShape,
    minuteAt,
    modeColor,
    moveShape,
    percentOf,
    refOf,
    resizeShape,
    sameRef,
    type Block,
    type BlockRef,
    type Edge,
    type Shape,
  } from '$lib/schedule/geometry';
  import type { Ghost, Intent } from '$lib/schedule/intents';
  import { blockKeyAction, deleteKeyAllowed, historyKey, TEXT_CONTEXT } from '$lib/schedule/keyboard';

  let {
    blocks,
    selected,
    ghost,
    editable,
    brushModeId,
    activeDay = $bindable(1),
    onselect,
    onopen,
    onintent,
    onmessage,
    onremove,
    onhistory,
  }: {
    blocks: Block[];
    selected: BlockRef | null;
    ghost: Ghost | null;
    /** Gesten und Tastatur dürfen Änderungen auslösen (Recht, kein laufender Schreibaufruf). */
    editable: boolean;
    brushModeId: number | null;
    activeDay?: number;
    onselect: (ref: BlockRef | null) => void;
    onopen: (ref: BlockRef) => void;
    onintent: (intent: Intent) => void;
    onmessage: (text: string) => void;
    onremove: () => void;
    onhistory: (kind: 'undo' | 'redo') => void;
  } = $props();

  type Kind = 'draw' | 'move' | 'start' | 'end';
  interface Gesture {
    kind: Kind;
    pointerId: number;
    weekday: number;
    block: Block | null;
    anchor: number;
    grabOffset: number;
    x: number;
    y: number;
    moved: boolean;
    touch: boolean;
    shape: Shape | null;
  }

  let root = $state<HTMLElement | null>(null);
  let gesture = $state<Gesture | null>(null);
  let ignoreClick = false;
  let refocus = false;

  const dayBlocks = (weekday: number) => blocks.filter((b) => b.weekday === weekday);

  function tracks(): { weekday: number; el: HTMLElement; rect: DOMRect }[] {
    if (!root) return [];
    return Array.from(root.querySelectorAll<HTMLElement>('[data-track]')).map((el) => ({
      weekday: Number(el.dataset.track),
      el,
      rect: el.getBoundingClientRect(),
    }));
  }

  /** Wochentag unter dem Zeiger; außerhalb der Zeilen der nächstliegende (die Geste endet nicht an der Kante). */
  function locate(x: number, y: number): { weekday: number; minute: number } | null {
    const t = tracks();
    if (!t.length) return null;
    const rows = t.map((r) => ({ weekday: r.weekday, top: r.rect.top, bottom: r.rect.bottom }));
    let weekday = dayAt(y, rows);
    if (weekday === null) {
      const nearest = rows.reduce((best, r) =>
        Math.min(Math.abs(y - r.top), Math.abs(y - r.bottom)) <
        Math.min(Math.abs(y - best.top), Math.abs(y - best.bottom))
          ? r
          : best,
      );
      weekday = nearest.weekday;
    }
    const rect = t[0].rect;
    return { weekday, minute: minuteAt(x, rect.left, rect.width) };
  }

  function begin(e: PointerEvent, weekday: number, block: Block | null, kind: Kind | null) {
    if (!editable || e.button !== 0 || !e.isPrimary) return;
    const where = locate(e.clientX, e.clientY);
    if (!where) return;
    e.stopPropagation();
    // Rest vom Vortag: nicht ziehbar (der Server kennt dafür keinen eigenen Schaltpunkt).
    if (block?.carry) return;
    ignoreClick = false;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    activeDay = weekday;
    const overpaint = e.shiftKey;
    const drawing = block === null || (overpaint && kind === 'move');
    gesture = {
      kind: drawing ? 'draw' : (kind ?? 'move'),
      pointerId: e.pointerId,
      weekday,
      block: drawing ? null : block,
      anchor: where.minute,
      grabOffset: block ? where.minute - block.start : 0,
      x: e.clientX,
      y: e.clientY,
      moved: false,
      touch: e.pointerType === 'touch',
      shape: null,
    };
  }

  function compute(g: Gesture, e: { clientX: number; clientY: number; altKey: boolean; shiftKey: boolean }): Shape | null {
    const where = locate(e.clientX, e.clientY);
    if (!where) return null;
    if (g.kind === 'draw') return drawShape(blocks, g.weekday, g.anchor, where.minute, e.shiftKey);
    if (!g.block) return null;
    if (g.kind === 'move')
      return moveShape(g.block, blocks, where.minute, g.grabOffset, where.weekday, e.altKey);
    return resizeShape(g.block, g.kind as Edge, blocks, where.minute);
  }

  function move(e: PointerEvent) {
    const g = gesture;
    if (!g || e.pointerId !== g.pointerId) return;
    const slop = g.touch ? 8 : 4;
    if (!g.moved && Math.hypot(e.clientX - g.x, e.clientY - g.y) < slop) return;
    gesture = { ...g, moved: true, shape: compute(g, e) };
  }

  function finish(e: PointerEvent) {
    const g = gesture;
    if (!g || e.pointerId !== g.pointerId) return;
    gesture = null;
    if (!g.moved) return;
    ignoreClick = true;
    setTimeout(() => (ignoreClick = false), 0);
    const shape = compute(g, e);
    if (!shape) return;
    if (!shape.valid) {
      onmessage(shape.reason || 'Änderung nicht möglich.');
      return;
    }
    if (g.kind === 'draw') {
      if (brushModeId === null) return;
      onintent({
        type: 'paint',
        weekday: shape.weekday,
        start: shape.start,
        end: shape.end,
        modeId: brushModeId,
      });
    } else if (g.kind === 'move' && g.block) {
      const intent: Intent = {
        type: shape.copy ? 'duplicate' : 'move',
        block: refOf(g.block),
        toWeekday: shape.weekday,
        toStart: shape.start,
      };
      onintent(intent);
    } else if (g.block) {
      onintent({
        type: 'resize',
        block: refOf(g.block),
        newStart: shape.start,
        newEnd: shape.end,
        adjacent: shape.adjacent === true,
      });
    }
  }

  function cancelGesture(message = true): boolean {
    if (!gesture) return false;
    if (gesture.moved) {
      ignoreClick = true;
      setTimeout(() => (ignoreClick = false), 0);
    }
    gesture = null;
    if (message) onmessage('Geste abgebrochen. Nichts wurde geändert.');
    return true;
  }

  function blockKeydown(e: KeyboardEvent, block: Block) {
    const action = blockKeyAction(e, block, blocks);
    if (action.type === 'none') {
      // Pfeile gehören dem Block, nicht dem Seitenscroll.
      if (e.key.startsWith('Arrow') && !e.ctrlKey && !e.metaKey) e.preventDefault();
      return;
    }
    e.preventDefault();
    if (action.type === 'open') {
      onselect(refOf(block));
      onopen(refOf(block));
    } else if (action.type === 'blocked') {
      onmessage(action.reason);
    } else if (editable) {
      refocus = true;
      setTimeout(() => (refocus = false), 4000);
      onintent(action.intent);
    }
  }

  function windowKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (cancelGesture()) {
        e.preventDefault();
        return;
      }
      const target = e.target as Element | null;
      if (selected && !(target instanceof Element && target.closest('[role="dialog"]'))) onselect(null);
      return;
    }
    // Ereignisfelder liegen auf dem Prototyp: kein `{ ...e }`, sondern ausdrücklich lesen.
    const keys = {
      key: e.key,
      altKey: e.altKey,
      shiftKey: e.shiftKey,
      ctrlKey: e.ctrlKey,
      metaKey: e.metaKey,
      repeat: e.repeat,
      defaultPrevented: e.defaultPrevented,
      target: e.target instanceof Element ? e.target : null,
    };
    const history = historyKey(keys);
    if (history) {
      e.preventDefault();
      onhistory(history);
      return;
    }
    if (
      selected &&
      editable &&
      !gesture &&
      deleteKeyAllowed(keys)
    ) {
      e.preventDefault();
      refocus = false;
      onremove();
    }
  }

  // Nach einer Tastenänderung wird der Block neu gezeichnet; der Fokus soll mit.
  $effect(() => {
    void blocks;
    if (refocus && selected && root) {
      const key = `${selected.weekday}-${selected.start}-${selected.end}`;
      root.querySelector<HTMLElement>(`[data-block="${key}"]`)?.focus();
      refocus = false;
    }
  });

  const tempText = (b: Block) => (b.temperatureC ? formatCelsius(b.temperatureC) : '');
  const label = (b: Block, isSelected: boolean) =>
    `${DAY_NAMES[b.weekday]}, ${formatMinute(b.start)} bis ${formatMinute(b.end)}, ${b.modeName}${
      b.temperatureC ? ', ' + formatCelsius(b.temperatureC) : ''
    }${b.carry ? ', setzt sich vom Vortag fort' : ''}${isSelected ? ', ausgewählt' : ''}`;

  const box = (start: number, end: number) =>
    `left:${percentOf(start)}%;width:${percentOf(end) - percentOf(start)}%`;
  const AXIS = [0, 6, 12, 18, 24];
  const grid =
    'repeating-linear-gradient(to right, transparent 0, transparent calc(25% - 1px), var(--border) calc(25% - 1px), var(--border) 25%)';
</script>

<svelte:window onpointermove={move} onpointerup={finish} onpointercancel={() => cancelGesture(false)} onkeydown={windowKeydown} />

<div bind:this={root} class="select-none" role="group" aria-label="Wochenübersicht" data-testid="week-grid">
  <div class="grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-2">
    <span></span>
    <div class="relative mb-1 h-4 text-xs text-muted-foreground" aria-hidden="true">
      {#each AXIS as h (h)}
        <span
          class="absolute top-0"
          style="left:{(h / 24) * 100}%;transform:translateX({h === 0 ? '0' : h === 24 ? '-100%' : '-50%'})"
          >{String(h).padStart(2, '0')}</span
        >
      {/each}
    </div>
    {#each WEEKDAYS as weekday (weekday)}
      <button
        type="button"
        class="my-0.5 grid min-h-11 place-items-center rounded-md text-sm font-medium hover:bg-muted aria-pressed:bg-muted"
        aria-pressed={activeDay === weekday}
        aria-label={DAY_NAMES[weekday]}
        title={DAY_NAMES[weekday]}
        onclick={() => {
          activeDay = weekday;
          onselect(null);
        }}>{DAY_SHORT[weekday]}</button
      >
      <div
        class="relative my-0.5 h-11 touch-pan-y rounded-md border bg-muted/50"
        class:cursor-crosshair={editable}
        style="background-image:{grid}"
        data-track={weekday}
        role="group"
        aria-label={DAY_NAMES[weekday]}
        onpointerdown={(e) => begin(e, weekday, null, 'draw')}
      >
        {#each dayBlocks(weekday) as block (`${block.weekday}-${block.start}-${block.end}`)}
          {@const isSelected = !!selected && sameRef(selected, block)}
          {@const dimmed = !!ghost?.source && sameRef(ghost.source, block)}
          <div
            class="absolute inset-y-0.5 rounded-md border border-black/10 {dimmed ? 'opacity-40' : ''} {block.carry
              ? 'border-dashed'
              : ''}"
            class:ring-2={isSelected}
            class:ring-foreground={isSelected}
            class:z-10={isSelected}
            style="{box(block.start, block.end)};background:{modeColor(block.warmth)}"
          >
            <button
              type="button"
              class="absolute inset-0 flex min-w-0 touch-pan-y items-center gap-1 overflow-hidden rounded-md px-1.5 text-left text-xs font-medium text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.55)] {editable &&
              !block.carry
                ? 'cursor-grab'
                : ''}"
              data-block="{block.weekday}-{block.start}-{block.end}"
              aria-label={label(block, isSelected)}
              aria-pressed={isSelected}
              title="{DAY_NAMES[block.weekday]} · {formatMinute(block.start)}–{formatMinute(block.end)} · {block.modeName}"
              onpointerdown={(e) => begin(e, weekday, block, 'move')}
              onclick={() => {
                if (ignoreClick) return;
                onselect(refOf(block));
              }}
              onkeydown={(e) => blockKeydown(e, block)}
            >
              {#if block.carry}<span aria-hidden="true">↤</span>{/if}
              <span class="truncate">{block.modeName}</span>
              {#if block.end - block.start >= 180}<span class="shrink-0 opacity-90">{tempText(block)}</span>{/if}
            </button>
            {#if editable && !block.carry}
              <span
                class="absolute inset-y-0 left-0 z-10 w-[min(12px,25%)] cursor-ew-resize touch-pan-y rounded-l-md bg-black/20 hover:bg-black/40"
                role="presentation"
                data-grip="start"
                onpointerdown={(e) => begin(e, weekday, block, 'start')}
              ></span>
              <span
                class="absolute inset-y-0 right-0 z-10 w-[min(12px,25%)] cursor-ew-resize touch-pan-y rounded-r-md bg-black/20 hover:bg-black/40"
                role="presentation"
                data-grip="end"
                onpointerdown={(e) => begin(e, weekday, block, 'end')}
              ></span>
            {/if}
          </div>
        {/each}

        {#if gesture?.moved && gesture.shape && gesture.shape.weekday === weekday}
          {@const s = gesture.shape}
          <div
            class="pointer-events-none absolute inset-y-0.5 z-20 grid place-items-center overflow-hidden rounded-md border-2 border-dashed text-xs font-semibold {s.valid
              ? 'border-foreground bg-foreground/15'
              : 'border-destructive bg-destructive/20 text-destructive'}"
            style={box(s.start, s.end)}
            data-testid="gesture-ghost"
            data-valid={s.valid}
          >
            <span class="truncate px-1"
              >{s.valid ? `${s.copy ? 'Kopie · ' : ''}${formatMinute(s.start)}–${formatMinute(s.end)}` : 'Belegt'}</span
            >
          </div>
        {/if}

        {#if ghost && ghost.weekday === weekday}
          <div
            class="pointer-events-none absolute inset-y-0.5 z-20 grid place-items-center overflow-hidden rounded-md border-2 border-dashed border-foreground bg-foreground/10 text-xs font-semibold motion-safe:animate-pulse"
            style={box(ghost.start, ghost.end)}
            data-testid="pending-ghost"
          >
            <span class="truncate px-1">{ghost.label}</span>
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>
