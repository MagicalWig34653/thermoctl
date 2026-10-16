<script lang="ts">
  import { Popover } from 'bits-ui';
  import { CircleHelp } from '@lucide/svelte';
  import { OPS_TERMS } from '$lib/operations/glossary';

  let { term }: { term: string } = $props();
  const entry = $derived(OPS_TERMS[term]);
</script>

{#if entry}
  <Popover.Root>
    <Popover.Trigger
      type="button"
      aria-label={`Hilfe zu ${entry.title}`}
      class="inline-grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      <CircleHelp size={16} aria-hidden="true" />
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content
        sideOffset={6}
        collisionPadding={12}
        class="z-50 w-72 max-w-[calc(100vw-24px)] rounded-xl border bg-popover p-4 text-sm text-popover-foreground shadow-lg"
      >
        <h3 class="mb-1 text-sm">{entry.title}</h3>
        <p class="mb-3 text-muted-foreground">{entry.text}</p>
        <Popover.Close class="min-h-9 rounded-md border px-3 py-1.5 text-xs hover:bg-muted">Schließen</Popover.Close>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
{/if}
