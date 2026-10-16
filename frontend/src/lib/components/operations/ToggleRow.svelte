<script lang="ts">
  import { Switch } from '$lib/components/ui/switch';
  import OpsHelp from './OpsHelp.svelte';

  // Schalter mit sichtbarer Beschriftung (kein Kästchen ohne Text).
  let {
    id,
    label,
    description,
    checked = $bindable(false),
    help,
    disabled = false,
    error = null,
  }: { id: string; label: string; description?: string; checked?: boolean; help?: string; disabled?: boolean; error?: string | null } = $props();
</script>

<div>
  <div class="flex items-center justify-between gap-3 rounded-lg bg-muted/60 px-3 py-2">
    <div class="min-w-0">
      <div class="flex items-center gap-1">
        <label class="text-sm font-medium" for={id}>{label}</label>
        {#if help}<OpsHelp term={help} />{/if}
      </div>
      {#if description}<p id={`${id}-desc`} class="mb-0 text-xs text-muted-foreground">{description}</p>{/if}
    </div>
    <Switch {id} bind:checked {disabled} aria-describedby={description ? `${id}-desc` : undefined} aria-invalid={error ? 'true' : undefined} />
  </div>
  {#if error}<p class="mt-1 mb-0 text-sm text-destructive" id={`${id}-error`}>{error}</p>{/if}
</div>
