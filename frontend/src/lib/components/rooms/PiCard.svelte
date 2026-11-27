<script lang="ts">
  // I-Anteil des PI-Reglers auf der Raumseite. Verwalter können ihn zurücksetzen oder setzen;
  // beides ist nur eine Anforderung, der nächste Regelzyklus übernimmt sie (Schaltprotokoll:
  // Grund „manuell_reset“ / „manuell_gesetzt“). Die Bestätigung ist ein eigener Schritt.
  import { Button } from '$lib/components/ui/button';
  import { shareAsPercent } from '$lib/operations/logic';
  import { lastResetText, parseIntegralPercent, pendingText } from '$lib/rooms/pi';
  import type { PiInfo } from '$lib/rooms/types';
  import HelpTip from './HelpTip.svelte';
  import Notice from './Notice.svelte';
  import PiBar from './PiBar.svelte';

  let {
    pi,
    timezone,
    canEdit = false,
    busy = false,
    error = null,
    done = null,
    onReset,
    onSet,
  }: {
    pi: PiInfo;
    timezone: string;
    canEdit?: boolean;
    busy?: boolean;
    error?: string | null;
    done?: string | null;
    onReset?: () => Promise<boolean>;
    onSet?: (value: string) => Promise<boolean>;
  } = $props();

  let mode = $state<'reset' | 'set' | null>(null);
  let draft = $state('');
  const parsed = $derived(parseIntegralPercent(draft));
  const draftError = $derived(draft.trim() !== '' && !parsed.ok ? parsed.message : null);

  function open(next: 'reset' | 'set') {
    mode = mode === next ? null : next;
    draft = '';
  }

  async function confirm() {
    const ok = mode === 'reset' ? await onReset?.() : parsed.ok ? await onSet?.(parsed.value) : false;
    if (ok) mode = null;
  }
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

  {#if pi.pending}
    <div class="mt-3" data-testid="room-pi-pending"><Notice tone="info">{pendingText(pi.pending)}</Notice></div>
  {/if}

  {#if canEdit}
    <div class="mt-3 flex flex-wrap gap-2">
      <Button size="sm" variant="outline" disabled={busy} aria-expanded={mode === 'reset'} data-testid="room-pi-reset-open" onclick={() => open('reset')}>I zurücksetzen</Button>
      <Button size="sm" variant="outline" disabled={busy} aria-expanded={mode === 'set'} data-testid="room-pi-set-open" onclick={() => open('set')}>I setzen</Button>
    </div>

    {#if mode === 'reset'}
      <div class="mt-3 space-y-2" data-testid="room-pi-confirm">
        <Notice tone="warn">
          I-Anteil auf 0 % zurücksetzen? Der Regler beginnt im nächsten Zyklus wieder bei 0 und der Raum heizt anfangs weniger lang. Der Eingriff wird im Audit-Protokoll und im Schaltprotokoll festgehalten.
        </Notice>
        <div class="flex gap-2">
          <Button size="sm" disabled={busy} data-testid="room-pi-confirm-go" onclick={confirm}>Zurücksetzen</Button>
          <Button size="sm" variant="outline" onclick={() => (mode = null)}>Abbrechen</Button>
        </div>
      </div>
    {:else if mode === 'set'}
      <div class="mt-3 space-y-2" data-testid="room-pi-confirm">
        <label class="block text-sm" for="room-pi-value-input">Neuer I-Anteil in Prozent (0 bis 100)</label>
        <input
          id="room-pi-value-input"
          class="h-10 w-40 rounded-lg border bg-background px-3 text-sm aria-[invalid=true]:border-destructive"
          inputmode="decimal"
          autocomplete="off"
          bind:value={draft}
          aria-invalid={draftError !== null}
          aria-describedby={draftError ? 'room-pi-value-error' : undefined}
        />
        {#if draftError}<p id="room-pi-value-error" class="m-0 text-sm text-destructive">{draftError}</p>{/if}
        <Notice tone="warn">
          Der Regler rechnet im nächsten Zyklus mit diesem Wert weiter. Der Eingriff wird im Audit-Protokoll und im Schaltprotokoll festgehalten.
        </Notice>
        <div class="flex gap-2">
          <Button size="sm" disabled={busy || !parsed.ok} data-testid="room-pi-confirm-go" onclick={confirm}>Setzen</Button>
          <Button size="sm" variant="outline" onclick={() => (mode = null)}>Abbrechen</Button>
        </div>
      </div>
    {/if}
    <div class="mt-3 space-y-2">
      {#if error}<Notice tone="error">{error}</Notice>{/if}
      {#if done}<Notice tone="success">{done}</Notice>{/if}
    </div>
  {/if}
</details>
