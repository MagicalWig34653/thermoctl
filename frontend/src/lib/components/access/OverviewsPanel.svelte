<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { ArrowLeft, LayoutTemplate, Pencil, Plus, Trash2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import DashboardView from '$lib/components/dashboard/DashboardView.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import type { UserRow, UsersData } from '$lib/access/types';
  import { dashboardAdmin, type TemplateItem, type TemplateList } from '$lib/dashboard/admin';

  // Übersichten pflegen: Vorlagen für neue Benutzer und die Übersicht jedes Benutzers.
  // Der Editor ist derselbe wie bei der eigenen Übersicht, nur mit anderem Ziel.
  const { api } = useApp();
  const client = createAccessClient(api);
  const admin = dashboardAdmin(api);
  const users = createResource<UsersData>(() => client.users());
  const templates = createResource<TemplateList>(() => admin.templates());
  const actions = createActions(async () => {
    await templates.load();
  });
  onMount(() => {
    void users.load();
    void templates.load();
  });
  onDestroy(() => {
    users.stop();
    templates.stop();
  });

  const field = 'h-11 min-w-0 rounded-lg border bg-background px-3';

  type Editing = { kind: 'user'; user: UserRow } | { kind: 'template'; template: TemplateItem } | null;
  let editing = $state<Editing>(null);
  let templateName = $state('');

  const userTarget = $derived(editing?.kind === 'user' ? admin.userTarget(editing.user.id) : undefined);
  const templateTarget = $derived(editing?.kind === 'template' ? admin.templateTarget(editing.template.id, () => templateName) : undefined);

  function edit(next: Editing) {
    editing = next;
    templateName = next?.kind === 'template' ? next.template.name : '';
  }
  async function closeEditor() {
    editing = null;
    await templates.load();
  }

  // -- Neue Vorlage -----------------------------------------------------------------------
  let newName = $state('');
  let fromUser = $state('');
  async function createEmpty(event: SubmitEvent) {
    event.preventDefault();
    const result = await actions.run('create-template', () => admin.createTemplate(newName), 'Vorlage angelegt.');
    if (result.ok) {
      newName = '';
      await templates.load();
    }
  }
  async function createFromUser() {
    if (!fromUser) return;
    const result = await actions.run('from-user', () => admin.createTemplateFromUser(Number(fromUser), newName), 'Vorlage aus der Übersicht übernommen.');
    if (result.ok) {
      newName = '';
      fromUser = '';
      await templates.load();
    }
  }

  // -- Löschen ----------------------------------------------------------------------------
  let removeTarget = $state<TemplateItem | null>(null);
  let removeOpen = $state(false);
  async function remove() {
    const target = removeTarget;
    if (!target) return;
    const result = await actions.run('remove-template', () => admin.removeTemplate(target.id), `Vorlage „${target.name}“ gelöscht.`);
    if (result.ok) {
      removeOpen = false;
      await templates.load();
    }
  }
</script>

{#if editing}
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <Button variant="outline" onclick={closeEditor} data-testid="overview-back"><ArrowLeft size={16} aria-hidden="true" />Zurück zur Liste</Button>
    {#if editing.kind === 'template'}
      <label class="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium">Name der Vorlage
        <input class="{field} max-w-sm flex-1" bind:value={templateName} maxlength="80" data-testid="template-name" />
      </label>
    {/if}
  </div>
  {#key editing.kind === 'user' ? `u${editing.user.id}` : `t${editing.template.id}`}
    {#if editing.kind === 'user'}
      <DashboardView
        target={userTarget}
        autoEdit
        heading={`Übersicht von ${editing.user.display_name || editing.user.username}`}
        lead="So sieht die Person ihre Übersicht. Es erscheinen nur Räume und Kacheln, die sie sehen darf."
      />
    {:else}
      <DashboardView
        target={templateTarget}
        autoEdit
        heading={`Vorlage „${editing.template.name}“`}
        lead="Neue Benutzer bekommen eine Kopie. Spätere Änderungen an der Vorlage ändern bestehende Übersichten nicht."
      />
    {/if}
  {/key}
{:else}
  <div class="grid gap-8">
    <section aria-labelledby="templates-title" class="grid gap-4">
      <div>
        <h2 id="templates-title" class="m-0">Vorlagen</h2>
        <p class="m-0 text-sm text-muted-foreground">Beim Anlegen einer Person lässt sich eine Vorlage für ihre Übersicht wählen. Was die Person nicht sehen darf, erscheint auch mit Vorlage nicht.</p>
      </div>

      {#if actions.notice && ['create-template', 'from-user', 'remove-template'].includes(actions.notice.where)}
        <Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>
      {/if}

      {#if templates.forbidden && !templates.data}
        <Notice tone="warn">Für die Übersichten fehlt dir die Berechtigung.</Notice>
      {:else if templates.error && !templates.data}
        <Notice tone="error">{templates.error}</Notice>
      {:else if !templates.data}
        <p class="text-muted-foreground" role="status">Wird geladen …</p>
      {:else}
        {#if templates.data.templates.length === 0}
          <p class="m-0 text-sm text-muted-foreground" data-testid="no-templates">Es gibt noch keine Vorlage.</p>
        {:else}
          <ul class="m-0 grid list-none gap-3 p-0" data-testid="template-list">
            {#each templates.data.templates as template (template.id)}
              <li class="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4">
                <div class="min-w-0">
                  <div class="flex items-center gap-2 font-medium"><LayoutTemplate size={16} aria-hidden="true" /><span class="truncate">{template.name}</span></div>
                  <div class="text-xs text-muted-foreground">{template.tile_count === 1 ? '1 Kachel' : `${template.tile_count} Kacheln`}</div>
                </div>
                <div class="flex flex-wrap gap-2">
                  <Button variant="outline" onclick={() => edit({ kind: 'template', template })}><Pencil size={16} aria-hidden="true" />Bearbeiten</Button>
                  <Button variant="outline" aria-label={`Vorlage ${template.name} löschen`} onclick={() => { removeTarget = template; removeOpen = true; }}><Trash2 size={16} aria-hidden="true" />Löschen</Button>
                </div>
              </li>
            {/each}
          </ul>
        {/if}

        <form class="grid gap-3 rounded-xl border bg-card p-4 sm:grid-cols-[minmax(0,1fr)_auto]" onsubmit={createEmpty}>
          <label class="grid gap-1 text-sm font-medium">Neue Vorlage
            <input class={field} bind:value={newName} required maxlength="80" placeholder="z. B. Mieter" autocomplete="off" data-testid="new-template-name" />
          </label>
          <div class="flex flex-wrap items-end gap-2">
            <Button type="submit" disabled={actions.busy || !newName.trim()}><Plus size={16} aria-hidden="true" />Leer anlegen</Button>
          </div>
          <div class="grid gap-1 text-sm font-medium sm:col-span-2">
            <label for="from-user">Oder aus der Übersicht einer Person übernehmen</label>
            <div class="flex flex-wrap gap-2">
              <select id="from-user" class="{field} min-w-0 flex-1" bind:value={fromUser}>
                <option value="">Person wählen …</option>
                {#each users.data?.users ?? [] as user (user.id)}<option value={String(user.id)}>{user.display_name || user.username}</option>{/each}
              </select>
              <Button type="button" variant="outline" disabled={actions.busy || !fromUser || !newName.trim()} onclick={createFromUser}>Übernehmen</Button>
            </div>
            <span class="text-xs font-normal text-muted-foreground">Der Name oben wird verwendet.</span>
          </div>
        </form>
      {/if}
    </section>

    <section aria-labelledby="user-overviews-title" class="grid gap-4">
      <div>
        <h2 id="user-overviews-title" class="m-0">Übersicht einer Person</h2>
        <p class="m-0 text-sm text-muted-foreground">Die Übersicht jeder Person lässt sich hier einrichten, auch zurücksetzen. Die Person sieht Änderungen beim nächsten Laden.</p>
      </div>
      {#if users.error && !users.data}
        <Notice tone="error">{users.error}</Notice>
      {:else if !users.data}
        <p class="text-muted-foreground" role="status">Wird geladen …</p>
      {:else}
        <ul class="m-0 grid list-none gap-3 p-0" data-testid="overview-users">
          {#each users.data.users as user (user.id)}
            <li class="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4">
              <div class="min-w-0">
                <div class="truncate font-medium">{user.display_name || user.username}</div>
                <div class="text-xs text-muted-foreground">{user.username}{user.is_active ? '' : ' · gesperrt'}</div>
              </div>
              <Button variant="outline" aria-label={`Übersicht von ${user.display_name || user.username} bearbeiten`} onclick={() => edit({ kind: 'user', user })}><Pencil size={16} aria-hidden="true" />Übersicht bearbeiten</Button>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </div>
{/if}

<ConfirmDialog
  bind:open={removeOpen}
  title={`Vorlage „${removeTarget?.name ?? ''}“ löschen?`}
  description="Bereits angelegte Personen behalten ihre Übersicht. Nur die Vorlage verschwindet aus der Auswahl."
  confirmLabel="Löschen"
  busy={actions.busy}
  onconfirm={remove}
/>
