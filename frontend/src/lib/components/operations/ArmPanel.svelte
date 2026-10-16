<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { armOutcome, errorAt, SENDING_LATCH_NOTE, type ArmOutcome } from '$lib/operations/logic';
  import { createSaver } from '$lib/operations/saver.svelte';
  import type { ArmedResult, Operation } from '$lib/operations/types';
  import type { OperationsClient } from '$lib/operations/client';
  import OpsHelp from './OpsHelp.svelte';

  // Freigabe ändern. Scharfschalten verlangt eine Begründung und eine ausdrückliche Bestätigung;
  // das Ergebnis wird aus der Serverantwort gelesen, nicht aus dem, was angefragt wurde.
  // Es gibt keinen zweiten, automatischen Versuch.
  let {
    operation,
    client,
    onresult,
  }: { operation: Operation; client: OperationsClient; onresult: (result: ArmedResult) => void } = $props();

  const saver = createSaver();
  let open = $state(false);
  let reason = $state('');
  let understood = $state(false);
  let outcome = $state<ArmOutcome | null>(null);
  const armed = $derived(operation.actuation.armed);
  // Die Richtung der Anfrage: aus dem gespeicherten Stand, nicht aus einem lokalen Schalter.
  const target = $derived(!armed);
  const canSend = $derived(!saver.busy && (!target || (reason.trim() !== '' && understood)));

  function start() {
    outcome = null;
    saver.clear();
    open = true;
  }
  function cancel() {
    open = false;
    reason = '';
    understood = false;
    saver.clear();
  }
  async function submit() {
    if (!canSend) return;
    const requested = target;
    const result = await saver.run(
      () => client.setArmed(requested, reason.trim()),
      () => null,
    );
    if (result) {
      outcome = armOutcome(requested, result);
      onresult(result);
      open = false;
      reason = '';
      understood = false;
    } else if (saver.failure?.field === 'reason') {
      document.getElementById('arm-reason')?.focus();
    }
  }
</script>

<section class="max-w-[820px] rounded-xl border bg-card p-5" aria-labelledby="arm-title" data-testid="arm-panel">
  <h2 id="arm-title" class="mb-1">Freigabe ändern</h2>
  <p class="mb-4 text-sm text-muted-foreground">
    Das ändert nur die gespeicherte Freigabe. Die Sendefreigabe ändert sich dadurch nicht. <span data-testid="arm-latch-note">{SENDING_LATCH_NOTE}</span>
  </p>

  {#if !open}
    <Button variant={armed ? 'outline' : 'default'} onclick={start} data-testid="arm-start">
      {armed ? 'Freigabe zurücknehmen …' : 'Freigabe erteilen …'}
    </Button>
  {:else}
    <form
      class="space-y-4 rounded-lg border p-4"
      onsubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      novalidate
      data-testid="arm-form"
    >
      <h3 class="text-base">{target ? 'Regelung scharf schalten' : 'Zurück in den Trockenlauf'}</h3>
      {#if target}
        <Notice tone="warn">
          Nach der Freigabe sendet die Regelung echte Befehle an die Heizung, sobald der Sende-Riegel offen ist – für MQTT und für Meross. Das geschieht erst nach einem Neustart des Dienstes.
        </Notice>
      {/if}
      <div>
        <div class="mb-1 flex min-h-8 items-center gap-1">
          <label class="text-sm font-medium" for="arm-reason">Begründung{target ? ' (Pflicht)' : ' (optional)'}</label><OpsHelp term="arm_reason" />
        </div>
        <textarea
          id="arm-reason"
          rows="3"
          class="w-full rounded-lg border bg-background px-3 py-2 text-sm aria-[invalid=true]:border-destructive"
          bind:value={reason}
          aria-invalid={errorAt(saver.failure, 'reason') ? 'true' : undefined}
          aria-describedby={errorAt(saver.failure, 'reason') ? 'arm-reason-error' : undefined}
          placeholder={target ? 'Worauf stützt sich die Freigabe?' : 'Warum zurück in den Trockenlauf?'}
        ></textarea>
        {#if errorAt(saver.failure, 'reason')}<p id="arm-reason-error" class="mt-1 mb-0 text-sm text-destructive">{errorAt(saver.failure, 'reason')}</p>{/if}
      </div>
      {#if target}
        <label class="flex min-h-10 items-start gap-2 text-sm">
          <input type="checkbox" class="mt-1 size-5" bind:checked={understood} data-testid="arm-understood" />
          Ich habe verstanden, dass die Regelung danach nach einem Neustart echte Heizbefehle senden kann.
        </label>
      {/if}
      <div class="flex flex-wrap gap-2">
        <Button type="submit" disabled={!canSend} variant={target ? 'default' : 'outline'} data-testid="arm-confirm">
          {saver.busy ? 'Wird gespeichert …' : target ? 'Freigabe speichern' : 'Freigabe zurücknehmen'}
        </Button>
        <Button type="button" variant="ghost" disabled={saver.busy} onclick={cancel}>Abbrechen</Button>
      </div>
      {#if saver.failure && errorAt(saver.failure, 'reason') === null}
        <Notice tone="error">{saver.failure.message}</Notice>
      {/if}
    </form>
  {/if}

  {#if outcome}
    <div class="mt-4" data-testid="arm-outcome"><Notice tone={outcome.tone === 'warn' ? 'warn' : outcome.tone === 'success' ? 'success' : 'info'}>{outcome.text}</Notice></div>
  {/if}
</section>
