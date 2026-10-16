<script lang="ts">
  import type { Snippet } from 'svelte';
  import OpsHelp from './OpsHelp.svelte';

  // Formularkarte: eine Spalte, höchstens etwa 820 px; Felder füllen die Kartenbreite.
  let {
    id,
    title,
    description,
    help,
    wide = false,
    children,
  }: { id: string; title: string; description?: string; help?: string; wide?: boolean; children: Snippet } = $props();
</script>

<section class="rounded-xl border bg-card p-5 {wide ? 'max-w-[1100px]' : 'max-w-[820px]'}" aria-labelledby={`${id}-title`} data-testid={id}>
  <h2 id={`${id}-title`} class="mb-1 flex items-center gap-1">{title}{#if help}<OpsHelp term={help} />{/if}</h2>
  {#if description}<p class="mb-4 text-sm text-muted-foreground">{description}</p>{:else}<div class="mb-3"></div>{/if}
  <div class="space-y-4">{@render children()}</div>
</section>
