<script lang="ts">
  import { Search } from '@lucide/svelte';
  import { searchNavigation, type VisibleArea } from '$lib/navigation';
  import { appHref } from '$lib/paths';
  import { useApp } from '$lib/context';

  let {
    areas,
    query = $bindable(''),
    input = $bindable(null),
    onpick,
  }: {
    areas: VisibleArea[];
    query: string;
    input: HTMLInputElement | null;
    onpick: () => void;
  } = $props();
  const { prefix, router } = useApp();
  const results = $derived(searchNavigation(areas, query));

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      query = '';
    } else if (event.key === 'Enter' && results.length) {
      event.preventDefault();
      router.navigate(results[0].to);
      query = '';
      onpick();
    }
  }
</script>

<div class="relative mx-1 mb-3">
  <Search
    size={16}
    class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
    aria-hidden="true"
  />
  <input
    bind:this={input}
    bind:value={query}
    onkeydown={keydown}
    type="search"
    class="h-9 w-full rounded-md border bg-transparent pr-3 pl-8 text-sm"
    aria-label="Einstellung suchen"
    placeholder="Einstellung suchen …"
    autocomplete="off"
  />
</div>

{#if query.trim()}
  <div class="min-h-0 flex-1 overflow-auto" aria-label="Suchergebnisse">
    <p class="px-2 py-1 text-xs text-muted-foreground" role="status">{results.length} Treffer</p>
    <ul class="m-0 list-none p-0">
      {#each results as result (result.to)}
        <li>
          <a
            class="flex min-h-11 flex-col justify-center rounded-md px-2 py-1 text-sm hover:bg-muted"
            href={appHref(prefix, result.to)}
            onclick={() => {
              query = '';
              onpick();
            }}
          >
            <strong class="font-medium">{result.label}</strong>
            <small class="text-muted-foreground">{result.areaLabel}</small>
          </a>
        </li>
      {:else}
        <li class="px-2 py-2 text-sm text-muted-foreground">Keine passende Einstellung gefunden.</li>
      {/each}
    </ul>
  </div>
{/if}
