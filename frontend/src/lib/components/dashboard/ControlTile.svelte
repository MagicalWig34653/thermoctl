<script lang="ts">
  import { Minus, Plus } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { formatCelsius, formatTemperature } from '$lib/rooms/decimal';
  import { formatInstant, roomActionsUi, roomStatus, toFailure, type Failure } from '$lib/rooms/logic';
  import type { Room } from '$lib/rooms/types';
  import Notice from '$lib/components/rooms/Notice.svelte';

  // Raum direkt bedienen: Sollwert in halben Grad und laufende Übersteuerung beenden. Es sind
  // dieselben Aufrufe wie auf der Raumseite; ob etwas erlaubt ist, entscheidet der Server
  // (`room.actions`), die Oberfläche blendet nur aus, was er nicht anbietet.
  let { room, timezone, heading, onchanged }: { room: Room; timezone: string; heading?: string; onchanged: () => void } = $props();
  const { api, prefix, session } = useApp();

  const ui = $derived(roomActionsUi(room, false));
  const tenant = $derived(session.bootstrap?.ui_profile === 'tenant');
  const status = $derived(roomStatus(room, tenant));
  let busy = $state(false);
  let failure = $state<Failure | null>(null);
  const base = $derived(`/ui-api/rooms/${room.id}`);

  async function call(request: () => Promise<unknown>) {
    if (busy) return;
    busy = true;
    failure = null;
    try {
      await request();
      onchanged();
    } catch (error) {
      failure = toFailure(error);
      onchanged();
    } finally {
      busy = false;
    }
  }
  function step(direction: 'up' | 'down') {
    const modeId = room.setpoint.mode_id;
    if (modeId === null) return;
    void call(() => api.post(`${base}/setpoint-step`, { mode_id: modeId, direction }));
  }
  const cancelOverride = () => call(() => api.post(`${base}/override/cancel`, {}));
</script>

<section class="flex h-full min-w-0 flex-col gap-3 rounded-xl border bg-card p-4" data-testid="tile-control" aria-label={`${heading ?? room.name} bedienen`}>
  <div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
    <h3 class="max-w-full min-w-0 text-base leading-snug [overflow-wrap:anywhere]">{heading ?? room.name}</h3>
    <a class="text-xs text-muted-foreground underline-offset-2 hover:underline" href={appHref(prefix, `/raeume/${room.id}`)}>Details</a>
  </div>

  <div class="flex items-baseline gap-2">
    <span class="text-3xl leading-none font-semibold tracking-tight tabular-nums" aria-label={`Ist ${formatCelsius(room.temperature_c)}`}>{formatTemperature(room.temperature_c)}<span class="ml-1 text-base font-normal text-muted-foreground">°C</span></span>
    <span class="text-xs text-muted-foreground">{status.label}</span>
  </div>

  {#if ui.step.show}
    <div>
      <div class="mb-1 text-xs text-muted-foreground">Soll</div>
      <div class="flex items-center gap-3">
        <Button variant="outline" size="icon" class="size-11" aria-label={`Sollwert in ${room.name} um ein halbes Grad senken`} disabled={ui.step.disabled || busy} onclick={() => step('down')}><Minus aria-hidden="true" /></Button>
        <output class="min-w-16 text-center text-2xl font-semibold tabular-nums" aria-live="polite">{formatTemperature(room.setpoint.temperature_c)}°</output>
        <Button variant="outline" size="icon" class="size-11" aria-label={`Sollwert in ${room.name} um ein halbes Grad erhöhen`} disabled={ui.step.disabled || busy} onclick={() => step('up')}><Plus aria-hidden="true" /></Button>
      </div>
      {#if ui.step.reason}<p class="mt-2 mb-0 text-xs text-muted-foreground">{ui.step.reason}</p>{/if}
    </div>
  {:else}
    <p class="m-0 text-sm text-muted-foreground">Soll {formatCelsius(room.setpoint.temperature_c)} · hier nicht änderbar.</p>
  {/if}

  {#if room.override}
    <div class="text-sm">
      <span class="font-medium">Übersteuert</span>
      {room.override.ends_at ? `bis ${formatInstant(room.override.ends_at, timezone)}` : 'ohne Ende'}
      {#if ui.cancelOverride.show}
        <Button class="mt-2 w-full" variant="outline" disabled={busy} onclick={cancelOverride}>Übersteuerung beenden</Button>
      {/if}
    </div>
  {/if}

  {#if failure}<Notice tone="error">{failure.message}</Notice>{/if}
</section>
