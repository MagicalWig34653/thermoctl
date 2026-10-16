<script lang="ts">
  import { ExternalLink } from '@lucide/svelte';
  import type { VisibleLink } from '$lib/navigation';
  import { appHref, legacyHref } from '$lib/paths';
  import { appPathOf } from '$lib/navigation';
  import { useApp } from '$lib/context';

  let {
    title,
    description = '',
    links,
  }: { title: string; description?: string; links: VisibleLink[] } = $props();
  const { prefix } = useApp();
  // Ein Ziel, das die Oberfläche kennt, bleibt in ihr; nur ein unbekannter Server-Pfad geht
  // als Vollseitenladen zur Serverseite (die ihrerseits umleitet).
  const hrefOf = (path: string) => {
    const inApp = appPathOf(path);
    return inApp === null ? legacyHref(prefix, path) : appHref(prefix, inApp);
  };
</script>

<header class="mb-6">
  <h1>{title}</h1>
  {#if description}<p class="text-muted-foreground">{description}</p>{/if}
</header>

<section class="rounded-xl border bg-card p-5 sm:p-6" aria-labelledby="placeholder-title">
  <h2 id="placeholder-title" class="text-lg">Noch nicht in der neuen Oberfläche</h2>
  <p class="text-muted-foreground">
    Diese Ansicht ist noch nicht migriert. Die bisherige Seite funktioniert unverändert.
  </p>
  <ul class="m-0 flex list-none flex-col gap-2 p-0">
    {#each links as link (link.path)}
      <li>
        <a
          class="inline-flex min-h-10 items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"
          href={hrefOf(link.path)}
        >
          {link.label}
          <ExternalLink size={14} aria-hidden="true" />
        </a>
      </li>
    {:else}
      <li class="text-sm text-muted-foreground">Für diese Ansicht gibt es keinen Verweis.</li>
    {/each}
  </ul>
</section>
