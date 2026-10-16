<script lang="ts">
  import { stateCards, type Tone } from '$lib/operations/logic';
  import type { OperationActuation } from '$lib/operations/types';
  import OpsHelp from './OpsHelp.svelte';

  // Vier getrennte Tatsachen nebeneinander, je mit eigener Beschriftung und Hilfe. Nichts wird
  // zu einem gemeinsamen „scharf“ zusammengezogen.
  let { actuation }: { actuation: OperationActuation } = $props();
  const cards = $derived(stateCards(actuation));
  const dot: Record<Tone, string> = { ok: 'bg-emerald-600', warn: 'bg-amber-500', neutral: 'bg-muted-foreground' };
</script>

<ul class="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 xl:grid-cols-4" aria-label="Zustände der Anlage" data-testid="state-cards">
  {#each cards as card (card.id)}
    <li class="rounded-xl border bg-card p-4" data-testid={`state-${card.id}`}>
      <h3 class="flex items-center gap-1 text-sm text-muted-foreground">{card.title}<OpsHelp term={card.help} /></h3>
      <p class="mt-1 mb-2 flex items-center gap-2 text-xl font-semibold">
        <span class="size-2.5 shrink-0 rounded-full {dot[card.tone]}" aria-hidden="true"></span>
        <span data-testid={`state-${card.id}-value`}>{card.value}</span>
      </p>
      <p class="mb-0 text-sm text-muted-foreground">{card.detail}</p>
    </li>
  {/each}
</ul>
