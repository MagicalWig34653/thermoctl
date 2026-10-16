<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { Plus, Trash2 } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import PermissionMatrix from './PermissionMatrix.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { changeCount, grantSummary, profileLabel, sortGrants, toggleGrant } from '$lib/access/matrix';
  import type { Grant, GroupRow, GroupsData } from '$lib/access/types';

  const { api } = useApp();
  const client = createAccessClient(api);
  const groups = createResource<GroupsData>(() => client.groups());
  const actions = createActions(() => groups.load());
  onMount(() => void groups.load());
  onDestroy(() => groups.stop());

  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';

  let selectedId = $state<number | null>(null);
  const selected = $derived(groups.data?.groups.find((g) => g.id === selectedId) ?? groups.data?.groups[0] ?? null);

  // Entwurf der Rechte der gewählten Gruppe; wird mit dem Serverstand neu gesetzt.
  let draft = $state<Grant[]>([]);
  let draftFor = $state<number | null>(null);
  $effect(() => {
    if (selected && draftFor !== selected.id) {
      draft = sortGrants(selected.permissions);
      draftFor = selected.id;
    }
  });
  const dirty = $derived(selected ? changeCount(selected.permissions, draft) : 0);

  function replace(row: GroupRow, resetDraft = true) {
    if (!groups.data) return;
    groups.set({ ...groups.data, groups: groups.data.groups.map((g) => (g.id === row.id ? row : g)) });
    if (resetDraft) draft = sortGrants(row.permissions);
  }

  function choose(id: number) {
    selectedId = id;
    draftFor = null;
    actions.clear();
  }

  async function savePermissions() {
    if (!selected) return;
    const id = selected.id;
    const result = await actions.run('perms', () => client.setPermissions(id, draft), 'Rechte gespeichert.');
    if (result.ok) replace(result.value);
  }

  async function changeProfile(select: HTMLSelectElement) {
    if (!selected) return;
    const previous = selected.ui_profile;
    const result = await actions.run('profile', () => client.setUiProfile(selected!.id, select.value), 'Oberfläche geändert.');
    if (result.ok) replace(result.value, false);
    else select.value = previous;
  }

  // -- Anlegen ----------------------------------------------------------------------------
  let createOpen = $state(false);
  let form = $state({ name: '', description: '', ui_profile: 'admin' });
  async function create(event: SubmitEvent) {
    event.preventDefault();
    const result = await actions.run(
      'create',
      () => client.createGroup({ name: form.name, description: form.description.trim() || null, ui_profile: form.ui_profile }),
      null,
    );
    if (result.ok) {
      createOpen = false;
      await groups.load();
      selectedId = result.value.id;
      draftFor = null;
      actions.clear();
    }
  }

  // -- Löschen ----------------------------------------------------------------------------
  let deleteOpen = $state(false);
  async function remove() {
    if (!selected) return;
    const name = selected.name;
    const id = selected.id;
    const result = await actions.run('delete', () => client.deleteGroup(id), `Gruppe „${name}“ gelöscht.`);
    if (result.ok) {
      selectedId = null;
      draftFor = null;
      await groups.load();
    }
  }
</script>

<section aria-labelledby="groups-title" class="grid gap-4">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h2 id="groups-title" class="m-0">Gruppen und Rechte</h2>
      <p class="m-0 text-sm text-muted-foreground">Eine Gruppe bündelt Rechte und bestimmt, welche Oberfläche ihre Mitglieder sehen.</p>
    </div>
    {#if groups.data}
      <Button onclick={() => { form = { name: '', description: '', ui_profile: 'admin' }; actions.clear(); createOpen = true; }}>
        <Plus size={16} aria-hidden="true" />Gruppe anlegen
      </Button>
    {/if}
  </div>

  {#if actions.notice && !['create', 'perms', 'profile'].includes(actions.notice.where) && actions.notice.tone === 'success'}
    <Notice tone="success">{actions.notice.text}</Notice>
  {/if}

  {#if groups.forbidden && !groups.data}
    <Notice tone="warn">Für die Gruppenverwaltung fehlt dir die Berechtigung.</Notice>
  {:else if groups.error && !groups.data}
    <Notice tone="error">{groups.error}</Notice>
  {:else if !groups.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if groups.error}<Notice tone="error">{groups.error}</Notice>{/if}
    <div class="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <ul class="m-0 flex list-none flex-wrap gap-2 p-0 lg:flex-col" aria-label="Gruppen" data-testid="group-list">
        {#each groups.data.groups as g (g.id)}
          <li class="lg:w-full">
            <button
              type="button"
              class="flex min-h-11 w-full flex-col items-start rounded-lg border px-3 py-2 text-left hover:bg-muted {selected?.id === g.id ? 'border-primary bg-muted' : 'bg-card'}"
              aria-pressed={selected?.id === g.id}
              onclick={() => choose(g.id)}
            >
              <span class="font-medium">{g.name}</span>
              <span class="text-xs text-muted-foreground">{g.member_count} {g.member_count === 1 ? 'Mitglied' : 'Mitglieder'} · {profileLabel(g.ui_profile)}</span>
            </button>
          </li>
        {/each}
      </ul>

      {#if selected}
        <div class="grid min-w-0 gap-5 rounded-xl border bg-card p-4" data-testid="group-detail">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="m-0 text-lg">{selected.name}</h3>
            {#if selected.is_builtin}<Badge variant="secondary">eingebaut</Badge>{/if}
          </div>
          {#if selected.description}<p class="m-0 text-sm text-muted-foreground">{selected.description}</p>{/if}

          <div class="grid gap-1">
            <label class="grid max-w-xs gap-1 text-sm font-medium">Oberfläche der Mitglieder
              <select class={field} value={selected.ui_profile} disabled={actions.busy} onchange={(e) => changeProfile(e.currentTarget)}>
                {#each groups.data.ui_profiles as p (p)}<option value={p}>{profileLabel(p)}</option>{/each}
              </select>
            </label>
            <p class="m-0 text-xs text-muted-foreground">Anlagensicht für die Verwaltung, Mietersicht für Bewohner mit eigenen Räumen.</p>
            {#if actions.notice?.where === 'profile'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
          </div>

          <div class="grid gap-2">
            <h4 class="m-0 text-base">Rechte</h4>
            <p class="m-0 text-sm text-muted-foreground">
              „Anlage“ gilt überall; einzelne Räume schränken ein Recht auf diese Räume ein. Aktuell: {grantSummary(selected.permissions)}.
            </p>
            <PermissionMatrix
              areas={groups.data.areas}
              zones={groups.data.zones}
              grants={draft}
              disabled={actions.busy}
              idPrefix={`g${selected.id}`}
              ontoggle={(code, zoneId) => (draft = toggleGrant(draft, code, zoneId))}
            />
            {#if actions.notice?.where === 'perms'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
            <div class="flex flex-wrap items-center gap-2">
              <Button disabled={actions.busy || dirty === 0} onclick={savePermissions}>Rechte speichern</Button>
              <Button variant="outline" disabled={actions.busy || dirty === 0} onclick={() => (draft = sortGrants(selected.permissions))}>Zurücksetzen</Button>
              <span class="text-sm text-muted-foreground" role="status">{dirty ? `${dirty} ungespeicherte Änderung${dirty === 1 ? '' : 'en'}` : 'Keine Änderungen'}</span>
            </div>
          </div>

          <div class="grid gap-2 border-t pt-4">
            {#if actions.notice?.where === 'delete' && actions.notice.tone !== 'success'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
            <div>
              <Button variant="destructive" disabled={actions.busy || selected.is_builtin} onclick={() => (deleteOpen = true)}>
                <Trash2 size={16} aria-hidden="true" />Gruppe löschen
              </Button>
              {#if selected.is_builtin}<span class="ml-2 text-sm text-muted-foreground">Eingebaute Gruppen bleiben bestehen.</span>{/if}
            </div>
          </div>
        </div>
      {/if}
    </div>
  {/if}
</section>

<Dialog.Root bind:open={createOpen}>
  <Dialog.Content class="sm:max-w-md">
    <form onsubmit={create} class="grid gap-4">
      <Dialog.Header>
        <Dialog.Title>Gruppe anlegen</Dialog.Title>
        <Dialog.Description>Die neue Gruppe hat zunächst keine Rechte. Sie vergibst du danach in der Gruppe.</Dialog.Description>
      </Dialog.Header>
      <label class="grid gap-1 text-sm font-medium">Name
        <input class={field} bind:value={form.name} required maxlength="64" autocomplete="off" aria-invalid={actions.notice?.failure?.field === 'name'} />
      </label>
      <label class="grid gap-1 text-sm font-medium">Beschreibung (optional)
        <input class={field} bind:value={form.description} maxlength="255" autocomplete="off" />
      </label>
      <label class="grid gap-1 text-sm font-medium">Oberfläche
        <select class={field} bind:value={form.ui_profile}>
          {#each groups.data?.ui_profiles ?? ['admin'] as p (p)}<option value={p}>{profileLabel(p)}</option>{/each}
        </select>
      </label>
      {#if actions.notice?.where === 'create'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
      <Dialog.Footer>
        <Button type="button" variant="outline" onclick={() => (createOpen = false)}>Abbrechen</Button>
        <Button type="submit" disabled={actions.busy}>Anlegen</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={deleteOpen}
  title={`Gruppe „${selected?.name ?? ''}“ löschen?`}
  description={`${selected?.member_count ? `${selected.member_count} Mitglied(er) verlieren die Rechte dieser Gruppe. ` : ''}Die Gruppe und ihre Rechte werden entfernt. Das lässt sich nicht rückgängig machen.`}
  confirmLabel="Gruppe löschen"
  busy={actions.busy}
  onconfirm={remove}
/>
