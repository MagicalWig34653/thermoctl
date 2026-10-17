<script lang="ts">
  import * as Tabs from '$lib/components/ui/tabs';
  import type { VisiblePage } from '$lib/navigation';
  import GroupsPanel from './GroupsPanel.svelte';
  import OverviewsPanel from './OverviewsPanel.svelte';
  import UsersPanel from './UsersPanel.svelte';

  // Welche Reiter es gibt, richtet sich nach den Pfaden, die der Server für diese Person geliefert
  // hat (Darstellung). Jeder Endpunkt prüft das Recht erneut.
  let { page }: { page: VisiblePage } = $props();
  const hasUsers = $derived(page.links.some((l) => l.path === '/users'));
  const hasGroups = $derived(page.links.some((l) => l.path === '/groups'));
  let tab = $state<string | null>(null);
  const current = $derived(tab ?? (hasUsers ? 'users' : 'groups'));
</script>

<header class="mb-6">
  <h1>{page.label}</h1>
  <p class="text-muted-foreground">Wer sich anmelden darf und was die Gruppen dürfen.</p>
</header>

{#if hasUsers && hasGroups}
  <Tabs.Root value={current} onValueChange={(v) => (tab = v)}>
    <Tabs.List class="mb-5 h-11 max-w-lg">
      <Tabs.Trigger value="users">Benutzer</Tabs.Trigger>
      <Tabs.Trigger value="groups">Gruppen und Rechte</Tabs.Trigger>
      <Tabs.Trigger value="overviews">Übersichten</Tabs.Trigger>
    </Tabs.List>
    <Tabs.Content value="users"><UsersPanel /></Tabs.Content>
    <Tabs.Content value="groups"><GroupsPanel /></Tabs.Content>
    <Tabs.Content value="overviews"><OverviewsPanel /></Tabs.Content>
  </Tabs.Root>
{:else if hasUsers}
  <UsersPanel />
{:else}
  <GroupsPanel />
{/if}
