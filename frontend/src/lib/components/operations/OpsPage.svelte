<script lang="ts">
  import type { Snippet } from 'svelte';

  let { title, description, children }: { title: string; description: string; children: Snippet } = $props();
</script>

<header class="mb-8">
  <h1>{title}</h1>
  <p class="mb-0 text-muted-foreground">{description}</p>
</header>
<div class="flex flex-col gap-8">{@render children()}</div>
