<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { Eye } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import DashboardView from '$lib/components/dashboard/DashboardView.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createResource } from '$lib/access/resource.svelte';
  import { navigationTree, permissionLines } from '$lib/access/preview';
  import { profileLabel } from '$lib/access/matrix';
  import type { UserPreview, UserRow } from '$lib/access/types';
  import type { DashboardTarget } from '$lib/dashboard/editor.svelte';

  // Schreibgeschützt: es entsteht keine Sitzung, nichts wird als diese Person ausgeführt.
  let { user }: { user: UserRow } = $props();
  const { api } = useApp();
  const client = createAccessClient(api);
  const preview = createResource<UserPreview>(() => client.userPreview(user.id));
  onMount(() => void preview.load());
  onDestroy(() => preview.stop());

  const name = $derived(user.display_name || user.username);
  const tree = $derived(preview.data ? navigationTree(preview.data.navigation) : []);
  const rights = $derived(preview.data ? permissionLines(preview.data.permissions) : []);

  // Die Übersicht kommt aus der Vorschau-Antwort; Speichern gibt es hier nicht.
  const target = $derived<DashboardTarget | null>(
    preview.data
      ? {
          load: async () => preview.data!.dashboard,
          save: () => Promise.reject(new Error('Die Vorschau ist schreibgeschützt.')),
        }
      : null,
  );
</script>

<div class="grid gap-5" data-testid="user-preview">
  <div class="rounded-lg border border-dashed bg-muted/40 p-3" role="note" data-testid="preview-banner">
    <p class="m-0 flex items-center gap-2 text-sm font-semibold"><Eye size={16} aria-hidden="true" />Vorschau – nur Ansicht</p>
    <p class="m-0 text-sm text-muted-foreground">
      So sieht die Oberfläche für {name} aus. Du meldest dich nicht als diese Person an und kannst hier nichts verändern.
    </p>
  </div>

  {#if preview.forbidden && !preview.data}
    <Notice tone="warn">Für die Vorschau fehlt dir die Berechtigung.</Notice>
  {:else if preview.error && !preview.data}
    <Notice tone="error">{preview.error}</Notice>
  {:else if !preview.data}
    <p class="text-muted-foreground" role="status">Vorschau wird geladen …</p>
  {:else}
    <p class="m-0 text-sm text-muted-foreground">
      Gruppenprofil: {profileLabel(preview.data.ui_profile)}. Es bestimmt nur die Startseite, nicht was sichtbar ist – das entscheiden allein die Rechte.
      {#if !preview.data.is_active}Das Konto ist gesperrt und kann sich nicht anmelden.{/if}
    </p>

    <section aria-labelledby="preview-nav" class="grid gap-2">
      <h3 id="preview-nav" class="m-0 text-base">Navigation</h3>
      <ul class="m-0 grid list-none gap-1 p-0" data-testid="preview-nav">
        {#each tree as node (node.label)}
          <li class="text-sm">
            <span class="font-medium" data-testid="preview-nav-area">{node.label}</span>
            {#if node.children.length > 0}
              <ul class="m-0 mt-1 list-disc pl-6 text-muted-foreground">
                {#each node.children as child (child)}<li data-testid="preview-nav-page">{child}</li>{/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    </section>

    <section aria-labelledby="preview-rights" class="grid gap-2">
      <h3 id="preview-rights" class="m-0 text-base">Rechte</h3>
      {#if rights.length === 0}
        <p class="m-0 text-sm text-muted-foreground" data-testid="preview-no-rights">Dieses Konto hat keinerlei Rechte.</p>
      {:else}
        <ul class="m-0 list-disc pl-6 text-sm" data-testid="preview-rights">
          {#each rights as line (line)}<li>{line}</li>{/each}
        </ul>
      {/if}
    </section>

    <section aria-labelledby="preview-dash" class="grid gap-2">
      <h3 id="preview-dash" class="m-0 text-base">Übersicht</h3>
      {#if target}
        {#key user.id}
          <div data-testid="preview-dashboard">
            <DashboardView {target} readonly heading="Übersicht von {name}" lead="Es erscheinen nur Räume und Kacheln, die diese Person sehen darf." />
          </div>
        {/key}
      {/if}
    </section>
  {/if}
</div>
