<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { Ban, Plus } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Switch } from '$lib/components/ui/switch';
  import * as Dialog from '$lib/components/ui/dialog';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import SecretDialog, { type SecretDialogApi } from './SecretDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { VALID_DAYS, formatDateTime, tokenState, validDays } from '$lib/access/format';
  import { kioskLink } from '$lib/access/secret';
  import type { KioskData, KioskRow } from '$lib/access/types';

  let { title }: { title: string } = $props();
  const { api, prefix } = useApp();
  const client = createAccessClient(api);
  const kiosk = createResource<KioskData>(() => client.kioskTokens());
  const actions = createActions(() => kiosk.load());
  onMount(() => void kiosk.load());
  onDestroy(() => kiosk.stop());

  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';
  let secret = $state<SecretDialogApi>();

  let issueOpen = $state(false);
  let name = $state('');
  let days = $state('');
  let control = $state(false);
  let zoneIds = $state<number[]>([]);
  function openIssue() {
    name = '';
    days = '';
    control = false;
    zoneIds = [];
    actions.clear();
    issueOpen = true;
  }
  function toggleZone(id: number) {
    zoneIds = zoneIds.includes(id) ? zoneIds.filter((z) => z !== id) : [...zoneIds, id];
  }
  async function issue(event: SubmitEvent) {
    event.preventDefault();
    const label = name;
    const result = await actions.run(
      'issue',
      () => client.issueKiosk({ name, zone_ids: zoneIds, control_allowed: control, valid_days: validDays(days) }),
      null,
    );
    if (result.ok) {
      issueOpen = false;
      secret?.show(result.value.plaintext, label);
      await kiosk.load();
    }
  }

  let revokeOpen = $state(false);
  let revokeTarget = $state<KioskRow | null>(null);
  async function revoke() {
    if (!revokeTarget) return;
    const id = revokeTarget.id;
    const result = await actions.run(`kiosk-${id}`, () => client.revokeKiosk(id), 'Zugang widerrufen. Das Tablett zeigt nichts mehr an.');
    if (result.ok) await kiosk.load();
  }
</script>

<header class="mb-6">
  <h1>{title}</h1>
  <p class="text-muted-foreground">
    Ein Kiosk-Zugang öffnet die Wandtablett-Ansicht ohne Anmeldung, aber nur für die gewählten Räume und jederzeit widerrufbar.
  </p>
</header>

<section class="grid gap-4" aria-labelledby="kiosk-title">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 id="kiosk-title" class="m-0">Zugänge</h2>
    {#if kiosk.data}<Button onclick={openIssue}><Plus size={16} aria-hidden="true" />Zugang ausstellen</Button>{/if}
  </div>

  {#if kiosk.forbidden && !kiosk.data}
    <Notice tone="warn">Kiosk-Zugänge darfst du nicht verwalten.</Notice>
  {:else if kiosk.error && !kiosk.data}
    <Notice tone="error">{kiosk.error}</Notice>
  {:else if !kiosk.data}
    <p class="text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    {#if kiosk.error}<Notice tone="error">{kiosk.error}</Notice>{/if}
    {#if !kiosk.data.tokens.length}<p class="m-0 text-muted-foreground">Noch kein Kiosk-Zugang ausgestellt.</p>{/if}
    <ul class="m-0 grid list-none gap-3 p-0" data-testid="kiosk-list">
      {#each kiosk.data.tokens as t (t.id)}
        {@const st = tokenState(t.state)}
        <li class="grid gap-2 rounded-xl border bg-card p-4" data-testid="kiosk-row" data-name={t.name}>
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="m-0 text-base">{t.name}</h3>
            <Badge variant={st.tone === 'ok' ? 'secondary' : st.tone === 'warn' ? 'destructive' : 'outline'}>{st.label}</Badge>
            <span class="font-mono text-xs text-muted-foreground">{t.prefix}…</span>
          </div>
          <p class="m-0 text-sm">
            <span class="text-muted-foreground">Räume:</span> {t.zones.length ? t.zones.map((z) => z.name).join(', ') : '—'} ·
            {t.control_allowed ? 'darf bedienen' : 'nur ansehen'}
          </p>
          <p class="m-0 text-sm text-muted-foreground">
            Ausgestellt {formatDateTime(t.created_at)} · gültig bis {formatDateTime(t.expires_at, 'unbegrenzt')} · zuletzt benutzt {formatDateTime(t.last_used_at, 'nie')}
          </p>
          {#if t.state === 'active'}
            <div class="pt-1">
              <Button variant="destructive" class="h-11" disabled={actions.busy} onclick={() => { revokeTarget = t; revokeOpen = true; }}>
                <Ban size={16} aria-hidden="true" />Widerrufen
              </Button>
            </div>
          {/if}
          {#if actions.notice?.where === `kiosk-${t.id}`}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Dialog.Root bind:open={issueOpen}>
  <Dialog.Content class="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
    <form onsubmit={issue} class="grid gap-4">
      <Dialog.Header>
        <Dialog.Title>Kiosk-Zugang ausstellen</Dialog.Title>
        <Dialog.Description>Die Adresse fürs Tablett siehst du danach genau einmal.</Dialog.Description>
      </Dialog.Header>
      <label class="grid gap-1 text-sm font-medium">Name
        <input class={field} bind:value={name} required maxlength="128" autocomplete="off" placeholder="z. B. Tablett Flur" aria-invalid={actions.notice?.failure?.field === 'name'} />
      </label>
      <fieldset class="m-0 grid gap-1 border-0 p-0">
        <legend class="mb-1 p-0 text-sm font-medium">Räume</legend>
        {#each kiosk.data?.zones ?? [] as z (z.id)}
          <label class="flex min-h-10 items-center gap-2 text-sm">
            <input type="checkbox" class="size-5" checked={zoneIds.includes(z.id)} onchange={() => toggleZone(z.id)} />{z.name}
          </label>
        {/each}
      </fieldset>
      <div class="flex items-center gap-3">
        <Switch id="kiosk-control" bind:checked={control} />
        <label for="kiosk-control" class="text-sm">Das Tablett darf Sollwerte ändern</label>
      </div>
      <label class="grid gap-1 text-sm font-medium">Gültig
        <select class={field} bind:value={days}>
          {#each VALID_DAYS as d (d.value)}<option value={d.value}>{d.label}</option>{/each}
        </select>
      </label>
      {#if actions.notice?.where === 'issue'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
      <Dialog.Footer>
        <Button type="button" variant="outline" onclick={() => (issueOpen = false)}>Abbrechen</Button>
        <Button type="submit" disabled={actions.busy}>Zugang ausstellen</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={revokeOpen}
  title={`Zugang „${revokeTarget?.name ?? ''}“ widerrufen?`}
  description="Das Tablett mit diesem Zugang zeigt sofort nichts mehr an. Das lässt sich nicht rückgängig machen; du kannst einen neuen ausstellen."
  confirmLabel="Widerrufen"
  busy={actions.busy}
  onconfirm={revoke}
/>

<SecretDialog
  bind:api={secret}
  title="Dein neuer Kiosk-Zugang"
  intro="Öffne die Adresse auf dem Tablett und speichere sie dort als Lesezeichen."
  secretLabel="Token"
  linkLabel="Adresse fürs Tablett"
  link={(plaintext) => kioskLink(window.location.origin, prefix, plaintext)}
/>
