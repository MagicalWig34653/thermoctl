<script lang="ts">
  import { actuationHeadline, actuationRows } from '$lib/rooms/logic';
  import type { Actuation } from '$lib/rooms/types';
  import HelpTip from './HelpTip.svelte';

  let { actuation }: { actuation: Actuation } = $props();
  const rows = $derived(actuationRows(actuation));
  const headline = $derived(actuationHeadline(actuation));
  const dot = { ok: 'bg-emerald-600', warn: 'bg-amber-500', neutral: 'bg-muted-foreground' };
</script>

<section class="rounded-xl border bg-card p-4" aria-labelledby="actuation-title" data-testid="actuation">
  <h2 id="actuation-title" class="mb-1 text-base">Anlagenstatus</h2>
  <p class="mb-3 text-sm {headline.tone === 'warn' ? 'font-medium' : 'text-muted-foreground'}">{headline.text}</p>
  <dl class="m-0 grid gap-2 sm:grid-cols-3">
    {#each rows as row (row.id)}
      <div class="rounded-lg bg-muted/60 px-3 py-2" data-testid={`actuation-${row.id}`}>
        <dt class="flex items-center gap-1 text-xs text-muted-foreground">
          {row.title}<HelpTip term={row.help} />
        </dt>
        <dd class="m-0 flex items-center gap-2 text-sm font-medium">
          <span class="size-2 shrink-0 rounded-full {dot[row.tone]}" aria-hidden="true"></span>{row.value}
        </dd>
      </div>
    {/each}
  </dl>
</section>
