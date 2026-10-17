<script lang="ts">
  import { onMount } from 'svelte';
  import { passkeySupported } from '$lib/access/passkey';
  import { appHref } from '$lib/paths';
  import { fetchPasskeysOffered, passkeyLogin, passkeyLoginMessage } from '$lib/auth/login';
  import { requestedReturnTo, targetHref } from '$lib/auth/target';
  import { loadSetupStatus } from '$lib/setup/client';
  import { KeyRound } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { Label } from '$lib/components/ui/label';
  import { useApp } from '$lib/context';

  const { session, router, api, prefix } = useApp();

  let username = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let pending = $state(false);
  let passkeysOffered = $state(false);
  let hint = $state<string | null>(null);

  // Nur ein Wunsch aus der Adresse; ob er gilt, sagt der Server (`target` in den Antworten).
  const returnTo = $derived(requestedReturnTo(router.search));
  const showPasskey = $derived(passkeysOffered && passkeySupported());

  onMount(() => {
    void (async () => {
      // Ohne Benutzer führt die Anmeldung ins Leere: dann zur Ersteinrichtung (wie `/` im Altsystem).
      if ((await loadSetupStatus(api)) === 'open') {
        window.location.replace(appHref(prefix, '/setup'));
        return;
      }
      passkeysOffered = await fetchPasskeysOffered(api);
    })();
  });

  /** Voll-Seitenwechsel auf ein vom Server bestätigtes Ziel (z. B. die OAuth-Zustimmung). */
  function leave(target: string) {
    window.location.assign(targetHref(prefix, target));
  }

  function arrived() {
    // Die Adresse ist unverändert geblieben; nur von der Anmeldeadresse selbst geht es zur Übersicht.
    if (router.path === '/login') router.navigate('/', { replace: true });
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (pending) return;
    pending = true;
    error = null;
    hint = null;
    const result = await session.login(username.trim(), password, returnTo);
    pending = false;
    password = '';
    if (result.error !== null) {
      error = result.error;
    } else if (result.target !== null) {
      leave(result.target);
    } else {
      arrived();
    }
  }

  async function withPasskey() {
    if (pending) return;
    pending = true;
    error = null;
    hint = 'Warte auf den Authenticator …';
    try {
      const { target } = await passkeyLogin({ api, credentials: navigator.credentials }, returnTo);
      if (target !== null) {
        leave(target);
        return;
      }
      const failed = await session.finishLogin();
      if (failed === null) arrived();
      else error = failed;
    } catch (failure) {
      error = passkeyLoginMessage(failure);
    } finally {
      pending = false;
      hint = null;
    }
  }
</script>

<main class="grid min-h-screen place-items-center px-4 py-10">
  <div class="w-full max-w-sm">
    <div class="mb-6 flex items-center gap-3">
      <img class="size-10" src={`${prefix}/static/favicon.svg`} alt="" width="40" height="40" />
      <div>
        <h1 class="m-0 text-2xl">thermoctl</h1>
        <p class="m-0 text-sm text-muted-foreground">Heizungssteuerung</p>
      </div>
    </div>

    <form class="rounded-xl border bg-card p-6 shadow-sm" onsubmit={submit} novalidate>
      <h2 class="mb-1">Anmelden</h2>
      {#if session.notice}
        <p class="mb-3 rounded-md bg-muted px-3 py-2 text-sm" role="status">{session.notice}</p>
      {/if}
      <div class="mt-3 flex flex-col gap-2">
        <Label for="login-username">Benutzername</Label>
        <Input
          id="login-username"
          name="username"
          autocomplete="username"
          autocapitalize="none"
          spellcheck="false"
          required
          bind:value={username}
          aria-invalid={error ? 'true' : undefined}
        />
      </div>
      <div class="mt-4 flex flex-col gap-2">
        <Label for="login-password">Passwort</Label>
        <Input
          id="login-password"
          name="password"
          type="password"
          autocomplete="current-password"
          required
          bind:value={password}
          aria-invalid={error ? 'true' : undefined}
        />
      </div>
      {#if error}
        <p class="mt-4 text-sm text-destructive" role="alert">{error}</p>
      {/if}
      <Button type="submit" class="mt-5 w-full" disabled={pending || !username || !password}>
        {pending && !hint ? 'Anmelden …' : 'Anmelden'}
      </Button>
      {#if showPasskey}
        <div class="my-4 flex items-center gap-3 text-xs text-muted-foreground" aria-hidden="true">
          <span class="h-px flex-1 bg-border"></span>oder<span class="h-px flex-1 bg-border"></span>
        </div>
        <Button type="button" variant="outline" class="w-full" disabled={pending} onclick={withPasskey}>
          <KeyRound size={16} aria-hidden="true" />
          Mit Passkey anmelden
        </Button>
        {#if hint}
          <p class="mt-2 text-sm text-muted-foreground" role="status">{hint}</p>
        {/if}
      {/if}
    </form>
  </div>
</main>
