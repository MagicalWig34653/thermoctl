<script lang="ts">
  import { tick } from 'svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { DAY_NAMES, WEEKDAYS } from '$lib/schedule/geometry';
  import type { Intent } from '$lib/schedule/intents';
  import { pointIntentOf, rangeIntentOf, type NewTimeField } from '$lib/schedule/newtime';
  import type { Plan } from '$lib/schedule/types';

  // Neue Zeit ohne Zeiger anlegen: Schaltpunkt (Tag, Uhrzeit, Modus) oder Zeitraum (Tag, Von, Bis, Modus).
  // Dieselben Absichten wie die Pinselgeste; der Server entscheidet über Überdeckung und Mindestlänge.
  // Eingaben bleiben bei jedem Fehler stehen, damit man sie berichtigen kann.
  let {
    plan,
    activeDay,
    busy,
    locked,
    brushModeId,
    onsubmit,
    onreload,
  }: {
    plan: Plan;
    activeDay: number;
    busy: boolean;
    /** Versionskonflikt: Bearbeiten ist gesperrt, bis neu geladen wird. */
    locked: boolean;
    brushModeId: number | null;
    /** Sendet die Absicht; liefert `error: null` bei Erfolg, sonst die Fehlermeldung. `status` ist die Erfolgsmeldung. */
    onsubmit: (intent: Intent) => Promise<{ error: string | null; status: string }>;
    onreload: () => void;
  } = $props();

  type Which = 'point' | 'range';
  let pointDay = $state(1);
  let rangeDay = $state(1);
  let pointTime = $state('06:30');
  let rangeStart = $state('06:30');
  let rangeEnd = $state('08:00');
  let toDayEnd = $state(false);
  let pointMode = $state<number | null>(null);
  let rangeMode = $state<number | null>(null);
  let error = $state<{ which: Which; text: string; field: NewTimeField | null } | null>(null);
  let success = $state<{ which: Which; text: string } | null>(null);
  let errorBox = $state<HTMLElement | null>(null);

  // Tag folgt dem im Plan gewählten Tag; Modus beginnt beim Pinselmodus.
  $effect(() => {
    pointDay = activeDay;
    rangeDay = activeDay;
  });
  const defaultMode = $derived(brushModeId ?? plan.modes[0]?.id ?? null);
  const pointModeId = $derived(pointMode ?? defaultMode);
  const rangeModeId = $derived(rangeMode ?? defaultMode);
  const disabled = $derived(busy || locked);

  const ids: Record<Which, Record<NewTimeField, string>> = {
    point: { time: 'newtime-point-time', start: '', end: '', mode: 'newtime-point-mode' },
    range: { time: '', start: 'newtime-range-start', end: 'newtime-range-end', mode: 'newtime-range-mode' },
  };

  async function fail(which: Which, text: string, field: NewTimeField | null) {
    error = { which, text, field };
    success = null;
    await tick();
    const target = field ? document.getElementById(ids[which][field]) : null;
    (target ?? errorBox)?.focus();
  }

  async function send(which: Which, planned: ReturnType<typeof pointIntentOf>) {
    if ('error' in planned) return fail(which, planned.error, planned.field);
    error = null;
    success = null;
    const result = await onsubmit(planned.intent);
    if (result.error === null) success = { which, text: result.status };
    else await fail(which, result.error, null);
  }

  const invalid = (which: Which, field: NewTimeField) => error?.which === which && error.field === field;
  const input = 'h-11 rounded-lg border bg-background px-3 aria-invalid:border-destructive';
</script>

<section aria-labelledby="newtime-title" class="rounded-xl border bg-card p-4" data-testid="new-time">
  <h2 id="newtime-title" class="mb-1 text-lg">Neue Zeit anlegen</h2>
  <p class="m-0 mb-3 text-sm text-muted-foreground">
    Ohne Zeiger: einen einzelnen Schaltpunkt oder einen ganzen Zeitraum innerhalb eines Tages eintragen. Der Modus gilt
    ab dem Beginn bis zum nächsten Schaltpunkt.
  </p>
  {#if locked}
    <div class="mb-3 flex flex-wrap items-center gap-3" data-testid="new-time-locked">
      <Notice tone="warn">Der Plan wurde in einer anderen Sitzung geändert. Anlegen ist gesperrt, bis du neu lädst.</Notice>
      <Button variant="outline" onclick={onreload}>Neu laden</Button>
    </div>
  {/if}
  {#if plan.modes.length === 0}
    <p class="m-0 text-sm text-muted-foreground">Es sind keine Modi vorhanden, daher kann keine Zeit angelegt werden.</p>
  {:else}
    <div class="grid gap-6 lg:grid-cols-2">
      <form
        class="grid content-start gap-3"
        aria-labelledby="newtime-point-title"
        data-testid="new-point"
        novalidate
        onsubmit={(e) => {
          e.preventDefault();
          void send('point', pointIntentOf(pointDay, pointTime, pointModeId));
        }}
      >
        <h3 id="newtime-point-title" class="m-0 text-base">Schaltpunkt</h3>
        <div class="flex flex-wrap items-end gap-3">
          <label class="grid gap-1 text-sm">
            <span>Tag</span>
            <select class={input} bind:value={pointDay} {disabled} name="weekday">
              {#each WEEKDAYS as d (d)}<option value={d}>{DAY_NAMES[d]}</option>{/each}
            </select>
          </label>
          <label class="grid gap-1 text-sm">
            <span>Uhrzeit</span>
            <input
              id={ids.point.time}
              type="time"
              class={input}
              bind:value={pointTime}
              {disabled}
              name="time_of_day"
              aria-invalid={invalid('point', 'time')}
              aria-describedby={error?.which === 'point' ? 'newtime-point-error' : undefined}
            />
          </label>
          <label class="grid gap-1 text-sm">
            <span>Modus</span>
            <select
              id={ids.point.mode}
              class={input}
              value={pointModeId}
              onchange={(e) => (pointMode = Number(e.currentTarget.value))}
              {disabled}
              name="mode_id"
            >
              {#each plan.modes as m (m.id)}<option value={m.id}>{m.name}</option>{/each}
            </select>
          </label>
          <Button type="submit" {disabled}>Punkt anlegen</Button>
        </div>
        {#if error?.which === 'point'}
          <div id="newtime-point-error" bind:this={errorBox} tabindex="-1" class="outline-none" data-testid="new-time-error">
            <Notice tone="error">{error.text}</Notice>
          </div>
        {:else if success?.which === 'point'}
          <Notice tone="success">{success.text}</Notice>
        {/if}
      </form>

      <form
        class="grid content-start gap-3"
        aria-labelledby="newtime-range-title"
        data-testid="new-range"
        novalidate
        onsubmit={(e) => {
          e.preventDefault();
          void send('range', rangeIntentOf(rangeDay, rangeStart, rangeEnd, toDayEnd, rangeModeId));
        }}
      >
        <h3 id="newtime-range-title" class="m-0 text-base">Zeitraum</h3>
        <div class="flex flex-wrap items-end gap-3">
          <label class="grid gap-1 text-sm">
            <span>Tag</span>
            <select class={input} bind:value={rangeDay} {disabled} name="weekday">
              {#each WEEKDAYS as d (d)}<option value={d}>{DAY_NAMES[d]}</option>{/each}
            </select>
          </label>
          <label class="grid gap-1 text-sm">
            <span>Von</span>
            <input
              id={ids.range.start}
              type="time"
              class={input}
              bind:value={rangeStart}
              {disabled}
              name="start_time"
              aria-invalid={invalid('range', 'start')}
              aria-describedby={error?.which === 'range' ? 'newtime-range-error' : undefined}
            />
          </label>
          <label class="grid gap-1 text-sm">
            <span>Bis</span>
            <input
              id={ids.range.end}
              type="time"
              class={input}
              bind:value={rangeEnd}
              disabled={disabled || toDayEnd}
              name="end_time"
              aria-invalid={invalid('range', 'end')}
              aria-describedby={error?.which === 'range' ? 'newtime-range-error' : undefined}
            />
          </label>
          <label class="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" class="size-5" bind:checked={toDayEnd} {disabled} name="end_boundary" />
            <span>Bis Tagesende (24:00)</span>
          </label>
          <label class="grid gap-1 text-sm">
            <span>Modus</span>
            <select
              id={ids.range.mode}
              class={input}
              value={rangeModeId}
              onchange={(e) => (rangeMode = Number(e.currentTarget.value))}
              {disabled}
              name="mode_id"
            >
              {#each plan.modes as m (m.id)}<option value={m.id}>{m.name}</option>{/each}
            </select>
          </label>
          <Button type="submit" {disabled}>Zeitraum anlegen</Button>
        </div>
        {#if error?.which === 'range'}
          <div id="newtime-range-error" bind:this={errorBox} tabindex="-1" class="outline-none" data-testid="new-time-error">
            <Notice tone="error">{error.text}</Notice>
          </div>
        {:else if success?.which === 'range'}
          <Notice tone="success">{success.text}</Notice>
        {/if}
        <p class="m-0 text-xs text-muted-foreground">Ein Zeitraum bleibt innerhalb eines Tages.</p>
      </form>
    </div>
  {/if}
</section>
