<script lang="ts">
  import { appHref } from '$lib/paths';
  import { useApp } from '$lib/context';

  let { title = 'Seite nicht gefunden' }: { title?: string } = $props();
  const { prefix } = useApp();
</script>

<header class="mb-6">
  <h1>{title}</h1>
</header>

<section class="rounded-xl border bg-card p-5 sm:p-6">
  <p class="text-muted-foreground">
    Diese Seite gibt es nicht, oder Sie dürfen sie nicht öffnen.
  </p>
  <a
    class="inline-flex min-h-10 items-center rounded-md border px-3 py-2 text-sm hover:bg-muted"
    href={appHref(prefix, '/')}>Zur Übersicht</a
  >
</section>
