<script lang="ts">
  import type { Snippet } from 'svelte';
  import OpsHelp from './OpsHelp.svelte';

  // Benannte Gruppe innerhalb einer Formularkarte: Überschrift mit deutlichem Abstand zu den Feldern
  // davor und danach (keine Fieldset-Legende, die auf der Linie liegt).
  let { id, title, description, help, children }: { id: string; title: string; description?: string; help?: string; children: Snippet } = $props();
</script>

<div role="group" aria-labelledby={`${id}-heading`} class="space-y-4 border-t pt-5 first:border-t-0 first:pt-0">
  <div>
    <h3 id={`${id}-heading`} class="flex items-center gap-1 text-base">{title}{#if help}<OpsHelp term={help} />{/if}</h3>
    {#if description}<p class="mt-1 mb-0 text-sm text-muted-foreground">{description}</p>{/if}
  </div>
  {@render children()}
</div>
