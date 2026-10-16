<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { scheduleFailure } from '$lib/schedule/errors';
  import { createScheduleClient } from '$lib/schedule/client';
  import type { VacationState } from '$lib/schedule/types';

  // Anlagenurlaub (nur Verwaltungssicht): ein laufender oder geplanter Urlaub, Daten in Ortszeit.
  const { api } = useApp();
  const client = createScheduleClient(api);

  let info = $state<VacationState | null>(null);
  let loadError = $state<string | null>(null);
  let message = $state<{ tone: 'error' | 'success'; text: string; field?: string | null } | null>(null);
  let busy = $state(false);
  let startDate = $state('');
  let endDate = $state('');
  let setback = $state('16');

  async function load() {
    try {
      info = await client.vacation();
      loadError = null;
    } catch (error) {
      loadError = scheduleFailure(error).message;
    }
  }
  onMount(() => void load());

  async function run(action: () => Promise<VacationState>, ok: string) {
    if (busy) return;
    busy = true;
    message = null;
    try {
      info = await action();
      message = { tone: 'success', text: ok };
    } catch (error) {
      const failure = scheduleFailure(error);
      message = { tone: 'error', text: failure.message, field: failure.field };
      await load();
    } finally {
      busy = false;
    }
  }

  function create(e: SubmitEvent) {
    e.preventDefault();
    void run(
      () =>
        client.setVacation({
          start_date: startDate,
          end_date: endDate,
          setback_temperature_c: setback.trim().replace(',', '.'),
        }),
      'Urlaub eingetragen.',
    );
  }
</script>

<section class="max-w-[820px] rounded-xl border bg-card p-4" aria-labelledby="vacation-title" data-testid="vacation">
  <h2 id="vacation-title">Urlaub der Anlage</h2>
  <p class="text-sm text-muted-foreground">
    Während des Urlaubs gilt für alle Räume die Absenktemperatur statt des Wochenplans.
  </p>
  {#if loadError && !info}
    <Notice tone="error">{loadError}</Notice>
  {:else if !info}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if message}<div class="mb-3"><Notice tone={message.tone}>{message.text}{#if message.field} ({message.field}){/if}</Notice></div>{/if}
    {#if info.vacation}
      <div class="grid gap-3">
        <p class="m-0">
          {info.vacation.running ? 'Läuft' : 'Geplant'}: <strong>{info.vacation.start_date}</strong> bis
          <strong>{info.vacation.end_date}</strong> mit <strong>{formatCelsius(info.vacation.setback_temperature_c)}</strong>
          <span class="text-sm text-muted-foreground">(Ortszeit {info.timezone})</span>
        </p>
        {#if info.can_manage}
          <div><Button variant="outline" disabled={busy} onclick={() => run(() => client.endVacation(), 'Urlaub beendet.')}>Urlaub beenden</Button></div>
        {/if}
      </div>
    {:else if info.can_manage}
      <form class="grid max-w-md gap-3" onsubmit={create}>
        <label class="grid gap-1 text-sm"><span>Erster Urlaubstag</span><input type="date" required class="h-11 rounded-lg border bg-background px-3" bind:value={startDate} /></label>
        <label class="grid gap-1 text-sm"><span>Letzter Urlaubstag</span><input type="date" required class="h-11 rounded-lg border bg-background px-3" bind:value={endDate} /></label>
        <label class="grid gap-1 text-sm"><span>Absenktemperatur (°C)</span><input type="text" inputmode="decimal" required class="h-11 rounded-lg border bg-background px-3" bind:value={setback} /></label>
        <div><Button type="submit" disabled={busy}>Urlaub eintragen</Button></div>
      </form>
    {:else}
      <p class="m-0 text-sm text-muted-foreground">Es ist kein Urlaub eingetragen.</p>
    {/if}
  {/if}
</section>
