<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { Flame } from '@lucide/svelte';

  let {
    title,
    subtitle,
    icon: Icon,
    children,
    testid,
  }: { title: string; subtitle?: string; icon?: typeof Flame; children: Snippet; testid?: string } = $props();
  const uid = $props.id();
</script>

<section class="flex h-full min-w-0 flex-col gap-3 rounded-xl border bg-card p-4" aria-labelledby={`${uid}-t`} data-testid={testid}>
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <h3 id={`${uid}-t`} class="m-0 truncate text-base" {title}>{title}</h3>
      {#if subtitle}<p class="m-0 truncate text-xs text-muted-foreground">{subtitle}</p>{/if}
    </div>
    {#if Icon}<Icon size={18} aria-hidden="true" class="mt-0.5 shrink-0 text-muted-foreground" />{/if}
  </div>
  {@render children()}
</section>
