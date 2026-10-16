<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { Search } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { toFailure, type Failure } from '$lib/rooms/logic';
  import { EXPLANATIONS, GLOSSARY_HINT } from '$lib/help/explanations';
  import { createZonesClient } from '$lib/zones/client';
  import { filterGlossary, groupByLetter, termOf } from '$lib/zones/logic';
  import type { Glossary } from '$lib/zones/types';

  // Glossar und Hilfe: dieselben Begriffe wie die alte Seite `/glossar`, aus derselben Quelle.
  const { api } = useApp();
  const client = createZonesClient(api);

  let glossary = $state<Glossary | null>(null);
  let failure = $state<Failure | null>(null);
  let loading = $state(true);
  let query = $state('');
  let area = $state<string | null>(null);

  async function load() {
    loading = true;
    failure = null;
    try {
      glossary = await client.glossary();
    } catch (error) {
      failure = toFailure(error);
    } finally {
      loading = false;
    }
  }
  onMount(() => void load());

  const shown = $derived(glossary ? filterGlossary(glossary.entries, query, area) : []);
  const groups = $derived(groupByLetter(shown));
  const present = $derived(new Set(groups.map((g) => g.letter)));

  async function jump(id: string) {
    // Ein Verweis auf einen ausgefilterten Begriff hebt den Filter auf, sonst läuft er ins Leere.
    if (!shown.some((entry) => entry.id === id)) {
      query = '';
      area = null;
      await tick();
    }
    const target = document.getElementById(`term-${id}`);
    target?.scrollIntoView({ block: 'start' });
    target?.focus();
  }
  async function jumpLetter(letter: string) {
    const target = document.getElementById(`letter-${letter}`);
    target?.scrollIntoView({ block: 'start' });
    target?.focus();
  }
  const reset = () => {
    query = '';
    area = null;
  };
</script>

<header class="mb-6">
  <h1>Glossar & Hilfe</h1>
  <p class="text-muted-foreground">Was die Anzeigen bedeuten und die Fachbegriffe der Oberfläche in einfachen Worten.</p>
</header>

<section class="mb-10 max-w-3xl" aria-labelledby="explain-title" data-testid="help-explanations">
  <h2 id="explain-title" class="mb-3 text-xl">Was bedeuten die Anzeigen?</h2>
  <ul class="m-0 grid list-none gap-3 p-0">
    {#each EXPLANATIONS as item (item.id)}
      <li>
        <article class="rounded-xl border bg-card p-4">
          <h3 class="m-0 mb-1 text-base">{item.title}</h3>
          <p class="m-0 text-sm">{item.text}</p>
        </article>
      </li>
    {/each}
  </ul>
  <p class="mt-3 mb-0 text-sm text-muted-foreground">{GLOSSARY_HINT}</p>
</section>

<h2 class="mb-3 text-xl">Glossar</h2>

{#if loading && !glossary}
  <p class="text-muted-foreground" role="status">Glossar wird geladen …</p>
{:else if !glossary}
  <div class="max-w-xl space-y-3">
    <Notice tone="error">{failure?.message ?? 'Das Glossar konnte nicht geladen werden.'}</Notice>
    <Button variant="outline" onclick={load}>Erneut versuchen</Button>
  </div>
{:else}
  <div class="mb-4 grid max-w-3xl gap-3">
    <div>
      <label class="mb-1 block text-sm font-medium" for="glossary-search">Suchen</label>
      <div class="relative">
        <Search size={16} class="pointer-events-none absolute top-3 left-3 text-muted-foreground" aria-hidden="true" />
        <input
          id="glossary-search"
          type="search"
          class="h-10 w-full rounded-lg border bg-background pr-3 pl-9 text-sm"
          placeholder="Begriff oder Stichwort"
          autocomplete="off"
          bind:value={query}
        />
      </div>
    </div>
    <div class="flex flex-wrap gap-2" role="group" aria-label="Bereich">
      <Button size="sm" variant={area === null ? 'secondary' : 'outline'} aria-pressed={area === null} class="min-h-9" onclick={() => (area = null)}>Alle</Button>
      {#each glossary.areas as name (name)}
        <Button size="sm" variant={area === name ? 'secondary' : 'outline'} aria-pressed={area === name} class="min-h-9" onclick={() => (area = area === name ? null : name)}>{name}</Button>
      {/each}
    </div>
    <nav aria-label="Anfangsbuchstaben" class="flex flex-wrap gap-1">
      {#each glossary.letters as letter (letter)}
        <button
          type="button"
          class="inline-grid size-9 place-items-center rounded-md border text-sm font-medium enabled:hover:bg-muted disabled:text-muted-foreground disabled:opacity-50"
          disabled={!present.has(letter)}
          aria-label={`Zu Buchstabe ${letter}`}
          onclick={() => jumpLetter(letter)}>{letter}</button
        >
      {/each}
    </nav>
    <p class="m-0 text-sm text-muted-foreground" role="status" aria-live="polite" data-testid="glossary-count">
      {shown.length === 1 ? '1 Begriff' : `${shown.length} Begriffe`}{query || area ? ' gefunden' : ''}
    </p>
  </div>

  {#if shown.length === 0}
    <div class="max-w-xl space-y-3">
      <p class="m-0">Keine Begriffe gefunden. Versuche ein kürzeres Stichwort oder entferne den Bereichsfilter.</p>
      <Button variant="outline" onclick={reset}>Filter zurücksetzen</Button>
    </div>
  {:else}
    <div class="grid max-w-3xl gap-6">
      {#each groups as group (group.letter)}
        <section aria-labelledby={`letter-${group.letter}`}>
          <h3 id={`letter-${group.letter}`} tabindex="-1" class="mb-2 text-xl">{group.letter}</h3>
          <ul class="m-0 grid list-none gap-3 p-0">
            {#each group.entries as entry (entry.id)}
              <li>
                <article id={`term-${entry.id}`} tabindex="-1" class="rounded-xl border bg-card p-4 scroll-mt-20" data-testid="glossary-entry">
                  <div class="flex flex-wrap items-baseline justify-between gap-2">
                    <h4 class="m-0 text-base">{entry.term}</h4>
                    <span class="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium">{entry.area}</span>
                  </div>
                  <p class="mt-1 mb-2 font-medium">{entry.short}</p>
                  <p class="m-0 text-sm text-muted-foreground">{entry.explanation}</p>
                  {#if entry.synonyms.length > 0}
                    <p class="mt-2 mb-0 text-xs text-muted-foreground">Auch: {entry.synonyms.join(', ')}</p>
                  {/if}
                  {#if entry.see_also.length > 0}
                    <p class="mt-2 mb-0 flex flex-wrap items-center gap-x-2 text-sm">
                      <span class="text-muted-foreground">Siehe auch:</span>
                      {#each entry.see_also as other (other)}
                        <button type="button" class="min-h-9 rounded-md px-1 text-primary underline underline-offset-2" onclick={() => jump(other)}>{termOf(glossary.entries, other)}</button>
                      {/each}
                    </p>
                  {/if}
                </article>
              </li>
            {/each}
          </ul>
        </section>
      {/each}
    </div>
  {/if}
{/if}
