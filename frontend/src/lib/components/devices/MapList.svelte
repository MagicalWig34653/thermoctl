<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import type { MapActions } from '$lib/devices/map-actions.svelte';
  import { rejectionReason, undrawnConnections } from '$lib/devices/map-logic';
  import type { DeviceMap, MapPort } from '$lib/devices/types';

  // Listen- und Formularalternative zur Karte: dieselben Prüf- und Schreibaufrufe
  // (`check`, dann `connect`/`disconnect`), nur ohne Zeigen und Ziehen.
  let { map, actions }: { map: DeviceMap; actions: MapActions } = $props();

  let from = $state('');
  let to = $state('');
  let confirming = $state<string | null>(null);

  const usable = $derived(map.nodes.map((n) => ({ ...n, ports: n.ports.filter((p) => !p.technical) })).filter((n) => n.ports.length));
  const all = $derived(new Map(map.nodes.flatMap((n) => n.ports).map((p) => [p.id, p] as const)));
  const first = $derived(from ? (all.get(from) ?? null) : null);
  const hint = $derived.by(() => {
    const a = from ? all.get(from) : undefined;
    const b = to ? all.get(to) : undefined;
    return a && b ? rejectionReason(map, a, b) : null;
  });
  const loose = $derived(undrawnConnections(map));

  const describe = (p: MapPort) => `${p.direction === 'out' ? 'Ausgang' : 'Eingang'}: ${p.label}`;

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (!from || !to) return;
    const ok = await actions.connect(from, to);
    if (ok) {
      from = '';
      to = '';
    }
  }
  async function remove(id: string) {
    if (confirming !== id) {
      confirming = id;
      return;
    }
    confirming = null;
    await actions.disconnect(id);
  }
</script>

<div class="grid gap-6" data-testid="map-list">
  {#if actions.notice}<Notice tone={actions.notice.tone}>{actions.notice.text}</Notice>{/if}

  <section aria-labelledby="conn-title">
    <h2 id="conn-title" class="m-0">Verbindungen</h2>
    {#if map.connections.length}
      <ul class="m-0 mt-2 grid list-none gap-2 p-0">
        {#each map.connections as c (c.id)}
          <li class="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2" data-testid="connection-row">
            <span class="min-w-0">{c.title}{#if !c.out_port || !c.in_port}<span class="text-sm text-muted-foreground"> (auf der Karte nicht gezeichnet)</span>{/if}</span>
            {#if c.editable && map.may_edit}
              <Button variant={confirming === c.id ? 'destructive' : 'outline'} size="sm" disabled={actions.busy} onclick={() => remove(c.id)}>
                {confirming === c.id ? 'Wirklich trennen' : 'Trennen'}
              </Button>
            {/if}
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-muted-foreground">Noch keine Verbindungen.</p>
    {/if}
    {#if loose.length}<p class="mt-2 text-sm text-muted-foreground">Verbindungen ohne passenden Anschluss am Gerät sind hier lösbar, auf der Karte aber nicht zu sehen.</p>{/if}
  </section>

  {#if map.may_edit}
    <form class="grid gap-3 rounded-xl border bg-card p-4" onsubmit={submit} aria-labelledby="new-title">
      <h2 id="new-title" class="m-0 text-base">Verbindung anlegen</h2>
      <p class="m-0 text-sm text-muted-foreground">Werte fließen vom Ausgang zum Eingang. Ob beide zusammenpassen, prüft der Server.</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="grid gap-1 text-sm"><span>Erster Anschluss</span>
          <select class="h-11 rounded-lg border bg-background px-3" bind:value={from} required>
            <option value="">Bitte wählen</option>
            {#each usable as n (n.id)}<optgroup label={n.name}>{#each n.ports as p (p.id)}<option value={p.id}>{describe(p)}</option>{/each}</optgroup>{/each}
          </select></label>
        <label class="grid gap-1 text-sm"><span>Zweiter Anschluss</span>
          <select class="h-11 rounded-lg border bg-background px-3" bind:value={to} required>
            <option value="">Bitte wählen</option>
            {#each usable as n (n.id)}<optgroup label={n.name}>{#each n.ports as p (p.id)}<option value={p.id}>{describe(p)}{first !== null && first.id !== p.id && rejectionReason(map, first, p) !== null ? ' – passt nicht' : ''}</option>{/each}</optgroup>{/each}
          </select></label>
      </div>
      {#if hint}<p class="m-0 text-sm text-destructive" role="status">{hint}</p>{/if}
      <div><Button type="submit" disabled={actions.busy || !from || !to}>Prüfen und verbinden</Button></div>
    </form>
  {:else}
    <Notice tone="info">Für das Verbinden fehlt die Berechtigung; die Liste ist nur zur Ansicht.</Notice>
  {/if}
</div>
