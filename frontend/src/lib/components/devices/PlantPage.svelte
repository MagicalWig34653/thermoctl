<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { edgeSentence, plantDiagram } from '$lib/devices/diagram';
  import { createQuery } from '$lib/history/query.svelte';
  import FlowDiagram from './FlowDiagram.svelte';

  // Anlagenbild: welches Gerät in welchem Raum was tut (`/ui-api/plant`). Ob die Person die
  // Seite sehen darf, entscheidet der Server; ein Verbot wird als Hinweis angezeigt.
  const { api, prefix } = useApp();
  const client = createDevicesClient(api);
  const query = createQuery(() => client.plant());
  let mode = $state<'diagramm' | 'liste'>('diagramm');
  onMount(() => {
    query.load();
    return () => query.cancel();
  });

  const bridge = $derived(query.data?.bridge);
  const bridgeText = $derived(bridge === true ? 'Die Brücke meldet sich.' : bridge === false ? 'Die Brücke meldet sich nicht.' : 'Von der Brücke gibt es noch keine Meldung.');
</script>

<header class="mb-6">
  <h1>Anlagenbild</h1>
  <p class="text-muted-foreground">
    Welches Gerät welchem Raum zugeordnet ist. Ein Fühler liefert Messwerte, der Raum entscheidet, der Aktor führt aus.
  </p>
</header>

{#if query.failure && !query.data}
  <Notice tone="error">{query.failure.message}</Notice>
{:else if !query.data}
  <p class="text-muted-foreground" role="status">Wird geladen …</p>
{:else}
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <span
      class="inline-flex min-h-9 items-center rounded-full border px-3 text-sm font-medium {bridge === false ? 'border-destructive text-destructive' : ''}"
      data-testid="bridge"
    >{bridgeText}</span>
    <p class="m-0 text-sm text-muted-foreground max-sm:basis-full">
      Alle Geräte hängen an der Zigbee2MQTT-Brücke. Meldet sie sich nicht, kommt von keinem etwas an.
    </p>
  </div>

  <div class="mb-4 flex flex-wrap gap-1" role="group" aria-label="Darstellung">
    <Button variant={mode === 'diagramm' ? 'secondary' : 'outline'} aria-pressed={mode === 'diagramm'} onclick={() => (mode = 'diagramm')}>Diagramm</Button>
    <Button variant={mode === 'liste' ? 'secondary' : 'outline'} aria-pressed={mode === 'liste'} onclick={() => (mode = 'liste')}>Liste</Button>
    <Button variant="ghost" disabled={query.loading} onclick={() => query.load()}>Neu laden</Button>
  </div>
  {#if query.failure}<div class="mb-3"><Notice tone="error">{query.failure.message}</Notice></div>{/if}

  {#if !query.data.zones.length}
    <p class="text-muted-foreground">Noch keine Räume angelegt.</p>
  {/if}
  <div class="grid gap-4">
    {#each query.data.zones as z (z.zone_id)}
      {@const diagram = plantDiagram(z)}
      <article class="min-w-0 rounded-xl border bg-card p-4" aria-labelledby={`plant-${z.zone_id}`} data-testid="plant-zone">
        <div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={`plant-${z.zone_id}`} class="m-0 text-lg">{z.name}</h2>
          <a class="inline-flex min-h-10 items-center text-sm underline underline-offset-2" href={appHref(prefix, '/geraete/liste')}>Zuordnung ändern</a>
        </div>
        {#if mode === 'diagramm'}
          <FlowDiagram {diagram} title={z.name} />
        {:else}
          <ul class="m-0 grid list-none gap-1 p-0 text-sm" aria-label={`Verbindungen von ${z.name}`}>
            {#each diagram.edges as e, i (i)}
              <li class={e.dashed ? 'italic text-muted-foreground' : ''}>{edgeSentence(diagram, e)}</li>
            {/each}
            {#each diagram.nodes.filter((n) => !diagram.edges.some((e) => e.source === n.id || e.target === n.id)) as n (n.id)}
              <li>{n.label}{n.detail ? ` · ${n.detail}` : ''}: zeigt an, steuert nichts</li>
            {/each}
          </ul>
        {/if}
        {#if z.defects.length}
          <ul class="m-0 mt-3 list-disc pl-5 text-sm text-muted-foreground">{#each z.defects as d, i (i)}<li>{d}</li>{/each}</ul>
        {/if}
      </article>
    {/each}

    {#if query.data.without_zone.length}
      <section class="rounded-xl border bg-card p-4" aria-labelledby="plant-free">
        <h2 id="plant-free" class="m-0 text-lg">Ohne Raum</h2>
        <p class="mt-1 text-sm text-muted-foreground">
          Diese Geräte melden sich, aber kein Raum benutzt sie; die Regelung sieht sie nicht. Das ist der häufigste Grund, warum ein neuer Fühler nicht ankommt.
        </p>
        <ul class="m-0 flex list-none flex-wrap gap-2 p-0">
          {#each query.data.without_zone as g (g.id)}
            <li class="rounded-lg border bg-muted px-2.5 py-1.5 text-sm">
              <span class="font-medium">{g.name}</span>{#if g.capabilities.length}<span class="text-muted-foreground"> · {g.capabilities.join(', ')}</span>{/if}{#if !g.active}<span class="text-muted-foreground"> · abgeschaltet</span>{/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  </div>
{/if}
