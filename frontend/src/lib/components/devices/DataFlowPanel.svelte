<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { failureText, deviceFailure } from '$lib/devices/errors';
  import { FLOW_NOTE, flowDiagram } from '$lib/devices/diagram';
  import type { FlowZone } from '$lib/devices/types';
  import { createQuery } from '$lib/history/query.svelte';
  import { zoneFromSearch } from '$lib/preselect';
  import FlowDiagram from './FlowDiagram.svelte';

  const { api, router } = useApp();
  const client = createDevicesClient(api);
  const flow = createQuery(() => client.dataFlow());
  // Ein einzelner Raum kommt vom eigenen Endpunkt (`/zones/{id}/data-flow`), nicht aus der Gesamtliste.
  let chosen = $state<number | null>(null);
  const single = createQuery(() => client.zoneDataFlow(chosen!));
  let mode = $state<'diagramm' | 'liste'>('diagramm');
  onMount(() => {
    flow.load();
    return () => {
      flow.cancel();
      single.cancel();
    };
  });
  function choose(value: string) {
    chosen = value === '' ? null : Number(value);
    if (chosen !== null) single.load();
    else single.cancel();
  }
  // Raumvorwahl (`?zone=`): erst wenn die Liste der Räume da ist und den Raum enthält.
  let preselected = $state<number | null>(null);
  $effect(() => {
    const zones = flow.data?.zones.map((z) => z.zone) ?? [];
    const wanted = zoneFromSearch(router.search, zones);
    if (wanted !== null && wanted !== preselected) {
      preselected = wanted;
      untrack(() => choose(String(wanted)));
    }
  });
  const failure = $derived(flow.failure ? deviceFailure(flow.failure) : null);
  const shown = $derived.by((): FlowZone[] => {
    if (chosen === null) return flow.data?.zones ?? [];
    return single.data && single.data.zone.id === chosen ? [single.data] : [];
  });
  const choosing = $derived(chosen !== null && shown.length === 0 && !single.failure);
</script>

<section aria-labelledby="flow-title" class="grid gap-4">
  <div>
    <h2 id="flow-title" class="m-0">Datenfluss</h2>
    <p class="m-0 text-sm text-muted-foreground">Woher ein Raum seine Werte bekommt und wohin seine Befehle gehen. Gestrichelt: Es fehlt etwas.</p>
  </div>
  {#if flow.failure}
    <Notice tone="error">{flow.failure.message}</Notice>
  {:else if !flow.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else if !flow.data.zones.length}
    <p class="text-muted-foreground">Keine Räume sichtbar.</p>
  {:else}
    <div class="flex flex-wrap items-end gap-3">
      <label class="grid gap-1 text-sm"><span>Raum</span>
        <select class="h-11 min-w-48 rounded-lg border bg-background px-3" value={chosen === null ? '' : String(chosen)} onchange={(e) => choose(e.currentTarget.value)}>
          <option value="">Alle Räume</option>
          {#each flow.data.zones as z (z.zone.id)}<option value={String(z.zone.id)}>{z.zone.name}</option>{/each}
        </select></label>
      <div class="flex gap-1" role="group" aria-label="Darstellung des Datenflusses">
        <Button variant={mode === 'diagramm' ? 'secondary' : 'outline'} aria-pressed={mode === 'diagramm'} onclick={() => (mode = 'diagramm')}>Diagramm</Button>
        <Button variant={mode === 'liste' ? 'secondary' : 'outline'} aria-pressed={mode === 'liste'} onclick={() => (mode = 'liste')}>Liste</Button>
      </div>
    </div>
    {#if single.failure && chosen !== null}<Notice tone="error">{single.failure.message}</Notice>{/if}
    {#if choosing}<p class="text-muted-foreground" role="status">Wird geladen …</p>{/if}
    {#each shown as z (z.zone.id)}
      <article class="min-w-0 rounded-xl border bg-card p-4" data-testid="flow-zone">
        <h3 class="m-0 text-base">{z.zone.name}</h3>
        {#if z.without_devices}
          <p class="m-0 mt-1 text-sm text-muted-foreground">Diesem Raum ist noch kein Gerät zugeordnet.</p>
        {/if}
        {#if mode === 'diagramm' && z.nodes.length}
          <div class="mt-3"><FlowDiagram diagram={flowDiagram(z)} title={z.zone.name} /></div>
        {:else}
          {#if z.nodes.length}
            <ul class="m-0 mt-2 flex list-none flex-wrap gap-2 p-0" aria-label="Beteiligte">
              {#each z.nodes as n (n.id)}
                <li class="rounded-lg border bg-muted px-2 py-1 text-sm">
                  <span class="font-medium">{n.label}</span>{#if n.detail}<span class="text-muted-foreground"> · {n.detail}</span>{/if}
                </li>
              {/each}
            </ul>
          {/if}
          {#if z.edges.length}
            <ul class="m-0 mt-3 grid list-none gap-1 p-0 text-sm" aria-label="Verbindungen">
              {#each z.edges as e, i (i)}
                <li class={e.dashed ? 'italic text-muted-foreground' : ''}>{e.text}</li>
              {/each}
            </ul>
          {/if}
        {/if}
        <p class="m-0 mt-2 text-sm text-muted-foreground" data-testid="flow-note">{FLOW_NOTE}</p>
      </article>
    {/each}
  {/if}
  <span class="sr-only">{failure ? failureText(failure) : ''}</span>
</section>
