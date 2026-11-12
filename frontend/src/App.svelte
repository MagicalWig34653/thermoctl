<script lang="ts">
  import { onMount } from 'svelte';
  import { createApi, runtimePrefix } from '$lib/api';
  import { createProber } from '$lib/connection';
  import { createConnection } from '$lib/connection.svelte';
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
  import HomeView from '$lib/components/rooms/HomeView.svelte';
  import RoomView from '$lib/components/rooms/RoomView.svelte';
  import { Button } from '$lib/components/ui/button';

  // Basispfad zur Laufzeit aus der Seite, nicht zur Build-Zeit.
  const prefix = runtimePrefix();
  const router = createRouter(prefix);
  const appearance = createAppearance();
  const connection = createConnection();
  const api = createApi({
    fetch: window.fetch.bind(window),
    prefix,
    cookies: () => document.cookie,
    onUnauthorized: (reason) => session.expire(reason),
    onReachability: (reachable) => connection.report(reachable),
    // Gesperrt wird nur in der angemeldeten Ansicht; Start und Anmeldung bleiben bedienbar.
    isOffline: () => connection.down && session.status === 'ready',
  });
  const session = createSession(api);
  provideApp({ prefix, api, router, session, appearance, connection });

  onMount(() => {
    void session.load();
  });

  // Solange die Verbindung unterbrochen ist, selbst alle ~10 s lesen: Auch eine Seite ohne eigene
  // Abfrage soll nicht gesperrt stehen bleiben. Der Abruf meldet sein Ergebnis über `api`.
  $effect(() => {
    if (!connection.down || session.status !== 'ready') return;
    const prober = createProber({ probe: () => api.get('/ui-api/bootstrap') });
    prober.start();
    return () => prober.stop();
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
      (route.kind === 'room'
        ? 'Raum'
        : route.kind === 'account'
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
  <main class="mx-auto flex min-h-dvh max-w-md flex-col justify-center p-4 sm:p-6" data-testid="session-error">
    <div class="rounded-xl border bg-card p-5 shadow-sm sm:p-6">
      <h1 class="mb-2">Keine Verbindung</h1>
      <p class="mb-1 text-destructive" role="alert">{session.failure}</p>
      <p class="mb-4 text-sm text-muted-foreground">
        Prüfe die Netzwerkverbindung und versuche es gleich noch einmal. Es wurde nichts geändert.
      </p>
      <Button onclick={() => session.load()}>Erneut versuchen</Button>
    </div>
  </main>
{:else if session.status === 'anonymous'}
  <LoginView />
{:else}
  <Shell {areas} {route} {title}>
    {#if route.kind === 'home'}
      <HomeView />
    {:else if route.kind === 'room'}
      {#key route.roomId}<RoomView id={route.roomId} />{/key}
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
        text="Diese Seite ist für dein Konto nicht freigegeben. Brauchst du sie, frage die Person, die die Anlage verwaltet, nach dem passenden Recht."
      />
    {:else if route.kind === 'login'}
      <MessageView title="Bereits angemeldet" text="Du bist angemeldet." />
    {:else}
      <MessageView title="Seite nicht gefunden" text="Diese Adresse gibt es nicht. Prüfe sie auf Tippfehler oder öffne die Seite über das Menü." />
    {/if}
  </Shell>
{/if}
