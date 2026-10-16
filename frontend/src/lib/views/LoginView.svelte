<script lang="ts">
  import { Thermometer } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { Label } from '$lib/components/ui/label';
  import { useApp } from '$lib/context';

  const { session, router } = useApp();

  let username = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let pending = $state(false);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (pending) return;
    pending = true;
    error = null;
    const result = await session.login(username.trim(), password);
    pending = false;
    if (result === null) {
      // Die Adresse ist unverändert geblieben; nur von der Anmeldeadresse selbst geht es zur Übersicht.
      if (router.path === '/login') router.navigate('/', { replace: true });
      password = '';
    } else {
      error = result;
      password = '';
    }
  }
</script>

<main class="grid min-h-screen place-items-center px-4 py-10">
  <div class="w-full max-w-sm">
    <div class="mb-6 flex items-center gap-3">
      <div class="grid size-10 place-items-center rounded-lg border bg-background">
        <Thermometer size={22} aria-hidden="true" />
      </div>
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
        {pending ? 'Anmelden …' : 'Anmelden'}
      </Button>
    </form>
  </div>
</main>
