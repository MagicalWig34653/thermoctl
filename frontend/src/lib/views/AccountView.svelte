<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { LogOut } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import PasskeysCard from '$lib/components/account/PasskeysCard.svelte';
  import PasswordCard from '$lib/components/account/PasswordCard.svelte';
  import SessionsCard from '$lib/components/account/SessionsCard.svelte';
  import ReportCard from '$lib/components/zones/ReportCard.svelte';
  import { useApp } from '$lib/context';
  import { createAccessClient } from '$lib/access/client';
  import { createResource } from '$lib/access/resource.svelte';
  import { formatDateTime, } from '$lib/access/format';
  import { profileLabel } from '$lib/access/matrix';
  import type { Account } from '$lib/access/types';
  import { appHref } from '$lib/paths';

  const { api, session, prefix } = useApp();
  const client = createAccessClient(api);
  const account = createResource<Account>(() => client.account());
  onMount(() => void account.load());
  onDestroy(() => account.stop());

  let error = $state<string | null>(null);
  async function logout() {
    error = await session.logout();
  }
</script>

<header class="mb-6">
  <h1>Konto & Sicherheit</h1>
  <p class="text-muted-foreground">Dein Profil, dein Passwort, deine Geräte und Passkeys.</p>
</header>

<div class="grid gap-6">
  <section class="grid max-w-xl gap-3 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="profile-title">
    <h2 id="profile-title" class="m-0 text-lg">Profil</h2>
    {#if account.error && !account.data}
      <Notice tone="error">{account.error}</Notice>
    {:else if !account.data}
      <p class="m-0 text-muted-foreground" role="status">Wird geladen …</p>
    {:else}
      <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt class="text-muted-foreground">Name</dt><dd class="m-0">{account.data.display_name || account.data.username}</dd>
        <dt class="text-muted-foreground">Benutzername</dt><dd class="m-0">{account.data.username}</dd>
        <dt class="text-muted-foreground">Oberfläche</dt><dd class="m-0">{profileLabel(account.data.ui_profile)}</dd>
        <dt class="text-muted-foreground">Konto seit</dt><dd class="m-0">{formatDateTime(account.data.created_at)}</dd>
        <dt class="text-muted-foreground">Letzte Anmeldung</dt><dd class="m-0">{formatDateTime(account.data.last_login_at, 'noch nie')}</dd>
      </dl>
    {/if}
    <div>
      <Button variant="outline" onclick={logout}><LogOut size={16} aria-hidden="true" />Abmelden</Button>
      {#if error}<p class="mt-3 text-sm text-destructive" role="alert">{error}</p>{/if}
    </div>
  </section>

  <PasswordCard />
  <SessionsCard />
  {#if account.data?.passkeys_available}<PasskeysCard />{/if}
  <ReportCard />

  <section class="max-w-xl rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="help-title">
    <h2 id="help-title" class="m-0 text-lg">Hilfe</h2>
    <p class="mb-0 text-sm"><a href={appHref(prefix, '/hilfe')}>Glossar und Hilfe</a> erklärt die Begriffe der Oberfläche.</p>
  </section>
</div>
