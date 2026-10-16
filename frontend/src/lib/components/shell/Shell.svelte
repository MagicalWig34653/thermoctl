<script lang="ts">
  import type { Snippet } from 'svelte';
  import { BookOpen, ChevronRight, LogOut, Palette, PanelLeft, Thermometer, UserRound } from '@lucide/svelte';
  import { AREA_ICONS } from '$lib/icons';
  import { areaPath, type VisibleArea } from '$lib/navigation';
  import { appHref, type Route } from '$lib/paths';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import SettingsSearch from './SettingsSearch.svelte';
  import AppearanceDialog from './AppearanceDialog.svelte';

  let {
    areas,
    route,
    title,
    children,
  }: { areas: VisibleArea[]; route: Route; title: string; children: Snippet } = $props();
  const { prefix, router, session } = useApp();

  let drawer = $state(false);
  let mobile = $state(false);
  let query = $state('');
  let searchInput = $state<HTMLInputElement | null>(null);
  let appearanceOpen = $state(false);
  let content = $state<HTMLElement | null>(null);
  let logoutError = $state<string | null>(null);

  const activeAreaId = $derived(
    route.kind === 'home'
      ? 'uebersicht'
      : route.kind === 'area' || route.kind === 'page' || route.kind === 'unavailable'
        ? route.areaId
        : null,
  );
  const currentArea = $derived(areas.find((a) => a.id === activeAreaId));
  const crumb = $derived(
    route.kind === 'account'
      ? 'Konto & Sicherheit'
      : route.kind === 'help'
        ? 'Glossar & Hilfe'
        : (currentArea?.label ?? ''),
  );

  $effect(() => {
    const media = window.matchMedia('(max-width: 1023px)');
    const update = () => {
      mobile = media.matches;
      if (!mobile) drawer = false;
    };
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  });

  // Nach jedem Ansichtswechsel: Schublade zu, Fokus auf den Inhalt (Tastatur und Screenreader
  // landen sonst weiter oben in der Leiste).
  let firstPath = true;
  $effect(() => {
    void router.path;
    drawer = false;
    if (firstPath) {
      firstPath = false;
      return;
    }
    content?.focus({ preventScroll: true });
  });

  $effect(() => {
    document.title = `thermoctl · ${title}`;
  });

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && drawer) drawer = false;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      drawer = true;
      queueMicrotask(() => searchInput?.focus());
    }
  }

  async function logout() {
    logoutError = await session.logout();
  }

  const link =
    'flex min-h-10 items-center gap-2.5 rounded-md px-2.5 py-2 text-sm hover:bg-muted';
</script>

<svelte:window onkeydown={keydown} />

<a
  href="#content"
  class="fixed top-[-100px] z-[100] bg-background p-3 focus:top-0"
  onclick={(e) => {
    e.preventDefault();
    content?.focus();
  }}>Zum Inhalt</a
>

<aside
  id="app-sidebar"
  class="app-sidebar fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-sidebar px-3 py-4"
  data-open={drawer}
  inert={mobile && !drawer}
>
  <a
    class="mb-3 flex items-center gap-2.5 rounded-lg border border-transparent px-2 py-2 hover:border-border hover:bg-muted"
    href={appHref(prefix, '/')}
  >
    <span class="grid size-9 place-items-center rounded-lg border bg-background">
      <Thermometer size={20} aria-hidden="true" />
    </span>
    <strong class="text-[15px] tracking-tight">thermoctl</strong>
  </a>

  <SettingsSearch {areas} bind:query bind:input={searchInput} onpick={() => (drawer = false)} />

  {#if !query.trim()}
    <nav class="min-h-0 flex-1 overflow-auto" aria-label="Hauptnavigation">
      {#each ['Zuhause', 'Verwaltung'] as group (group)}
        {@const groupAreas = areas.filter((a) => a.group === group)}
        {#if groupAreas.length}
          <div class="px-2.5 pt-4 pb-1.5 text-xs font-medium text-muted-foreground">{group}</div>
          {#each groupAreas as area (area.id)}
            {@const Icon = AREA_ICONS[area.id]}
            {@const active = activeAreaId === area.id}
            <a
              class="{link} {active ? 'bg-secondary font-medium' : ''}"
              href={appHref(prefix, areaPath(area.id))}
              aria-current={active ? 'page' : undefined}
              title={area.description}
            >
              <Icon size={18} class="shrink-0 text-muted-foreground" aria-hidden="true" />
              <span>{area.label}</span>
            </a>
          {/each}
        {/if}
      {/each}
    </nav>
  {/if}

  <div class="mt-auto border-t pt-3">
    <a
      class="{link} {route.kind === 'account' ? 'bg-secondary font-medium' : ''}"
      href={appHref(prefix, '/konto')}
      aria-current={route.kind === 'account' ? 'page' : undefined}
    >
      <UserRound size={16} aria-hidden="true" />Konto &amp; Sicherheit
    </a>
    <a
      class="{link} {route.kind === 'help' ? 'bg-secondary font-medium' : ''}"
      href={appHref(prefix, '/hilfe')}
      aria-current={route.kind === 'help' ? 'page' : undefined}
    >
      <BookOpen size={16} aria-hidden="true" />Glossar &amp; Hilfe
    </a>
    <button type="button" class="{link} w-full text-left" onclick={logout}>
      <LogOut size={16} aria-hidden="true" />Abmelden
    </button>
    {#if logoutError}<p class="px-2.5 text-xs text-destructive" role="alert">{logoutError}</p>{/if}
  </div>
</aside>

{#if drawer}
  <button
    type="button"
    class="fixed inset-x-0 top-[60px] bottom-0 z-30 bg-black/30 lg:hidden"
    aria-label="Navigation schließen"
    onclick={() => (drawer = false)}
  ></button>
{/if}

<div class="lg:ml-64">
  <header
    class="app-topbar sticky top-0 z-20 flex h-[60px] items-center justify-between gap-3 border-b px-4 sm:px-7"
  >
    <div class="flex min-w-0 items-center gap-2">
      <Button
        variant="ghost"
        size="icon"
        class="lg:hidden"
        aria-label={drawer ? 'Navigation schließen' : 'Navigation öffnen'}
        aria-expanded={drawer}
        aria-controls="app-sidebar"
        onclick={() => (drawer = !drawer)}><PanelLeft size={18} aria-hidden="true" /></Button
      >
      <nav class="flex min-w-0 items-center gap-1 text-sm text-muted-foreground" aria-label="Pfad">
        <span class="hidden sm:inline">thermoctl</span>
        {#if crumb}
          <ChevronRight size={14} class="hidden sm:inline" aria-hidden="true" />
          <span class="truncate">{crumb}</span>
        {/if}
      </nav>
    </div>
    <Button
      variant="ghost"
      size="icon"
      aria-label="Darstellung ändern"
      onclick={() => (appearanceOpen = true)}><Palette size={18} aria-hidden="true" /></Button
    >
  </header>

  <main
    id="content"
    bind:this={content}
    tabindex="-1"
    class="mx-auto w-full max-w-6xl px-4 py-6 outline-none sm:px-7 sm:py-8"
  >
    {@render children()}
  </main>
</div>

<AppearanceDialog bind:open={appearanceOpen} />
