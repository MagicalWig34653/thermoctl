<script lang="ts">
  import { CircleAlert, CircleCheck, Info } from '@lucide/svelte';
  import type { Snippet } from 'svelte';

  let {
    tone = 'info',
    children,
    id,
  }: { tone?: 'error' | 'success' | 'info' | 'warn'; children: Snippet; id?: string } = $props();
  const classes = {
    error: 'border-destructive/40 bg-destructive/10 text-destructive',
    success: 'border-border bg-muted text-foreground',
    info: 'border-border bg-muted text-foreground',
    warn: 'border-amber-500/50 bg-amber-500/10 text-foreground',
  };
</script>

<!-- Fehler werden sofort vorgelesen (alert), alles andere höflich (status). -->
<div
  {id}
  role={tone === 'error' ? 'alert' : 'status'}
  class="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm {classes[tone]}"
>
  {#if tone === 'error' || tone === 'warn'}
    <CircleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
  {:else if tone === 'success'}
    <CircleCheck size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
  {:else}
    <Info size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
  {/if}
  <div class="min-w-0">{@render children()}</div>
</div>
