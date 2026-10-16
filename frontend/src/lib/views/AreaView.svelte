<script lang="ts">
  import { ChevronRight } from '@lucide/svelte';
  import type { VisibleArea } from '$lib/navigation';
  import { appHref } from '$lib/paths';
  import { useApp } from '$lib/context';
  import NotFound from '$lib/components/NotFound.svelte';

  let { area }: { area: VisibleArea } = $props();
  const { prefix, session } = useApp();
  const zones = $derived(area.id === 'uebersicht' ? (session.bootstrap?.zones ?? []) : []);
</script>

<header class="mb-6">
  <h1>{area.label}</h1>
  <p class="text-muted-foreground">{area.description}</p>
</header>

{#if area.pages.length}
  <ul class="m-0 mb-8 grid list-none gap-3 p-0 sm:grid-cols-2">
    {#each area.pages as page (page.id)}
      <li>
        <a
          class="flex min-h-14 items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 hover:bg-muted"
          href={appHref(prefix, `/${area.id}/${page.id}`)}
        >
          <span class="font-medium">{page.label}</span>
          <ChevronRight size={16} class="text-muted-foreground" aria-hidden="true" />
        </a>
      </li>
    {/each}
  </ul>
{/if}

{#if area.id === 'uebersicht'}
  <section class="mb-8" aria-labelledby="zones-title">
    <h2 id="zones-title">Räume</h2>
    {#if zones.length}
      <ul class="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3">
        {#each zones as zone (zone.id)}
          <li>
            <a
              class="flex min-h-14 items-center rounded-xl border bg-card px-4 py-3 hover:bg-muted"
              href={appHref(prefix, `/raeume/${zone.id}`)}>{zone.name}</a
            >
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-muted-foreground">Keine Räume sichtbar.</p>
    {/if}
  </section>
{/if}

{#if !area.pages.length && area.id !== 'uebersicht'}
  <NotFound />
{/if}
