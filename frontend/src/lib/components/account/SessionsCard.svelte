<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import ConfirmDialog from '$lib/components/access/ConfirmDialog.svelte';
  import { createAccessClient } from '$lib/access/client';
  import { createActions } from '$lib/access/actions.svelte';
  import { createResource } from '$lib/access/resource.svelte';
  import { deviceLabel, formatDateTime } from '$lib/access/format';
  import type { SessionRow } from '$lib/access/types';

  const { api } = useApp();
  const client = createAccessClient(api);
  const sessions = createResource<SessionRow[]>(() => client.sessions());
  const actions = createActions(() => sessions.load());
  onMount(() => void sessions.load());
  onDestroy(() => sessions.stop());

  let confirmOpen = $state(false);
  const others = $derived((sessions.data ?? []).filter((s) => !s.current));

  async function endOthers() {
    const result = await actions.run('all', () => client.revokeOthers(), null);
    if (result.ok) {
      await sessions.load();
      // Meldung erst nach dem Neulesen, damit die Zahl zur Liste passt.
      await actions.run('all', async () => true, result.value.ended === 1 ? 'Eine andere Sitzung wurde beendet.' : `${result.value.ended} andere Sitzungen wurden beendet.`);
    }
  }
  async function end(s: SessionRow) {
    const result = await actions.run(`s-${s.id}`, () => client.revokeSession(s.id), 'Sitzung beendet.');
    if (result.ok) await sessions.load();
  }
</script>

<section class="grid w-full max-w-[820px] gap-4 rounded-xl border bg-card p-4 sm:p-5" aria-labelledby="sessions-title">
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div>
      <h2 id="sessions-title" class="m-0 text-lg">Angemeldete Geräte</h2>
      <p class="m-0 text-sm text-muted-foreground">Hier siehst du, wo dein Konto gerade angemeldet ist.</p>
    </div>
    {#if others.length}
      <Button variant="destructive" class="h-11" disabled={actions.busy} onclick={() => (confirmOpen = true)}>Alle anderen beenden</Button>
    {/if}
  </div>
  {#if actions.notice?.where === 'all'}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}

  {#if sessions.error && !sessions.data}
    <Notice tone="error">{sessions.error}</Notice>
  {:else if !sessions.data}
    <p class="m-0 text-muted-foreground" role="status">Wird geladen …</p>
  {:else}
    <ul class="m-0 grid list-none gap-2 p-0" data-testid="session-list">
      {#each sessions.data as s (s.id)}
        <li class="grid gap-1 rounded-lg border p-3" data-testid="session-row" data-current={s.current}>
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium">{deviceLabel(s.user_agent)}</span>
            {#if s.current}<Badge variant="secondary">dieses Gerät</Badge>{/if}
          </div>
          <p class="m-0 text-sm text-muted-foreground">
            {s.ip_address ?? 'Adresse unbekannt'} · angemeldet {formatDateTime(s.created_at)} · zuletzt aktiv {formatDateTime(s.last_seen_at)} · läuft ab {formatDateTime(s.expires_at)}
          </p>
          {#if !s.current}
            <div class="pt-1"><Button variant="outline" class="h-11" disabled={actions.busy} onclick={() => end(s)}>Sitzung beenden</Button></div>
          {/if}
          {#if actions.notice?.where === `s-${s.id}`}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmOpen}
  title="Alle anderen Sitzungen beenden?"
  description="Alle anderen Geräte werden abgemeldet. Dieses Gerät bleibt angemeldet."
  confirmLabel="Beenden"
  busy={actions.busy}
  onconfirm={endOthers}
/>
