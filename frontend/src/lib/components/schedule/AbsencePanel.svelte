<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatInstant } from '$lib/rooms/logic';
  import { scheduleFailure } from '$lib/schedule/errors';
  import { createScheduleClient } from '$lib/schedule/client';
  import type { AbsenceState } from '$lib/schedule/types';

  // Abwesenheit der Wohnungssicht: „Ich bin bis <Datum> weg“. Welche Räume betroffen sind,
  // bestimmt allein der Server; die Anzeige nennt sie nur.
  const { api, session } = useApp();
  const client = createScheduleClient(api);

  let info = $state<AbsenceState | null>(null);
  let loadError = $state<string | null>(null);
  let message = $state<{ tone: 'error' | 'success'; text: string } | null>(null);
  let busy = $state(false);
  let returnOn = $state('');
  let temperature = $state('16');

  const names = $derived(
    info
      ? (session.bootstrap?.zones ?? []).filter((z) => info!.eligible_zone_ids.includes(z.id)).map((z) => z.name)
      : [],
  );

  async function load() {
    try {
      info = await client.absence();
      loadError = null;
    } catch (error) {
      loadError = scheduleFailure(error).message;
    }
  }
  onMount(() => void load());

  async function run(action: () => Promise<AbsenceState>, ok: string) {
    if (busy) return;
    busy = true;
    message = null;
    try {
      info = await action();
      message = { tone: 'success', text: ok };
    } catch (error) {
      message = { tone: 'error', text: scheduleFailure(error).message };
      await load();
    } finally {
      busy = false;
    }
  }

  function start(e: SubmitEvent) {
    e.preventDefault();
    void run(
      () => client.startAbsence({ temperature_c: temperature.trim().replace(',', '.'), return_on: returnOn }),
      'Abwesenheit geplant.',
    );
  }
</script>

<section class="max-w-[820px] rounded-xl border bg-card p-4" aria-labelledby="absence-title" data-testid="absence">
  <h2 id="absence-title">Abwesenheit</h2>
  <p class="text-sm text-muted-foreground">
    Du bist unterwegs? Deine Räume werden bis zur Rückkehr sparsamer geregelt.
  </p>
  {#if loadError && !info}
    <Notice tone="error">{loadError}</Notice>
  {:else if !info}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if message}<div class="mb-3"><Notice tone={message.tone}>{message.text}</Notice></div>{/if}
    {#if info.absence}
      <div class="grid gap-3">
        <p class="m-0">
          Abwesenheit bis <strong>{formatInstant(info.absence.ends_at, info.timezone)}</strong> mit
          <strong>{formatCelsius(info.absence.setback_temperature_c)}</strong>.
        </p>
        {#if info.absence.zones.length}
          <p class="m-0 text-sm text-muted-foreground">Räume: {info.absence.zones.map((z) => z.name).join(', ')}</p>
        {/if}
        <div>
          <Button variant="outline" disabled={busy} onclick={() => run(() => client.endAbsence(), 'Abwesenheit beendet.')}>Abwesenheit beenden</Button>
        </div>
      </div>
    {:else if info.can_start}
      <form class="grid max-w-md gap-3" onsubmit={start}>
        <label class="grid gap-1 text-sm">
          <span>Rückkehr am</span>
          <input type="date" required class="h-11 rounded-lg border bg-background px-3" bind:value={returnOn} />
        </label>
        <label class="grid gap-1 text-sm">
          <span>Temperatur während der Abwesenheit (°C)</span>
          <input type="text" inputmode="decimal" required class="h-11 rounded-lg border bg-background px-3" bind:value={temperature} />
        </label>
        <p class="m-0 text-sm text-muted-foreground">Gilt für: {names.join(', ') || '—'}</p>
        <div><Button type="submit" disabled={busy}>Abwesenheit planen</Button></div>
      </form>
    {:else}
      <p class="m-0 text-sm text-muted-foreground">Für dein Konto ist keine Abwesenheit möglich.</p>
    {/if}
  {/if}
</section>
