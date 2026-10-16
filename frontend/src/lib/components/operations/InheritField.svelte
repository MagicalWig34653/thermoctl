<script lang="ts">
  import { Badge } from '$lib/components/ui/badge';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { inheritanceNote, outsideLimit, toInput } from '$lib/operations/logic';
  import type { Parameter } from '$lib/operations/types';
  import OpsHelp from './OpsHelp.svelte';

  // Vererbbarer Raumwert: Eigenwert und wirksamer Wert getrennt sichtbar. Leer = erbt.
  let {
    id,
    param,
    value = $bindable(''),
    help,
    error = null,
    disabled = false,
  }: { id: string; param: Parameter; value?: string; help?: string; error?: string | null; disabled?: boolean } = $props();

  const unit = $derived(param.limit.unit ?? '');
  const isOwn = $derived(value.trim() !== '');
  const outside = $derived(outsideLimit(value, param.limit));
  const describedBy = $derived(`${id}-note${error ? ` ${id}-error` : ''}`);
</script>

<div data-testid={`field-${id}`}>
  <div class="mb-1 flex min-h-8 flex-wrap items-center gap-1">
    <label class="text-sm font-medium" for={id}>{param.limit.label}</label>
    {#if help}<OpsHelp term={help} />{/if}
    <Badge variant={isOwn ? 'default' : 'secondary'} class="ml-1">{isOwn ? 'Eigenwert' : 'Erbt'}</Badge>
  </div>
  <div class="flex items-center gap-2">
    <div class="relative min-w-0 flex-1">
    <Input
      {id}
      inputmode="decimal"
      autocomplete="off"
      class="h-10 w-full {unit ? 'pr-14' : ''}"
      bind:value
      {disabled}
      placeholder={toInput(param.effective)}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={describedBy}
    />
    {#if unit}<span class="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted-foreground" aria-hidden="true">{unit}</span>{/if}
    </div>
    <Button type="button" variant="ghost" size="sm" class="min-h-10" disabled={disabled || !isOwn} onclick={() => (value = '')} aria-label={`${param.limit.label}: Eigenwert entfernen, Wert erben`}>Erben</Button>
  </div>
  <p id={`${id}-note`} class="mt-1 mb-0 text-xs text-muted-foreground">
    Wirksam: <strong class="text-foreground">{toInput(param.effective)}{unit ? ` ${unit}` : ''}</strong> · {inheritanceNote(param.source, param.inherited)}
    · Erlaubt: {toInput(param.limit.minimum)} bis {toInput(param.limit.maximum)}
  </p>
  {#if outside && !error}<p id={`${id}-warn`} class="mt-1 mb-0 text-sm text-amber-700 dark:text-amber-400" data-testid={`${id}-outside`}>Außerhalb des genannten Bereichs ({toInput(param.limit.minimum)} bis {toInput(param.limit.maximum)}). Bitte prüfen, bevor du speicherst.</p>{/if}
  {#if error}<p id={`${id}-error`} class="mt-1 mb-0 text-sm text-destructive">{error}</p>{/if}
</div>
