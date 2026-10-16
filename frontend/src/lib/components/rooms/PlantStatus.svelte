<script lang="ts">
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { appHref } from '$lib/paths';
  import { DESTINATION_PATHS } from '$lib/dashboard/catalog';
  import { formatInstant } from '$lib/rooms/logic';
  import { emergencyRooms, emptyState, homeLine, isSwitching, latches, plantChips, restartRequired } from '$lib/rooms/plant';
  import type { Rooms } from '$lib/rooms/types';
  import HelpTip from './HelpTip.svelte';
  import Notice from './Notice.svelte';

  // Fester Statusbereich oben in der Übersicht: unabhängig vom persönlichen Layout, nicht
  // ausblendbar, nicht Teil des Anpassens. Anlagensicht: Anlagenhinweise. Wohnungssicht:
  // nur die Beruhigungs- bzw. Hinweiszeile. Alles kommt vom Server (/ui-api/rooms).
  let { data }: { data: Rooms } = $props();
  const { prefix } = useApp();

  const plant = $derived(data.plant);
  const home = $derived(data.home);
  const dot = { ok: 'bg-emerald-600', warn: 'bg-amber-500', neutral: 'bg-muted-foreground' };
  const chipCls = {
    warn: 'border-amber-500/50 bg-amber-500/10',
    quiet: 'bg-muted',
    warm: 'border-emerald-600/40 bg-emerald-600/10',
  };
  const rows = $derived(latches(data.actuation));
  const chips = $derived(plant ? plantChips(plant, (iso) => formatInstant(iso, data.timezone)) : []);
  const emergencies = $derived(emergencyRooms(data.rooms));
  const empty = $derived(plant ? emptyState(data.rooms, plant) : null);
  const line = $derived(home ? homeLine(home) : null);
</script>

<section aria-label="Anlagenhinweise" class="space-y-3" data-testid="plant-status">
  {#if plant}
    <div class="rounded-xl border bg-card p-4" data-testid="plant-latches" data-switching={isSwitching(data.actuation)} data-restart-required={restartRequired(data.actuation)}>
      <dl class="m-0 grid gap-2 sm:grid-cols-3">
        {#each rows as row (row.id)}
          <div class="rounded-lg bg-muted/60 px-3 py-2" data-testid={`latch-${row.id}`} data-tone={row.tone}>
            <dt class="flex items-center gap-1 text-xs text-muted-foreground">{row.title}<HelpTip term={row.help} /></dt>
            <dd class="m-0">
              <span class="flex items-center gap-2 text-sm font-medium"><span class="size-2 shrink-0 rounded-full {dot[row.tone]}" aria-hidden="true"></span>{row.value}</span>
              <span class="mt-1 block text-xs text-muted-foreground">{row.text}</span>
            </dd>
          </div>
        {/each}
      </dl>
      {#if chips.length > 0}
        <ul class="m-0 mt-3 flex list-none flex-wrap gap-2 p-0" aria-label="Hinweise zur Anlage">
          {#each chips as chip (chip.id)}
            <li class="rounded-full border px-3 py-1 text-xs {chipCls[chip.tone]}" title={chip.title} data-testid={`chip-${chip.id}`}>
              {#if chip.id === 'vacation'}
                <a class="underline-offset-2 hover:underline" href={appHref(prefix, DESTINATION_PATHS.absence)}>{chip.text}</a>
              {:else}{chip.text}{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    {#each emergencies as e (e.room.id)}
      <div data-testid="emergency-banner"><Notice tone="warn"><strong>{e.room.name}:</strong> {e.headline}<span class="block text-xs text-muted-foreground">{e.detail}</span></Notice></div>
    {/each}

    {#if empty?.show}
      <div class="rounded-xl border bg-card p-6 text-center" data-testid="empty-zones">
        <p class="mb-1 text-base font-medium">Noch keine Zone eingerichtet.</p>
        <p class="mb-3 text-sm text-muted-foreground">Eine Zone ist ein Raum oder eine Gruppe von Räumen mit einem eigenen Sollwert. Ohne mindestens eine gibt es nichts zu regeln.</p>
        {#if empty.canCreate}<Button href={appHref(prefix, '/uebersicht/raeume')} data-testid="create-first-zone">Erste Zone anlegen</Button>{/if}
      </div>
    {/if}
  {:else if line}
    <div data-testid="home-line" data-calm={line.calm}>
      <Notice tone={line.calm ? 'success' : 'warn'}><strong>{line.title}</strong>{#if line.text}<span class="block text-xs text-muted-foreground">{line.text}</span>{/if}</Notice>
    </div>
  {/if}
</section>
