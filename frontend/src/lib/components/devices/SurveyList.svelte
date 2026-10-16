<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Badge } from '$lib/components/ui/badge';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { createQuery } from '$lib/history/query.svelte';
  import { formatInstant } from '$lib/rooms/logic';

  const { api } = useApp();
  const client = createDevicesClient(api);
  const survey = createQuery(() => client.survey());
  let text = $state('');
  let onlyIssues = $state(false);

  onMount(() => {
    survey.load();
    return () => survey.cancel();
  });

  const shown = $derived(
    (survey.data?.devices ?? []).filter((d) => {
      if (onlyIssues && d.is_fine) return false;
      const q = text.trim().toLocaleLowerCase('de-DE');
      if (!q) return true;
      return [d.name, d.model ?? '', d.integration ?? '', ...d.zones, ...d.capabilities].some((v) => v.toLocaleLowerCase('de-DE').includes(q));
    }),
  );
  const issueCount = $derived((survey.data?.devices ?? []).filter((d) => !d.is_fine).length);
  const tone = (kind: string) => (kind === 'battery' || kind === 'radio' ? 'warn' : kind === 'disabled' ? 'muted' : 'bad');
</script>

<section aria-labelledby="survey-title" class="grid gap-4" data-testid="survey">
  <div>
    <h2 id="survey-title" class="m-0">Geräte</h2>
    <p class="m-0 text-sm text-muted-foreground">Alle bekannten Geräte, Auffälliges zuerst.</p>
  </div>
  {#if survey.failure && !survey.data}
    <Notice tone="error">{survey.failure.message}</Notice>
  {:else if !survey.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    <div class="flex flex-wrap items-center gap-3">
      <label class="sr-only" for="survey-search">Geräte filtern</label>
      <input id="survey-search" type="search" class="h-10 min-w-0 flex-1 rounded-lg border bg-background px-3 sm:max-w-xs" placeholder="Name, Modell, Raum …" bind:value={text} />
      <label class="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" class="size-4" bind:checked={onlyIssues} />nur Auffälliges ({issueCount})</label>
      {#if survey.data.without_zone}<span class="text-sm text-muted-foreground">{survey.data.without_zone} ohne Raum</span>{/if}
    </div>
    {#if survey.failure}<Notice tone="error">{survey.failure.message}</Notice>{/if}
    {#if !shown.length}
      <p class="text-muted-foreground">Keine Geräte gefunden.</p>
    {:else}
      <ul class="m-0 grid list-none gap-3 p-0 md:grid-cols-2" aria-label="Geräteliste">
        {#each shown as d (d.id)}
          <li class="grid content-start gap-2 rounded-xl border bg-card p-4" data-testid="survey-device">
            <div class="flex flex-wrap items-baseline justify-between gap-x-3">
              <strong class="min-w-0 break-words">{d.name}</strong>
              {#if d.is_group}<Badge variant="outline">Gruppe</Badge>{/if}
            </div>
            <div class="text-sm text-muted-foreground">{d.model ?? 'Modell unbekannt'}{#if d.integration} · {d.integration}{/if}</div>
            {#if d.findings.length}
              <ul class="m-0 flex list-none flex-wrap gap-1 p-0" aria-label="Befunde">
                {#each d.findings as f, i (i)}
                  <li class="rounded-md border px-2 py-0.5 text-xs {tone(f.kind) === 'bad' ? 'border-destructive/40 bg-destructive/10 text-destructive' : tone(f.kind) === 'warn' ? 'border-amber-500/50 bg-amber-500/10' : 'bg-muted text-muted-foreground'}">{f.text}</li>
                {/each}
              </ul>
            {:else}
              <p class="m-0 text-sm text-muted-foreground">Keine Auffälligkeiten.</p>
            {/if}
            <div class="text-sm text-muted-foreground">
              {#if d.last_heard}Zuletzt gehört: {formatInstant(d.last_heard, survey.data.timezone ?? 'UTC')}{/if}
              {#if d.battery_percent !== null} · Batterie {Math.round(Number(d.battery_percent))} %{/if}
              {#if d.radio_quality !== null} · LQI {d.radio_quality}{/if}
            </div>
            <div class="text-sm">
              {#if d.zones.length}<span class="text-muted-foreground">Räume:</span> {d.zones.join(', ')}{:else}<span class="text-muted-foreground">Keinem Raum zugeordnet.</span>{/if}
            </div>
            {#if d.capabilities.length}
              <ul class="m-0 flex list-none flex-wrap gap-1 p-0" aria-label="Fähigkeiten">
                {#each d.capabilities as c (c)}<li class="rounded-md bg-muted px-2 py-0.5 text-xs">{c}</li>{/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>
