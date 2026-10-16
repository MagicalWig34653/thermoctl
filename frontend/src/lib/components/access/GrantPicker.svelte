<script lang="ts">
  import { toggleGrant, keyOf } from '$lib/access/matrix';
  import type { AvailableGrant, Grant } from '$lib/access/types';

  // Auswahl der Rechte eines Tokens aus dem, was die ausstellende Person selbst hält
  // (`available` vom Server). Der Server prüft jede Auswahl erneut (permission_not_held).
  let {
    available,
    selected = $bindable(),
    zoneName,
    idPrefix,
    disabled = false,
  }: {
    available: AvailableGrant[];
    selected: Grant[];
    zoneName: (id: number) => string;
    idPrefix: string;
    disabled?: boolean;
  } = $props();

  // Bereits gewählte Rechte, die man selbst nicht (mehr) hält, bleiben sichtbar und abwählbar.
  const items = $derived.by(() => {
    const have = new Set(available.map(keyOf));
    const extra = selected
      .filter((g) => !have.has(keyOf(g)))
      .map((g) => ({ ...g, description: g.code, foreign: true }));
    return [...available.map((a) => ({ ...a, foreign: false })), ...extra];
  });
  const isOn = (g: Grant) => selected.some((s) => keyOf(s) === keyOf(g));
</script>

<fieldset class="m-0 grid gap-1 border-0 p-0" {disabled}>
  <legend class="mb-1 p-0 text-sm font-medium">Rechte</legend>
  {#if !items.length}
    <p class="m-0 text-sm text-muted-foreground">Du hast selbst keine Rechte, die sich weitergeben lassen.</p>
  {/if}
  {#each items as g (keyOf(g))}
    <label class="flex min-h-10 items-start gap-2 rounded-md px-1 py-1 text-sm">
      <input
        type="checkbox"
        class="mt-0.5 size-5 shrink-0"
        id={`${idPrefix}-${keyOf(g)}`}
        checked={isOn(g)}
        onchange={() => (selected = toggleGrant(selected, g.code, g.zone_id))}
      />
      <span>
        {g.description}{#if g.zone_id !== null}<span class="text-muted-foreground"> · nur {zoneName(g.zone_id)}</span>{/if}
        {#if g.foreign}<span class="text-destructive"> · dieses Recht hast du nicht (mehr)</span>{/if}
      </span>
    </label>
  {/each}
</fieldset>
