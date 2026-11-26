<script lang="ts">
  // Kachel „Regler“: I-Anteil je Raum, nur lesend; ein Klick führt zur Raumtechnik.
  import { Gauge } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { controllerRows, lastResetText } from '$lib/rooms/pi';
  import type { Room } from '$lib/rooms/types';
  import PiBar from '$lib/components/rooms/PiBar.svelte';
  import TileCard from './TileCard.svelte';

  let { rooms, timezone, title }: { rooms: Room[]; timezone: string; title: string } = $props();
  const { prefix } = useApp();
  const rows = $derived(controllerRows(rooms));
</script>

<TileCard {title} subtitle="I-Anteil des PI-Reglers" icon={Gauge} testid="tile-controller">
  {#if rows.length === 0}
    <p class="m-0 text-sm text-muted-foreground">Kein Raum mit PI-Regler.</p>
  {:else}
    <ul class="m-0 grid list-none gap-1 p-0">
      {#each rows as row (row.id)}
        <li>
          <a class="grid gap-1 rounded-lg px-2 py-1.5 hover:bg-muted" href={appHref(prefix, row.path)} data-testid={`controller-row-${row.id}`}>
            <span class="flex items-baseline justify-between gap-3 text-sm">
              <strong class="min-w-0 truncate font-medium">{row.name}</strong>
              <span class="shrink-0 tabular-nums">{row.text}</span>
            </span>
            <PiBar percent={row.percent} />
            <span class="text-xs text-muted-foreground">Letzter Reset: {lastResetText(row.reset, timezone)}</span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</TileCard>
