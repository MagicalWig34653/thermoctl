<script lang="ts">
  import { onMount } from 'svelte';
  import { createApi, runtimePrefix } from '$lib/api';
  import { createAppearance } from '$lib/appearance.svelte';
  import { provideApp } from '$lib/context';
  import { buildVisibleAreas } from '$lib/navigation';
  import { resolveRoute } from '$lib/paths';
  import { createRouter } from '$lib/router.svelte';
  import { createSession } from '$lib/session.svelte';
  import Shell from '$lib/components/shell/Shell.svelte';
  import AccountView from '$lib/views/AccountView.svelte';
  import AreaView from '$lib/views/AreaView.svelte';
  import HelpView from '$lib/views/HelpView.svelte';
  import LoginView from '$lib/views/LoginView.svelte';
  import MessageView from '$lib/views/MessageView.svelte';
  import PageView from '$lib/views/PageView.svelte';
  import { Button } from '$lib/components/ui/button';

  // Basispfad zur Laufzeit aus der Seite, nicht zur Build-Zeit.
  const prefix = runtimePrefix();
  const router = createRouter(prefix);
  const appearance = createAppearance();
  const api = createApi({
    fetch: window.fetch.bind(window),
    prefix,
    cookies: () => document.cookie,
    onUnauthorized: (reason) => session.expire(reason),
  });
  const session = createSession(api);
  provideApp({ prefix, router, session, appearance });

  onMount(() => {
    void session.load();
  });

  const areas = $derived(buildVisibleAreas(session.bootstrap?.navigation ?? []));
  const route = $derived(resolveRoute(router.path, areas));
  const area = $derived(
    route.kind === 'area' || route.kind === 'page' ? areas.find((a) => a.id === route.areaId) : undefined,
  );
  const page = $derived(
    route.kind === 'page' ? area?.pages.find((p) => p.id === route.pageId) : undefined,
  );
  const title = $derived(
    page?.label ??
      area?.label ??
      (route.kind === 'account'
        ? 'Konto & Sicherheit'
        : route.kind === 'help'
          ? 'Glossar & Hilfe'
          : route.kind === 'notfound'
            ? 'Nicht gefunden'
            : route.kind === 'unavailable'
              ? 'Nicht verfügbar'
              : 'Übersicht'),
  );
</script>

<svelte:document onclick={(e) => router.handleClick(e)} />

{#if session.status === 'loading'}
  <p class="p-6 text-muted-foreground" role="status">Lade …</p>
{:else if session.status === 'error'}
  <main class="mx-auto max-w-md p-6">
    <h1>Keine Verbindung</h1>
    <p class="text-muted-foreground" role="alert">{session.failure}</p>
    <Button onclick={() => session.load()}>Erneut versuchen</Button>
  </main>
{:else if session.status === 'anonymous'}
  <LoginView />
{:else}
  <Shell {areas} {route} {title}>
    {#if route.kind === 'home'}
      {@const home = areas.find((a) => a.id === 'uebersicht')}
      {#if home}<AreaView area={home} />{/if}
    {:else if route.kind === 'area' && area}
      <AreaView {area} />
    {:else if route.kind === 'page' && area && page}
      <PageView {area} {page} />
    {:else if route.kind === 'account'}
      <AccountView />
    {:else if route.kind === 'help'}
      <HelpView />
    {:else if route.kind === 'unavailable'}
      <MessageView
        title="Nicht verfügbar"
        text="Diese Seite ist für dein Konto nicht sichtbar. Ob etwas erlaubt ist, entscheidet der Server bei jedem Zugriff."
      />
    {:else if route.kind === 'login'}
      <MessageView title="Bereits angemeldet" text="Du bist angemeldet." />
    {:else}
      <MessageView title="Seite nicht gefunden" text="Diese Adresse gibt es in der Oberfläche nicht." />
    {/if}
  </Shell>
{/if}
