<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';

  const { api } = useApp();
  const client = createAccessClient(api);
  const actions = createActions(() => undefined);
  const field = 'h-11 w-full min-w-0 rounded-lg border bg-background px-3';

  let current = $state('');
  let next = $state('');
  let again = $state('');
  let mismatch = $state(false);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    mismatch = next !== again;
    if (mismatch) return;
    const result = await actions.run(
      'password',
      () => client.changePassword(current, next),
      'Passwort geändert. Alle anderen Sitzungen wurden beendet.',
    );
    // Eingaben nach jedem Versuch aus dem Speicher der Seite nehmen; bei Fehler bleibt nur das neue Feld stehen.
    current = '';
    if (result.ok) {
      next = '';
      again = '';
    }
  }
  const wrong = $derived(actions.notice?.failure?.field === 'current_password');
  const weak = $derived(actions.notice?.failure?.field === 'new_password');
</script>

<section class="grid w-full max-w-[820px] gap-4 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="pw-title">
  <div>
    <h2 id="pw-title" class="m-0 text-lg">Passwort ändern</h2>
    <p class="m-0 text-sm text-muted-foreground">Dein bisheriges Passwort ist nötig. Danach enden alle anderen angemeldeten Sitzungen.</p>
  </div>
  <form onsubmit={submit} class="grid gap-3">
    <label class="grid gap-1 text-sm font-medium">Bisheriges Passwort
      <input class={field} type="password" bind:value={current} required autocomplete="current-password" aria-invalid={wrong} aria-describedby={wrong ? 'pw-msg' : undefined} />
    </label>
    <label class="grid gap-1 text-sm font-medium">Neues Passwort
      <input class={field} type="password" bind:value={next} required autocomplete="new-password" aria-invalid={weak} aria-describedby={weak ? 'pw-msg' : undefined} />
      <span class="text-xs font-normal text-muted-foreground">Mindestens 12 Zeichen. Alle anderen angemeldeten Geräte werden dabei abgemeldet; dieses bleibt angemeldet.</span>
    </label>
    <label class="grid gap-1 text-sm font-medium">Neues Passwort wiederholen
      <input class={field} type="password" bind:value={again} required autocomplete="new-password" aria-invalid={mismatch} />
    </label>
    {#if mismatch}<Notice tone="error">Die beiden neuen Passwörter sind nicht gleich.</Notice>{/if}
    {#if actions.notice}<Notice id="pw-msg" tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
    <div><Button type="submit" disabled={actions.busy}>Passwort ändern</Button></div>
  </form>
</section>
