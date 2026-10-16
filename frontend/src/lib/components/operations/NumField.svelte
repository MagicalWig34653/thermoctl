<script lang="ts">
  import { Input } from '$lib/components/ui/input';
  import OpsHelp from './OpsHelp.svelte';

  // Beschriftetes Zahlenfeld: Einheit, Hinweis und Feldfehler stehen am Feld.
  let {
    id,
    label,
    value = $bindable(''),
    unit,
    help,
    hint,
    error = null,
    placeholder,
    disabled = false,
    inputmode = 'decimal',
    type = 'text',
  }: {
    id: string;
    label: string;
    value?: string;
    unit?: string | null;
    help?: string;
    hint?: string | null;
    error?: string | null;
    placeholder?: string;
    disabled?: boolean;
    inputmode?: 'decimal' | 'numeric' | 'text';
    type?: 'text';
  } = $props();
  const describedBy = $derived([hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined);
</script>

<div>
  <div class="mb-1 flex min-h-8 items-center gap-1">
    <label class="text-sm font-medium" for={id}>{label}</label>
    {#if help}<OpsHelp term={help} />{/if}
  </div>
  <div class="relative">
    <Input
      {id}
      {type}
      {inputmode}
      {placeholder}
      {disabled}
      autocomplete="off"
      class="h-10 w-full {unit ? 'pr-14' : ''}"
      bind:value
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={describedBy}
    />
    {#if unit}<span class="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted-foreground" aria-hidden="true">{unit}</span>{/if}
  </div>
  {#if hint}<p id={`${id}-hint`} class="mt-1 mb-0 text-xs text-muted-foreground">{hint}</p>{/if}
  {#if error}<p id={`${id}-error`} class="mt-1 mb-0 text-sm text-destructive">{error}</p>{/if}
</div>
