<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { createQuery } from '$lib/history/query.svelte';
  import { formatInstant } from '$lib/rooms/logic';

  // Schaltprotokoll: jeder gesendete oder zurückgehaltene Befehl an einen Aktor (nur Lesen).
  const { api } = useApp();
  const client = createDevicesClient(api);
  let fromDate = $state('');
  let toDate = $state('');
  let zone = $state('');
  let outcome = $state('');
  let page = $state(1);

  const log = createQuery(() => client.commands({ from_date: fromDate, to_date: toDate, zone, outcome, page }));
  $effect(() => {
    void [fromDate, toDate, zone, outcome, page];
    log.load();
  });
  onMount(() => () => log.cancel());
  const reset = () => (page = 1);
  const field = 'h-9 rounded-lg border bg-background px-2';
</script>

<form class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onsubmit={(e) => e.preventDefault()} aria-label="Filter Schaltprotokoll">
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Von</span><input type="date" class={field} bind:value={fromDate} onchange={reset} /></label>
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Bis</span><input type="date" class={field} bind:value={toDate} onchange={reset} /></label>
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Raum</span>
    <select class={field} bind:value={zone} onchange={reset}><option value="">Alle</option>{#each log.data?.zone_names ?? [] as z (z)}<option value={z}>{z}</option>{/each}</select></label>
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Ergebnis</span>
    <select class={field} bind:value={outcome} onchange={reset}><option value="">Alle</option>{#each log.data?.outcomes ?? [] as o (o.code)}<option value={o.code}>{o.label}</option>{/each}</select></label>
</form>

{#if log.loading && !log.data}
  <p class="text-muted-foreground" role="status">Schaltprotokoll wird geladen …</p>
{:else if !log.data}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{log.failure?.message ?? 'Das Schaltprotokoll konnte nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={() => log.load()}>Erneut laden</Button>
  </div>
{:else}
  {@const data = log.data}
  {#if log.failure}<div class="mb-3"><Notice tone={log.failure.kind === 'validation' ? 'error' : 'warn'}>{log.failure.message}</Notice></div>{/if}
  {#if !data.entries.length}
    <p class="text-muted-foreground" role="status">Keine Befehle für diese Filter.</p>
  {:else}
    <div class="overflow-x-auto rounded-xl border">
      <table class="w-full min-w-[44rem] border-collapse text-sm" data-testid="commands-table">
        <caption class="sr-only">Schaltprotokoll, Seite {data.page}</caption>
        <thead>
          <tr class="bg-muted/50 text-left">
            <th scope="col" class="px-3 py-2 font-medium">Zeit</th>
            <th scope="col" class="px-3 py-2 font-medium">Raum</th>
            <th scope="col" class="px-3 py-2 font-medium">Gerät</th>
            <th scope="col" class="px-3 py-2 font-medium">Befehl</th>
            <th scope="col" class="px-3 py-2 font-medium">Quelle</th>
            <th scope="col" class="px-3 py-2 font-medium">Ergebnis</th>
          </tr>
        </thead>
        <tbody>
          {#each data.entries as e (e.id)}
            <tr class="border-t align-top">
              <td class="px-3 py-2 whitespace-nowrap">{formatInstant(e.sent_at, data.timezone ?? 'UTC')}</td>
              <td class="px-3 py-2">{e.zone_name ?? '—'}</td>
              <td class="px-3 py-2">{e.device_name ?? '—'}</td>
              <td class="px-3 py-2">{e.command ?? '—'}{#if e.payload}<span class="block text-xs text-muted-foreground">{e.payload}</span>{/if}</td>
              <td class="px-3 py-2">{e.source ? (data.source_labels[e.source] ?? e.source) : '—'}</td>
              <td class="px-3 py-2">
                {e.outcome ? (data.outcome_labels[e.outcome] ?? e.outcome) : '—'}{#if e.simulated} <span class="rounded-md bg-muted px-1.5 py-0.5 text-xs">Trockenlauf</span>{/if}
                {#if e.error}<span class="block text-xs text-destructive">{e.error}</span>{/if}
                {#if e.reason}<span class="block text-xs text-muted-foreground">{e.reason}</span>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="mt-3 flex items-center gap-2">
      <Button variant="outline" size="sm" disabled={page <= 1} onclick={() => (page -= 1)}>Neuere</Button>
      <span class="text-sm text-muted-foreground">Seite {data.page}</span>
      <Button variant="outline" size="sm" disabled={!data.has_more} onclick={() => (page += 1)}>Ältere</Button>
    </div>
  {/if}
{/if}
