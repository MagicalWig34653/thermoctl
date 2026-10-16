<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { Fingerprint, Trash2 } from '@lucide/svelte';
  import { ApiError } from '$lib/api';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from '$lib/components/access/ConfirmDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { formatDateTime } from '$lib/access/format';
  import { accessFailure } from '$lib/access/errors';
  import { passkeyMessage, passkeySupported, prepareRegistration, registrationBody, type RegistrationOptions } from '$lib/access/passkey';
  import type { PasskeyRow, PasskeysData } from '$lib/access/types';

  const { api } = useApp();
  const client = createAccessClient(api);
  const passkeys = createResource<PasskeysData>(() => client.passkeys());
  const actions = createActions(() => passkeys.load());
  onMount(() => void passkeys.load());
  onDestroy(() => passkeys.stop());

  const supported = passkeySupported();
  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';
  let label = $state('');
  let registering = $state(false);
  let message = $state<{ tone: 'error' | 'success'; text: string } | null>(null);

  // Registrierung über die vorhandenen JSON-Routen (nicht unter /ui-api): Optionen holen,
  // Authenticator fragen, Ergebnis zurückschicken. Der Server prüft Herausforderung und Besitzer.
  async function register(event: SubmitEvent) {
    event.preventDefault();
    if (registering || actions.busy) return;
    registering = true;
    message = null;
    try {
      const options = await api.post<RegistrationOptions>('/passkey/registration/options');
      const credential = (await navigator.credentials.create({ publicKey: prepareRegistration(options) })) as PublicKeyCredential | null;
      if (!credential) throw new Error('Es wurde kein Passkey angelegt.');
      await api.post('/passkey/registration/verify', registrationBody(label.trim(), credential));
      label = '';
      message = { tone: 'success', text: 'Passkey hinterlegt.' };
      await passkeys.load();
    } catch (error) {
      if (error instanceof ApiError) {
        const notice = error.body && typeof error.body.notice === 'string' ? error.body.notice : null;
        message = { tone: 'error', text: notice ?? accessFailure(error).message };
      } else {
        message = { tone: 'error', text: passkeyMessage(error) };
      }
    } finally {
      registering = false;
    }
  }

  let removeOpen = $state(false);
  let removeTarget = $state<PasskeyRow | null>(null);
  async function remove() {
    if (!removeTarget) return;
    const id = removeTarget.id;
    message = null;
    const result = await actions.run('remove', () => client.removePasskey(id), 'Passkey entfernt.');
    if (result.ok) await passkeys.load();
  }
</script>

<section class="grid max-w-3xl gap-4 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="passkeys-title">
  <div>
    <h2 id="passkeys-title" class="m-0 text-lg">Passkeys</h2>
    <p class="m-0 text-sm text-muted-foreground">Anmelden mit Fingerabdruck, Gesicht oder Sicherheitsschlüssel statt Passwort.</p>
  </div>

  {#if passkeys.error && !passkeys.data}
    <Notice tone="error">{passkeys.error}</Notice>
  {:else if !passkeys.data}
    <p class="m-0 text-muted-foreground" role="status">Wird geladen …</p>
  {:else if !passkeys.data.available}
    <p class="m-0 text-sm text-muted-foreground">Passkeys sind für diese Installation nicht eingerichtet.</p>
  {:else}
    {#if !passkeys.data.passkeys.length}<p class="m-0 text-sm text-muted-foreground">Noch kein Passkey hinterlegt.</p>{/if}
    <ul class="m-0 grid list-none gap-2 p-0" data-testid="passkey-list">
      {#each passkeys.data.passkeys as p (p.id)}
        <li class="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3" data-testid="passkey-row">
          <div>
            <div class="font-medium">{p.label || 'Passkey'}</div>
            <div class="text-sm text-muted-foreground">Angelegt {formatDateTime(p.created_at)} · zuletzt benutzt {formatDateTime(p.last_used_at, 'nie')}</div>
          </div>
          <Button variant="destructive" class="h-11" disabled={actions.busy} onclick={() => { removeTarget = p; removeOpen = true; }}>
            <Trash2 size={16} aria-hidden="true" />Entfernen
          </Button>
        </li>
      {/each}
    </ul>
    {#if actions.notice?.where === 'remove'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}

    {#if supported}
      <form onsubmit={register} class="grid max-w-md gap-3">
        <label class="grid gap-1 text-sm font-medium">Name für den neuen Passkey (optional)
          <input class={field} bind:value={label} maxlength="64" autocomplete="off" placeholder="z. B. iPhone" />
        </label>
        <div><Button type="submit" disabled={registering || actions.busy}><Fingerprint size={16} aria-hidden="true" />{registering ? 'Warte auf das Gerät …' : 'Passkey hinterlegen'}</Button></div>
      </form>
    {:else}
      <p class="m-0 text-sm text-muted-foreground">Dieser Browser unterstützt keine Passkeys. Öffne die Seite in einem aktuellen Browser über HTTPS.</p>
    {/if}
    {#if message}<Notice tone={message.tone}>{message.text}</Notice>{/if}
  {/if}
</section>

<ConfirmDialog
  bind:open={removeOpen}
  title={`Passkey „${removeTarget?.label || 'Passkey'}“ entfernen?`}
  description="Mit diesem Passkey kannst du dich danach nicht mehr anmelden. Andere Anmeldewege bleiben."
  confirmLabel="Entfernen"
  busy={actions.busy}
  onconfirm={remove}
/>
