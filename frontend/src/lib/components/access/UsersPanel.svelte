<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { KeyRound, Plus, UserCheck, UserX } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { formatDateTime } from '$lib/access/format';
  import type { UserRow, UsersData } from '$lib/access/types';

  const { api } = useApp();
  const client = createAccessClient(api);
  const users = createResource<UsersData>(() => client.users());
  const actions = createActions(() => users.load());
  onMount(() => void users.load());
  onDestroy(() => users.stop());

  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';

  function replace(row: UserRow) {
    if (users.data) users.set({ ...users.data, users: users.data.users.map((u) => (u.id === row.id ? row : u)) });
  }

  // -- Anlegen ----------------------------------------------------------------------------
  let createOpen = $state(false);
  let form = $state({ username: '', display_name: '', password: '', group_id: '' });
  function openCreate() {
    form = { username: '', display_name: '', password: '', group_id: '' };
    actions.clear();
    createOpen = true;
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    const result = await actions.run(
      'create',
      () =>
        client.createUser({
          username: form.username,
          display_name: form.display_name,
          password: form.password,
          group_id: form.group_id ? Number(form.group_id) : null,
        }),
      null,
    );
    if (result.ok) {
      createOpen = false;
      form.password = '';
      await users.load();
      actions.clear();
    }
  }

  // -- Gruppe ändern ----------------------------------------------------------------------
  async function changeGroup(user: UserRow, select: HTMLSelectElement) {
    note = null;
    const previous = user.group_id === null ? '' : String(user.group_id);
    const result = await actions.run(
      `user-${user.id}`,
      () => client.setUserGroup(user.id, select.value ? Number(select.value) : null),
      'Gruppe geändert.',
    );
    if (result.ok) replace(result.value);
    else select.value = previous; // der Server hat abgelehnt: Anzeige zurück auf den echten Stand
  }

  // -- Sperren / Aktivieren ---------------------------------------------------------------
  let lockTarget = $state<UserRow | null>(null);
  let lockOpen = $state(false);
  async function setActive(user: UserRow, active: boolean) {
    const result = await actions.run(`user-${user.id}`, () => client.setActive(user.id, active), active ? 'Konto aktiviert.' : 'Konto gesperrt.');
    if (result.ok) replace(result.value);
  }

  // -- Passwort setzen --------------------------------------------------------------------
  let pwTarget = $state<UserRow | null>(null);
  let pwOpen = $state(false);
  let pw = $state('');
  let note = $state<{ id: number; text: string } | null>(null);
  async function setPassword(event: SubmitEvent) {
    event.preventDefault();
    note = null;
    if (!pwTarget) return;
    const target = pwTarget;
    const result = await actions.run('password', () => client.setUserPassword(target.id, pw), null);
    if (result.ok) {
      pw = '';
      pwOpen = false;
      note = { id: target.id, text: `Passwort für ${target.username} gesetzt. Alle Sitzungen dieses Kontos wurden beendet.` };
    }
  }
</script>

<section aria-labelledby="users-title" class="grid gap-4">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h2 id="users-title" class="m-0">Benutzer</h2>
      <p class="m-0 text-sm text-muted-foreground">Wer sich anmelden darf. Die Rechte bestimmt die Gruppe.</p>
    </div>
    {#if users.data}<Button onclick={openCreate}><Plus size={16} aria-hidden="true" />Benutzer anlegen</Button>{/if}
  </div>

  {#if actions.notice && !['create', 'password'].includes(actions.notice.where) && !actions.notice.where.startsWith('user-')}
    <Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>
  {/if}

  {#if users.forbidden && !users.data}
    <Notice tone="warn">Für die Benutzerverwaltung fehlt dir die Berechtigung.</Notice>
  {:else if users.error && !users.data}
    <Notice tone="error">{users.error}</Notice>
  {:else if !users.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if users.error}<Notice tone="error">{users.error}</Notice>{/if}
    <ul class="m-0 grid list-none gap-3 p-0" data-testid="user-list">
      {#each users.data.users as user (user.id)}
        <li class="grid gap-3 rounded-xl border bg-card p-4" data-testid="user-row" data-username={user.username}>
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="m-0 text-base">{user.display_name || user.username}</h3>
            <span class="text-sm text-muted-foreground">{user.username}</span>
            {#if user.is_self}<Badge variant="secondary">Du</Badge>{/if}
            {#if !user.is_active}<Badge variant="destructive">gesperrt</Badge>{/if}
          </div>
          <p class="m-0 text-sm text-muted-foreground">
            Angelegt {formatDateTime(user.created_at)} · Letzte Anmeldung {formatDateTime(user.last_login_at, 'noch nie')}
          </p>
          <div class="flex flex-wrap items-end gap-3">
            <label class="grid min-w-48 flex-1 gap-1 text-sm font-medium sm:max-w-xs">
              Gruppe
              <select
                class={field}
                value={user.group_id === null ? '' : String(user.group_id)}
                disabled={actions.busy}
                onchange={(e) => changeGroup(user, e.currentTarget)}
              >
                <option value="">Keine Gruppe</option>
                {#each users.data.groups as g (g.id)}<option value={String(g.id)}>{g.name}</option>{/each}
              </select>
            </label>
            {#if !user.is_self}
              <Button variant="outline" class="h-11" disabled={actions.busy} onclick={() => { pwTarget = user; pw = ''; actions.clear(); pwOpen = true; }}>
                <KeyRound size={16} aria-hidden="true" />Passwort setzen
              </Button>
            {/if}
            {#if user.is_active}
              <Button variant="destructive" class="h-11" disabled={actions.busy} onclick={() => { lockTarget = user; lockOpen = true; }}>
                <UserX size={16} aria-hidden="true" />Sperren
              </Button>
            {:else}
              <Button variant="outline" class="h-11" disabled={actions.busy} onclick={() => setActive(user, true)}>
                <UserCheck size={16} aria-hidden="true" />Aktivieren
              </Button>
            {/if}
          </div>
          {#if actions.notice?.where === `user-${user.id}`}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>
          {:else if note?.id === user.id}<Notice tone="success">{note.text}</Notice>{/if}
        </li>
      {/each}
    </ul>
    {#if users.data.groups.length === 0}
      <p class="m-0 text-sm text-muted-foreground">Es gibt noch keine Gruppe. Lege zuerst unter „Gruppen“ eine an.</p>
    {/if}
  {/if}
</section>

<Dialog.Root bind:open={createOpen}>
  <Dialog.Content class="sm:max-w-md">
    <form onsubmit={create} class="grid gap-4">
      <Dialog.Header>
        <Dialog.Title>Benutzer anlegen</Dialog.Title>
        <Dialog.Description>Die Person meldet sich mit diesem Namen und Passwort an und kann es unter Konto ändern.</Dialog.Description>
      </Dialog.Header>
      <label class="grid gap-1 text-sm font-medium">Benutzername
        <input class={field} bind:value={form.username} required maxlength="64" autocomplete="off" autocapitalize="none" aria-invalid={actions.notice?.failure?.field === 'username'} />
      </label>
      <label class="grid gap-1 text-sm font-medium">Anzeigename
        <input class={field} bind:value={form.display_name} maxlength="128" autocomplete="off" />
      </label>
      <label class="grid gap-1 text-sm font-medium">Startpasswort
        <input class={field} type="password" bind:value={form.password} required autocomplete="new-password" aria-invalid={actions.notice?.failure?.field === 'password'} />
      </label>
      <label class="grid gap-1 text-sm font-medium">Gruppe
        <select class={field} bind:value={form.group_id}>
          <option value="">Keine Gruppe</option>
          {#each users.data?.groups ?? [] as g (g.id)}<option value={String(g.id)}>{g.name}</option>{/each}
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

<Dialog.Root bind:open={pwOpen}>
  <Dialog.Content class="sm:max-w-md">
    <form onsubmit={setPassword} class="grid gap-4">
      <Dialog.Header>
        <Dialog.Title>Passwort setzen für {pwTarget?.username}</Dialog.Title>
        <Dialog.Description>Alle laufenden Sitzungen dieses Kontos enden. Das eigene Passwort änderst du unter Konto.</Dialog.Description>
      </Dialog.Header>
      <label class="grid gap-1 text-sm font-medium">Neues Passwort
        <input class={field} type="password" bind:value={pw} required autocomplete="new-password" aria-invalid={actions.notice?.failure?.field === 'password'} />
      </label>
      {#if actions.notice?.where === 'password'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
      <Dialog.Footer>
        <Button type="button" variant="outline" onclick={() => (pwOpen = false)}>Abbrechen</Button>
        <Button type="submit" disabled={actions.busy}>Passwort setzen</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={lockOpen}
  title={`${lockTarget?.username ?? ''} sperren?`}
  description={lockTarget?.is_self
    ? 'Du sperrst dein eigenes Konto. Du kannst dich danach nicht mehr anmelden, bis jemand es wieder aktiviert.'
    : 'Die Person kann sich nicht mehr anmelden; laufende Sitzungen enden. Das Konto bleibt erhalten und lässt sich wieder aktivieren.'}
  confirmLabel="Sperren"
  busy={actions.busy}
  onconfirm={() => (lockTarget ? setActive(lockTarget, false) : undefined)}
/>
