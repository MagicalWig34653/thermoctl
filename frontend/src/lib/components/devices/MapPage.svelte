<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import DataFlowPanel from '$lib/components/devices/DataFlowPanel.svelte';
  import DeviceMapCanvas from '$lib/components/devices/DeviceMapCanvas.svelte';
  import MapList from '$lib/components/devices/MapList.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { createMapActions } from '$lib/devices/map-actions.svelte';
  import { createQuery } from '$lib/history/query.svelte';
  import { choiceFromSearch } from '$lib/preselect';

  const { api, router } = useApp();
  const client = createDevicesClient(api);
  const query = createQuery(() => client.map());
  type Tab = 'karte' | 'liste' | 'datenfluss';
  const TABS = ['karte', 'liste', 'datenfluss'] as const;
  let tab = $state<Tab>(choiceFromSearch(router.search, 'tab', TABS) ?? 'karte');
  $effect(() => {
    const next = choiceFromSearch(router.search, 'tab', TABS);
    if (next !== null) tab = next;
  });

  const actions = createMapActions({
    client,
    map: () => query.data!,
    reload: () => query.load(),
  });

  onMount(() => {
    query.load();
    return () => query.cancel();
  });

  const tabs: { id: Tab; label: string }[] = [
    { id: 'karte', label: 'Karte' },
    { id: 'liste', label: 'Liste' },
    { id: 'datenfluss', label: 'Datenfluss' },
  ];
</script>

<header class="mb-6">
  <h1>Gerätekarte</h1>
  <p class="text-muted-foreground max-sm:hidden">Welche Geräte zu welchem Raum gehören: Ausgänge liefern Werte, Eingänge nehmen sie an. Verbinden, trennen und die Betriebsart der Ventile ablesen.</p>
</header>

<div class="mb-4 flex flex-wrap gap-1" role="group" aria-label="Darstellung">
  {#each tabs as t (t.id)}
    <Button variant={tab === t.id ? 'secondary' : 'outline'} aria-pressed={tab === t.id} onclick={() => (tab = t.id)}>{t.label}</Button>
  {/each}
  <Button variant="ghost" disabled={query.loading} onclick={() => query.load()}>Neu laden</Button>
</div>

{#if tab === 'datenfluss'}
  <DataFlowPanel />
{:else if query.failure && !query.data}
  <Notice tone="error">{query.failure.message}</Notice>
{:else if !query.data}
  <p class="text-muted-foreground" role="status">Wird geladen …</p>
{:else}
  {#if query.failure}<div class="mb-3"><Notice tone="error">{query.failure.message}</Notice></div>{/if}
  {#if tab === 'karte'}
    <DeviceMapCanvas map={query.data} {actions} />
  {:else}
    <MapList map={query.data} {actions} />
  {/if}
{/if}
