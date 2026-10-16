<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import type { Saver } from '$lib/operations/saver.svelte';

  // Speichern-Zeile: Aktion, dann (mit Abstand) Ergebnis. Fehler und Erfolg stehen unter dem Knopf;
  // `general` ist der Fehlertext für Fehler ohne sichtbares Feld.
  let {
    saver,
    label = 'Speichern',
    busyLabel = 'Wird gespeichert …',
    dirty,
    general = null,
    onsave,
    onreset,
  }: {
    saver: Saver;
    label?: string;
    busyLabel?: string;
    dirty: boolean;
    general?: string | null;
    onsave: () => void;
    onreset?: () => void;
  } = $props();
</script>

<div class="pt-2">
  <div class="flex flex-wrap items-center gap-2">
    <Button type="button" disabled={saver.busy || !dirty} onclick={onsave}>{saver.busy ? busyLabel : label}</Button>
    {#if onreset}<Button type="button" variant="outline" disabled={saver.busy || !dirty} onclick={onreset}>Änderungen verwerfen</Button>{/if}
    {#if dirty && !saver.busy}<span class="text-sm text-muted-foreground">Nicht gespeicherte Änderungen</span>{/if}
  </div>
  <div class="mt-3 space-y-2">
    {#if general}<Notice tone="error">{general}</Notice>{/if}
    {#if saver.failure && !general && saver.failure.kind === 'validation'}
      <Notice tone="error">Bitte die markierten Angaben prüfen. Es wurde nichts gespeichert.</Notice>
    {/if}
    {#if saver.done}<Notice tone="success">{saver.done}</Notice>{/if}
  </div>
</div>
