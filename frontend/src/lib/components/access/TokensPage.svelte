<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { Ban, Pencil, Plus } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import GrantPicker from './GrantPicker.svelte';
  import SecretDialog, { type SecretDialogApi } from './SecretDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { VALID_DAYS, formatDateTime, presetLabel, sourceLabel, tokenState, validDays } from '$lib/access/format';
  import { grantSummary, sameGrants } from '$lib/access/matrix';
  import type { Grant, TokenRow, TokensData } from '$lib/access/types';

  let { title }: { title: string } = $props();
  const { api, session } = useApp();
  const client = createAccessClient(api);
  const tokens = createResource<TokensData>(() => client.tokens());
  const actions = createActions(() => tokens.load());
  onMount(() => void tokens.load());
  onDestroy(() => tokens.stop());

  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';
  const zoneName = (id: number) => session.bootstrap?.zones.find((z) => z.id === id)?.name ?? `Raum ${id}`;

  let secret = $state<SecretDialogApi>();

  // -- Ausstellen -------------------------------------------------------------------------
  let issueOpen = $state(false);
  let name = $state('');
  let days = $state('90');
  let mode = $state<string>('read_only'); // Name einer Vorlage oder 'custom'
  let picked = $state<Grant[]>([]);
  function openIssue() {
    name = '';
    days = '90';
    mode = 'read_only';
    picked = [];
    actions.clear();
    issueOpen = true;
  }
  async function issue(event: SubmitEvent) {
    event.preventDefault();
    const body =
      mode === 'custom'
        ? { name, valid_days: validDays(days), permissions: picked }
        : { name, valid_days: validDays(days), preset: mode };
    const label = name;
    const result = await actions.run('issue', () => client.issueToken(body), null);
    if (result.ok) {
      issueOpen = false;
      // Der Klartext geht direkt in den Dialog und wird hier nirgends gemerkt.
      secret?.show(result.value.plaintext, label);
      await tokens.load();
    }
  }

  // -- Rechte ändern ----------------------------------------------------------------------
  let editOpen = $state(false);
  let editTarget = $state<TokenRow | null>(null);
  let editGrants = $state<Grant[]>([]);
  function openEdit(token: TokenRow) {
    editTarget = token;
    editGrants = [...token.permissions];
    actions.clear();
    editOpen = true;
  }
  async function saveEdit() {
    if (!editTarget) return;
    const id = editTarget.id;
    const result = await actions.run('edit', () => client.setTokenPermissions(id, editGrants), null);
    if (result.ok) {
      editOpen = false;
      await tokens.load();
      note = { id, text: 'Rechte geändert.' };
    }
  }

  // -- Widerrufen -------------------------------------------------------------------------
  let revokeOpen = $state(false);
  let revokeTarget = $state<TokenRow | null>(null);
  let note = $state<{ id: number; text: string } | null>(null);
  async function revoke() {
    if (!revokeTarget) return;
    const id = revokeTarget.id;
    note = null;
    const result = await actions.run(`token-${id}`, () => client.revokeToken(id), 'Token widerrufen. Es funktioniert nicht mehr.');
    if (result.ok) await tokens.load();
  }
  const presetNames = $derived(Object.keys(tokens.data?.presets ?? {}));
</script>

<header class="mb-6">
  <h1>{title}</h1>
  <p class="text-muted-foreground">
    Mit einem API-Token greift ein Programm in deinem Namen auf thermoctl zu, nie mit mehr Rechten als du selbst.
  </p>
</header>

<section class="grid gap-4" aria-labelledby="tokens-title">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 id="tokens-title" class="m-0">Deine Tokens</h2>
    {#if tokens.data}<Button onclick={openIssue}><Plus size={16} aria-hidden="true" />Token ausstellen</Button>{/if}
  </div>

  {#if tokens.forbidden && !tokens.data}
    <Notice tone="warn">Eigene API-Tokens darfst du nicht ausstellen.</Notice>
  {:else if tokens.error && !tokens.data}
    <Notice tone="error">{tokens.error}</Notice>
  {:else if !tokens.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if tokens.error}<Notice tone="error">{tokens.error}</Notice>{/if}
    {#if !tokens.data.tokens.length}
      <p class="m-0 text-muted-foreground">Du hast noch kein Token ausgestellt.</p>
    {/if}
    <ul class="m-0 grid list-none gap-3 p-0" data-testid="token-list">
      {#each tokens.data.tokens as t (t.id)}
        {@const st = tokenState(t.state)}
        <li class="grid gap-2 rounded-xl border bg-card p-4" data-testid="token-row" data-name={t.name}>
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="m-0 text-base">{t.name}</h3>
            <Badge variant={st.tone === 'ok' ? 'secondary' : st.tone === 'warn' ? 'destructive' : 'outline'}>{st.label}</Badge>
            <span class="font-mono text-xs text-muted-foreground">{t.prefix}…</span>
          </div>
          <p class="m-0 text-sm text-muted-foreground">
            {sourceLabel(t.source, t.client_name)} · ausgestellt {formatDateTime(t.created_at)} ·
            gültig bis {formatDateTime(t.expires_at, 'unbegrenzt')} · zuletzt benutzt {formatDateTime(t.last_used_at, 'nie')}
          </p>
          <p class="m-0 text-sm">
            <span class="text-muted-foreground">Rechte:</span> {grantSummary(t.permissions)}
            {#if !sameGrants(t.permissions, t.effective)}
              <span class="text-muted-foreground"> · wirksam derzeit: {grantSummary(t.effective)}</span>
            {/if}
          </p>
          {#if t.state === 'active'}
            <div class="flex flex-wrap gap-2 pt-1">
              <Button variant="outline" class="h-11" disabled={actions.busy} onclick={() => openEdit(t)}>
                <Pencil size={16} aria-hidden="true" />Rechte ändern
              </Button>
              <Button variant="destructive" class="h-11" disabled={actions.busy} onclick={() => { revokeTarget = t; revokeOpen = true; }}>
                <Ban size={16} aria-hidden="true" />Widerrufen
              </Button>
            </div>
          {/if}
          {#if actions.notice?.where === `token-${t.id}`}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>
          {:else if note?.id === t.id}<Notice tone="success">{note.text}</Notice>{/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Dialog.Root bind:open={issueOpen}>
  <Dialog.Content class="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
    <form onsubmit={issue} class="grid gap-4">
      <Dialog.Header>
        <Dialog.Title>Token ausstellen</Dialog.Title>
        <Dialog.Description>Den Klartext siehst du danach genau einmal.</Dialog.Description>
      </Dialog.Header>
      <label class="grid gap-1 text-sm font-medium">Name
        <input class={field} bind:value={name} required maxlength="128" autocomplete="off" placeholder="z. B. Skript im Keller" aria-invalid={actions.notice?.failure?.field === 'name'} />
      </label>
      <label class="grid gap-1 text-sm font-medium">Gültig
        <select class={field} bind:value={days}>
          {#each VALID_DAYS as d (d.value)}<option value={d.value}>{d.label}</option>{/each}
        </select>
      </label>
      <fieldset class="m-0 grid gap-1 border-0 p-0">
        <legend class="mb-1 p-0 text-sm font-medium">Rechte</legend>
        {#each presetNames as p (p)}
          <label class="flex min-h-10 items-start gap-2 text-sm">
            <input type="radio" class="mt-0.5 size-5" name="mode" value={p} bind:group={mode} />
            <span>{presetLabel(p)}<span class="text-muted-foreground"> · {tokens.data?.presets[p]?.length ?? 0} Rechte</span></span>
          </label>
        {/each}
        <label class="flex min-h-10 items-start gap-2 text-sm">
          <input type="radio" class="mt-0.5 size-5" name="mode" value="custom" bind:group={mode} />
          <span>Einzeln auswählen</span>
        </label>
      </fieldset>
      {#if mode === 'custom' && tokens.data}
        <GrantPicker available={tokens.data.available} bind:selected={picked} {zoneName} idPrefix="issue" />
      {/if}
      {#if actions.notice?.where === 'issue'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
      <Dialog.Footer>
        <Button type="button" variant="outline" onclick={() => (issueOpen = false)}>Abbrechen</Button>
        <Button type="submit" disabled={actions.busy}>Token ausstellen</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<Dialog.Root bind:open={editOpen}>
  <Dialog.Content class="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
    <Dialog.Header>
      <Dialog.Title>Rechte von „{editTarget?.name}“</Dialog.Title>
      <Dialog.Description>Du kannst nur Rechte vergeben, die du selbst hast. Der Klartext des Tokens ändert sich nicht.</Dialog.Description>
    </Dialog.Header>
    {#if tokens.data}
      <GrantPicker available={tokens.data.available} bind:selected={editGrants} {zoneName} idPrefix="edit" />
    {/if}
    {#if actions.notice?.where === 'edit'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (editOpen = false)}>Abbrechen</Button>
      <Button disabled={actions.busy} onclick={saveEdit}>Speichern</Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={revokeOpen}
  title={`Token „${revokeTarget?.name ?? ''}“ widerrufen?`}
  description="Programme, die dieses Token benutzen, verlieren sofort den Zugriff. Das lässt sich nicht rückgängig machen; du kannst danach ein neues ausstellen."
  confirmLabel="Widerrufen"
  busy={actions.busy}
  onconfirm={revoke}
/>

<SecretDialog
  bind:api={secret}
  title="Dein neues Token"
  intro="Verwende es als Bearer-Token in Programmen, nie in Adresszeilen oder Chats."
  secretLabel="Token"
/>
