<script lang="ts">
  import FormCard from './FormCard.svelte';
  import NumField from './NumField.svelte';
  import SaveRow from './SaveRow.svelte';
  import { createSaver } from '$lib/operations/saver.svelte';
  import {
    dirtyFields, draftFromFields, errorAt, generalError, thresholdsPayload,
  } from '$lib/operations/logic';
  import type { NumberField, Settings } from '$lib/operations/types';
  import type { ThresholdBody } from '$lib/operations/client';

  // Eine Gruppe von Schwellen mit eigenem Speichern (je Gruppe ein Endpunkt). Der Server prüft
  // die Grenzen; hier stehen Beschriftung, Erlaubt-Hinweis und Feldfehler.
  let {
    id,
    title,
    description,
    help,
    fields,
    pick,
    canEdit,
    save,
    onsaved,
  }: {
    id: string;
    title: string;
    description: string;
    help?: string;
    fields: NumberField[];
    /** Welche Felder die Antwort des Servers für diese Gruppe trägt. */
    pick: (settings: Settings) => NumberField[];
    canEdit: boolean;
    save: (body: ThresholdBody) => Promise<Settings>;
    onsaved: (next: Settings) => void;
  } = $props();

  const saver = createSaver();
  // Erstbefüllung beim Anlegen; spätere Änderungen am Prop überschreiben keine Eingaben.
  // svelte-ignore state_referenced_locally
  let draft = $state<Record<string, string>>(draftFromFields(fields));
  const dirty = $derived(dirtyFields(fields, draft).length > 0);
  const names = $derived(new Set(fields.map((f) => f.name)));

  async function submit() {
    const result = await saver.run(
      () => save(thresholdsPayload(fields, draft)),
      () => `${title} gespeichert.`,
    );
    if (result) {
      onsaved(result);
      draft = draftFromFields(pick(result));
    } else if (saver.failure?.field) {
      document.getElementById(`${id}-${saver.failure.field}`)?.focus();
    }
  }
</script>

<FormCard {id} {title} {description} {help}>
  {#each fields as field (field.name)}
    <NumField
      id={`${id}-${field.name}`}
      label={field.label}
      bind:value={draft[field.name]}
      inputmode={field.integer ? 'numeric' : 'decimal'}
      disabled={!canEdit}
      hint={`Erlaubt: ${field.minimum.replace('.', ',')} bis ${field.maximum.replace('.', ',')}`}
      error={errorAt(saver.failure, field.name)}
    />
  {/each}
  {#if canEdit}
    <SaveRow
      {saver}
      {dirty}
      onsave={submit}
      general={generalError(saver.failure, (f) => names.has(f))}
    />
  {:else}
    <p class="text-sm text-muted-foreground">Du kannst diese Werte ansehen, aber nicht ändern.</p>
  {/if}
</FormCard>
