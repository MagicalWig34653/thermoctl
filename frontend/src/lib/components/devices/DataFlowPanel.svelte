<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createDevicesClient } from '$lib/devices/client';
  import { failureText, deviceFailure } from '$lib/devices/errors';
  import { createQuery } from '$lib/history/query.svelte';

  const { api } = useApp();
  const client = createDevicesClient(api);
  const flow = createQuery(() => client.dataFlow());
  onMount(() => {
    flow.load();
    return () => flow.cancel();
  });
  const failure = $derived(flow.failure ? deviceFailure(flow.failure) : null);
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
    {#each flow.data.zones as z (z.zone.id)}
      <article class="rounded-xl border bg-card p-4" data-testid="flow-zone">
        <h3 class="m-0 text-base">{z.zone.name}</h3>
        {#if z.without_devices}
          <p class="m-0 mt-1 text-sm text-muted-foreground">Diesem Raum ist noch kein Gerät zugeordnet.</p>
        {/if}
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
      </article>
    {/each}
  {/if}
  <span class="sr-only">{failure ? failureText(failure) : ''}</span>
</section>
