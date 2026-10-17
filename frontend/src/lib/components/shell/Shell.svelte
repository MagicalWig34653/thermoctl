<script lang="ts">
  import { untrack, type Snippet } from 'svelte';
  import { BookOpen, ChevronDown, ChevronRight, LogOut, Palette, PanelLeft, UserRound } from '@lucide/svelte';
  import { AREA_ICONS } from '$lib/icons';
  import type { VisibleArea } from '$lib/navigation';
  import {
    buildNavEntries,
    groupIds,
    loadOpen,
    nextFocusIndex,
    openGroups,
    saveOpen,
    toggleGroup,
  } from '$lib/nav-menu';
  import { appHref, type Route } from '$lib/paths';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import SettingsSearch from './SettingsSearch.svelte';
  import AppearanceDialog from './AppearanceDialog.svelte';
  import SourceLink from './SourceLink.svelte';

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
    route.kind === 'home' || route.kind === 'room'
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

  const entries = $derived(buildNavEntries(areas));
  // Gemerkte Auswahl pro Browser; der Bereich der aktuellen Seite ist immer offen (Direktlinks,
  // Neuladen). Ohne Speicher funktioniert alles, nur ohne Erinnerung.
  let stored = loadOpen(() => localStorage);
  let userOpen = $state<Set<string>>(new Set());
  let seeded = false;
  $effect(() => {
    // Beim ersten Lauf und bei jedem Bereichswechsel: Gruppe der aktuellen Seite aufklappen.
    const ids = groupIds(entries);
    const active = activeAreaId;
    untrack(() => {
      userOpen = openGroups(seeded ? [...userOpen] : stored, ids, active);
      seeded = true;
    });
  });
  function toggle(id: string) {
    userOpen = toggleGroup(userOpen, id);
    saveOpen(() => localStorage, userOpen);
  }

  let navEl = $state<HTMLElement | null>(null);
  function navKeydown(event: KeyboardEvent) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !navEl) return;
    const items = [...navEl.querySelectorAll<HTMLElement>('[data-nav-item]')];
    const current = items.indexOf(document.activeElement as HTMLElement);
    const target = items[nextFocusIndex(items.length, current, event.key)];
    if (!target) return;
    event.preventDefault();
    target.focus();
  }

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
    'flex min-h-10 items-center gap-2.5 rounded-md px-2.5 py-2 text-sm hover:bg-muted focus-visible:outline-offset-[-2px]';
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
    <img class="size-9" src={`${prefix}/static/favicon.svg`} alt="" width="36" height="36" />
    <strong class="text-[15px] tracking-tight">thermoctl</strong>
  </a>

  <SettingsSearch {areas} bind:query bind:input={searchInput} onpick={() => (drawer = false)} />

  {#if !query.trim()}
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <nav
      class="min-h-0 flex-1 overflow-auto"
      aria-label="Hauptnavigation"
      bind:this={navEl}
      onkeydown={navKeydown}
    >
      {#each ['Zuhause', 'Verwaltung'] as group (group)}
        {@const groupEntries = entries.filter((e) => e.area.group === group)}
        {#if groupEntries.length}
          <div class="px-2.5 pt-4 pb-1.5 text-xs font-medium text-muted-foreground">{group}</div>
          {#each groupEntries as entry (entry.area.id)}
            {@const area = entry.area}
            {@const Icon = AREA_ICONS[area.id]}
            {@const active = activeAreaId === area.id}
            {#if entry.kind === 'link'}
              <a
                data-nav-item
                class="{link} {active ? 'bg-secondary font-medium' : ''}"
                href={appHref(prefix, entry.to)}
                aria-current={active ? 'page' : undefined}
                title={area.description}
              >
                <Icon size={18} class="shrink-0 text-muted-foreground" aria-hidden="true" />
                <span>{area.label}</span>
              </a>
            {:else}
              {@const open = userOpen.has(area.id)}
              <button
                type="button"
                data-nav-item
                class="{link} w-full text-left {active ? 'font-medium' : ''}"
                aria-expanded={open}
                aria-controls="nav-sub-{area.id}"
                title={area.description}
                onclick={() => toggle(area.id)}
              >
                <Icon size={18} class="shrink-0 text-muted-foreground" aria-hidden="true" />
                <span class="flex-1">{area.label}</span>
                <ChevronDown
                  size={16}
                  class="shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none {open
                    ? 'rotate-180'
                    : ''}"
                  aria-hidden="true"
                />
              </button>
              <ul id="nav-sub-{area.id}" class="ml-[22px] border-l pl-2" hidden={!open}>
                {#each entry.pages as { page, to } (page.id)}
                  {@const here =
                    route.kind === 'page' && route.areaId === area.id && route.pageId === page.id}
                  <li>
                    <a
                      data-nav-item={open ? '' : undefined}
                      tabindex={open ? undefined : -1}
                      class="{link} {here ? 'bg-secondary font-medium' : ''}"
                      href={appHref(prefix, to)}
                      aria-current={here ? 'page' : undefined}
                    >
                      {page.label}
                    </a>
                  </li>
                {/each}
              </ul>
            {/if}
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
  <footer class="mx-auto w-full max-w-6xl px-4 pb-6 text-xs text-muted-foreground sm:px-7">
    thermoctl steht unter der AGPL-3.0. <SourceLink />
  </footer>
</div>

<AppearanceDialog bind:open={appearanceOpen} />
