<script lang="ts">
  // I-Anteil des PI-Reglers auf der Raumseite: nur lesen. Zurücksetzen und Setzen gibt es nicht.
  import { shareAsPercent } from '$lib/operations/logic';
  import { lastResetText } from '$lib/rooms/pi';
  import type { PiInfo } from '$lib/rooms/types';
  import HelpTip from './HelpTip.svelte';
  import PiBar from './PiBar.svelte';

  let { pi, timezone }: { pi: PiInfo; timezone: string } = $props();
</script>

<details class="rounded-xl border bg-card p-4 sm:p-5 scroll-mt-20" id="room-pi" data-testid="room-pi" open>
  <summary class="flex cursor-pointer items-center gap-1 text-base font-semibold">PI-Regler: I-Anteil<HelpTip term="pi_integral" /></summary>
  <div class="mt-3 text-3xl leading-none font-semibold tabular-nums" data-testid="room-pi-value">{shareAsPercent(pi.integral)}</div>
  <div class="mt-2"><PiBar percent={pi.integral === null ? null : Number(pi.integral) * 100} /></div>
  <dl class="mt-3 mb-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
    <dt class="text-muted-foreground">Letzter Reset</dt>
    <dd class="m-0" data-testid="room-pi-reset">{lastResetText(pi.last_reset, timezone)}</dd>
  </dl>
  <p class="mt-2 mb-0 text-xs text-muted-foreground">
    Je länger der Raum unter dem Sollwert liegt, desto höher steigt der I-Anteil und desto länger fordert der Regler Wärme an.
    {#if pi.integral === null}Für diesen Raum ist noch kein Wert gespeichert.{/if}
  </p>
</details>
