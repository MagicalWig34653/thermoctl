<script lang="ts">
  import { Trash2, X } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { DAY_NAMES, DAY_END, WEEKDAYS, modeColor, type Block } from '$lib/schedule/geometry';
  import { clockValue, exactTimes, nudge, parseClock, toDay, type Planned } from '$lib/schedule/inspector';
  import { range, type Intent } from '$lib/schedule/intents';
  import type { PlanMode } from '$lib/schedule/types';

  let {
    block,
    blocks,
    modes,
    editable,
    onintent,
    onmessage,
    onclose,
  }: {
    block: Block;
    blocks: Block[];
    modes: PlanMode[];
    editable: boolean;
    onintent: (intent: Intent) => void;
    onmessage: (text: string) => void;
    onclose: () => void;
  } = $props();

  let heading = $state<HTMLElement | null>(null);
  let startText = $state('');
  let endText = $state('');
  let otherDay = $state(1);
  const locked = $derived(!editable);
  /** Tage nach dem Beginn, an dem der Block endet (0 = am selben Tag), und sein Ende in Minuten dieses Tages. */
  const endDays = $derived(Math.floor((block.end - 1) / DAY_END));
  const endOfDay = $derived(block.end - endDays * DAY_END);

  // Eingabefelder folgen dem Serverstand (neuer Block -> neue Werte).
  $effect(() => {
    startText = clockValue(block.start);
    endText = clockValue(endOfDay);
    otherDay = block.weekday === 7 ? 6 : block.weekday + 1;
  });

  export function focus() {
    heading?.focus();
  }

  function run(planned: Planned) {
    if ('error' in planned) {
      onmessage(planned.error);
      startText = clockValue(block.start);
      endText = clockValue(endOfDay);
    } else {
      onintent(planned.intent);
    }
  }

  function commitTimes() {
    const start = parseClock(startText);
    const end = parseClock(endText, true);
    if (start === null || end === null) {
      onmessage('Bitte gültige Uhrzeiten eingeben.');
      startText = clockValue(block.start);
      endText = clockValue(endOfDay);
      return;
    }
    const newEnd = endDays * DAY_END + end;
    if (start === block.start && newEnd === block.end) return;
    run(exactTimes(block, blocks, start, newEnd));
  }

  function setMode(modeId: number) {
    if (modeId === block.modeId) return;
    onintent({ type: 'setMode', block, pointId: block.pointId, modeId });
  }
</script>

<section
  class="flex flex-col gap-4"
  aria-labelledby="inspector-title"
  data-testid="inspector"
>
  <div class="flex items-start justify-between gap-2">
    <div>
      <p class="m-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">Zeit anpassen</p>
      <h3 id="inspector-title" class="m-0 text-xl" tabindex="-1" bind:this={heading}>
        {DAY_NAMES[block.weekday]}
      </h3>
      <p class="m-0 text-sm text-muted-foreground" data-testid="inspector-range">
        {range(block).replace(`${DAY_NAMES[block.weekday]}, `, '').replace(' bis ', '–')} · {block.modeName}{block.temperatureC
          ? ` · ${formatCelsius(block.temperatureC)}`
          : ''}
      </p>
    </div>
    <Button variant="ghost" size="icon" onclick={onclose} aria-label="Auswahl schließen">
      <X size={18} aria-hidden="true" />
    </Button>
  </div>

  {#if !editable}
    <p class="m-0 text-sm text-muted-foreground">Du kannst diesen Plan ansehen, aber nicht ändern.</p>
  {/if}

  <fieldset class="m-0 border-0 p-0" disabled={locked}>
    <legend class="mb-2 text-sm font-medium">Temperatur</legend>
    <div class="flex flex-wrap gap-2">
      {#each modes as mode (mode.id)}
        <button
          type="button"
          class="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm hover:bg-muted disabled:opacity-60 aria-pressed:border-foreground aria-pressed:bg-muted"
          aria-pressed={mode.id === block.modeId}
          onclick={() => setMode(mode.id)}
        >
          <span class="size-3 rounded-full" style="background:{modeColor(mode.warmth)}" aria-hidden="true"></span>
          {mode.name}
          {#if mode.temperature_c}<span class="text-muted-foreground">{formatCelsius(mode.temperature_c)}</span>{/if}
        </button>
      {/each}
    </div>
  </fieldset>

  <fieldset class="m-0 grid grid-cols-2 gap-3 border-0 p-0" disabled={locked}>
    <legend class="mb-2 text-sm font-medium">Exakte Zeiten</legend>
    <label class="grid gap-1 text-sm">
      <span>Beginn</span>
      <input
        type="time"
        class="h-11 rounded-lg border bg-background px-3"
        bind:value={startText}
        onchange={commitTimes}
        data-testid="start-time"
      />
    </label>
    <label class="grid gap-1 text-sm">
      <span>Ende</span>
      <input
        type="time"
        class="h-11 rounded-lg border bg-background px-3"
        bind:value={endText}
        onchange={commitTimes}
        data-testid="end-time"
      />
    </label>
    <p class="col-span-2 m-0 -mt-1 text-xs text-muted-foreground">
      Ende 00:00 bedeutet Mitternacht{endDays > 0 ? `; der Bereich endet am ${DAY_NAMES[((block.weekday - 1 + endDays) % 7) + 1]}` : ''}. Beginn und Ende einzeln ändern.
    </p>
    <div class="flex gap-2">
      <Button variant="outline" size="sm" class="min-h-9 flex-1" onclick={() => run(nudge(block, blocks, 'start', -15))}>Beginn −15</Button>
      <Button variant="outline" size="sm" class="min-h-9 flex-1" onclick={() => run(nudge(block, blocks, 'start', 15))}>+15</Button>
    </div>
    <div class="flex gap-2">
      <Button variant="outline" size="sm" class="min-h-9 flex-1" onclick={() => run(nudge(block, blocks, 'end', -15))}>Ende −15</Button>
      <Button variant="outline" size="sm" class="min-h-9 flex-1" onclick={() => run(nudge(block, blocks, 'end', 15))}>+15</Button>
    </div>
  </fieldset>

  <fieldset class="m-0 grid gap-2 border-0 p-0" disabled={locked}>
    <legend class="mb-2 text-sm font-medium">Anderer Tag</legend>
    <div class="flex flex-wrap items-center gap-2">
      <label class="sr-only" for="other-day">Zieltag</label>
      <select id="other-day" class="h-11 rounded-lg border bg-background px-3 text-sm" bind:value={otherDay}>
        {#each WEEKDAYS as d (d)}
          {#if d !== block.weekday}<option value={d}>{DAY_NAMES[d]}</option>{/if}
        {/each}
      </select>
      <Button variant="outline" onclick={() => run(toDay(block, blocks, otherDay, false))}>Dorthin verschieben</Button>
      <Button variant="outline" onclick={() => run(toDay(block, blocks, otherDay, true))}>Kopie anlegen</Button>
    </div>
  </fieldset>

  <div>
    <Button variant="destructive" disabled={locked} onclick={() => onintent({ type: 'delete', block })}>
      <Trash2 size={16} aria-hidden="true" />Zeit entfernen
    </Button>
  </div>
</section>
