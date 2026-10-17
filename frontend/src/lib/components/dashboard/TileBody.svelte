<script lang="ts">
  import { ArrowUpRight, Cpu, ExternalLink, Flame, ShieldCheck, StickyNote, Thermometer, TriangleAlert } from '@lucide/svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import { appHref } from '$lib/paths';
  import { actuationHeadline, actuationRows } from '$lib/rooms/logic';
  import type { Rooms } from '$lib/rooms/types';
  import HelpTip from '$lib/components/rooms/HelpTip.svelte';
  import HistoryChart from '$lib/components/history/HistoryChart.svelte';
  import RoomCard from '$lib/components/rooms/RoomCard.svelte';
  import ControlTile from './ControlTile.svelte';
  import { DESTINATION_LABELS, DESTINATION_PATHS, entryOf } from '$lib/dashboard/catalog';
  import { agendaItems, metricValue, tileTitle } from '$lib/dashboard/tiles';
  import type { Tile } from '$lib/dashboard/types';
  import HeatingTile from './HeatingTile.svelte';
  import ScheduleTile from './ScheduleTile.svelte';
  import TileCard from './TileCard.svelte';

  let { tile, data, zones, onchanged = () => {} }: { tile: Tile; data: Rooms; zones: { id: number; name: string }[]; onchanged?: () => void } = $props();
  const { prefix } = useApp();

  const room = $derived(tile.zone_id === null ? null : (data.rooms.find((r) => r.id === tile.zone_id) ?? null));
  const title = $derived(tileTitle(tile));
  const dot = { ok: 'bg-emerald-600', warn: 'bg-amber-500', neutral: 'bg-muted-foreground' };
  const chartPeriod = $derived(tile.period_days === 1 ? '24h' : tile.period_days === 7 ? '7d' : '30d');
</script>

{#if tile.kind === 'room'}
  {#if room}<RoomCard {room} heading={tile.title.trim() || undefined} timezone={data.timezone} nowFraction={data.now_fraction} />{/if}
{:else if tile.kind === 'control'}
  {#if room}<ControlTile {room} heading={tile.title.trim() || undefined} timezone={data.timezone} {onchanged} />{/if}
{:else if tile.kind === 'chart'}
  {#if tile.zone_id !== null && zones.some((z) => z.id === tile.zone_id)}
    {#key `${tile.zone_id}|${chartPeriod}`}
      <HistoryChart {zones} zoneId={tile.zone_id} period={chartPeriod} filters={false} compact lazy heading="h3" title={tile.title.trim() || 'Temperaturverlauf'} />
    {/key}
  {/if}
{:else if tile.kind === 'metric'}
  {@const m = metricValue(tile.metric ?? 'rooms', data)}
  <TileCard title={tile.title.trim() || m.title} icon={m.available ? Thermometer : TriangleAlert} testid="tile-metric">
    <div class="text-4xl leading-none font-semibold tracking-tight tabular-nums">{m.value}</div>
    <p class="m-0 text-xs text-muted-foreground">{m.note}</p>
  </TileCard>
{:else if tile.kind === 'agenda'}
  {@const items = agendaItems(data)}
  <TileCard {title} subtitle="Auffälligkeiten und Übersteuerungen" testid="tile-agenda">
    {#if items.length === 0}
      <p class="m-0 text-sm text-muted-foreground">Heute ist nichts Auffälliges zu sehen.</p>
    {:else}
      <ul class="m-0 grid list-none gap-1 p-0">
        {#each items as item, i (`${item.roomId}-${i}`)}
          <li>
            <a class="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-muted" href={appHref(prefix, `/raeume/${item.roomId}`)}>
              {#if item.tone === 'warn'}<TriangleAlert size={16} class="shrink-0 text-destructive" aria-hidden="true" />{:else}<Thermometer size={16} class="shrink-0 text-muted-foreground" aria-hidden="true" />{/if}
              <span class="min-w-0"><strong class="block truncate font-medium">{item.roomName}</strong><span class="block truncate text-xs text-muted-foreground">{item.text}</span></span>
              <ArrowUpRight size={14} class="ml-auto shrink-0 text-muted-foreground" aria-hidden="true" />
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </TileCard>
{:else if tile.kind === 'schedule'}
  {#if room && tile.zone_id !== null}
    {#key tile.zone_id}<ScheduleTile zoneId={tile.zone_id} roomName={room.name} {title} />{/key}
  {/if}
{:else if tile.kind === 'heating'}
  {#if room && tile.zone_id !== null}
    {#key `${tile.zone_id}|${tile.period_days}`}<HeatingTile zoneId={tile.zone_id} roomName={room.name} {title} days={tile.period_days} />{/key}
  {/if}
{:else if tile.kind === 'devices'}
  <TileCard {title} subtitle="Sensoren und Aktoren" icon={Cpu} testid="tile-devices">
    <p class="m-0 text-sm text-muted-foreground">Den Zustand einzelner Geräte zeigt die Geräteseite.</p>
    <Button href={appHref(prefix, DESTINATION_PATHS.devices)} variant="outline" class="mt-auto w-full">Geräte öffnen</Button>
  </TileCard>
{:else if tile.kind === 'system'}
  {@const rows = actuationRows(data.actuation)}
  {@const headline = actuationHeadline(data.actuation)}
  <TileCard {title} subtitle={headline.text} icon={ShieldCheck} testid="tile-system">
    <dl class="m-0 grid gap-2">
      {#each rows as row (row.id)}
        <div class="rounded-lg bg-muted/60 px-3 py-2" data-testid={`tile-system-${row.id}`}>
          <dt class="flex items-center gap-1 text-xs text-muted-foreground">{row.title}<HelpTip term={row.help} /></dt>
          <dd class="m-0 flex items-center gap-2 text-sm font-medium"><span class="size-2 shrink-0 rounded-full {dot[row.tone]}" aria-hidden="true"></span>{row.value}</dd>
        </div>
      {/each}
    </dl>
    <Button href={appHref(prefix, DESTINATION_PATHS.operation)} variant="outline" class="mt-auto w-full">Betrieb öffnen</Button>
  </TileCard>
{:else if tile.kind === 'note'}
  <TileCard title={tile.title.trim() || 'Notiz'} icon={StickyNote} testid="tile-note">
    <!-- Reiner Text: nie als HTML gedeutet. -->
    <p class="m-0 text-sm break-words whitespace-pre-wrap" data-testid="note-text">{tile.note || 'Hier ist Platz für deinen Hinweis.'}</p>
  </TileCard>
{:else if tile.kind === 'shortcut' && tile.destination}
  {@const label = DESTINATION_LABELS[tile.destination]}
  <TileCard title={tile.title.trim() || label} icon={ExternalLink} testid="tile-shortcut">
    <Button href={appHref(prefix, DESTINATION_PATHS[tile.destination])} variant="outline" class="mt-auto w-full">{label} öffnen <ArrowUpRight aria-hidden="true" /></Button>
  </TileCard>
{/if}
