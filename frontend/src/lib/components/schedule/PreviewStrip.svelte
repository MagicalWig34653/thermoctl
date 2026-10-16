<script lang="ts">
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatInstant } from '$lib/rooms/logic';
  import { localClock, stripParts } from '$lib/schedule/format';
  import { modeColor } from '$lib/schedule/geometry';
  import type { Preview } from '$lib/schedule/types';

  let { preview, error }: { preview: Preview | null; error: string | null } = $props();
  const parts = $derived(preview ? stripParts(preview.segments) : []);
</script>

<section aria-labelledby="preview-title" class="rounded-xl border bg-card p-4">
  <h2 id="preview-title" class="mb-1 text-lg">Vorschau · nächste {preview?.horizon_hours ?? 24} Stunden</h2>
  <p class="mb-3 text-sm text-muted-foreground">
    Was die Regelung tatsächlich vorsieht: Plan, Urlaub und Übersteuerungen in ihrer Rangfolge.
  </p>
  {#if error && !preview}
    <p class="m-0 text-sm text-destructive" role="alert">{error}</p>
  {:else if !preview}
    <p class="m-0 text-sm text-muted-foreground" role="status">Vorschau wird geladen …</p>
  {:else if !parts.length}
    <p class="m-0 text-sm text-muted-foreground">Keine Schaltzeiten in den nächsten Stunden.</p>
  {:else}
    <div
      class="relative h-9 w-full overflow-hidden rounded-md border bg-muted"
      role="img"
      data-testid="preview-strip"
      aria-label={`Vorschau: ${preview.segments
        .map((s) => `ab ${localClock(s.local_starts_at)} Uhr ${formatCelsius(s.temperature_c)}`)
        .join(', ')}`}
    >
      {#each parts as part, i (i)}
        <span
          class="absolute inset-y-0 grid place-items-center overflow-hidden text-xs font-medium text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.55)]"
          style="left:{part.left}%;width:{part.width}%;background:{modeColor(part.segment.warmth)}"
          title="{localClock(part.segment.local_starts_at)} Uhr · {formatCelsius(part.segment.temperature_c)}"
        >
          {#if part.width > 12}{formatCelsius(part.segment.temperature_c)}{/if}
        </span>
      {/each}
    </div>
    <details class="mt-3 text-sm">
      <summary class="cursor-pointer rounded-md py-1">Als Liste anzeigen</summary>
      <div class="mt-2 overflow-x-auto">
        <table class="w-full min-w-[20rem] text-left text-sm">
          <caption class="sr-only">Sollwerte der nächsten 24 Stunden</caption>
          <thead>
            <tr class="text-muted-foreground">
              <th scope="col" class="py-1 pr-3 font-medium">Ab</th>
              <th scope="col" class="py-1 pr-3 font-medium">Dauer</th>
              <th scope="col" class="py-1 pr-3 font-medium">Sollwert</th>
              <th scope="col" class="py-1 font-medium">Grund</th>
            </tr>
          </thead>
          <tbody>
            {#each preview.segments as s, i (i)}
              <tr class="border-t">
                <td class="py-1 pr-3 whitespace-nowrap">{formatInstant(s.starts_at, preview.timezone)}</td>
                <td class="py-1 pr-3 whitespace-nowrap">{Math.floor(s.duration_minutes / 60)} h {s.duration_minutes % 60} min</td>
                <td class="py-1 pr-3 whitespace-nowrap">{formatCelsius(s.temperature_c)}</td>
                <td class="py-1">{s.mode_name ?? s.reason}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </details>
  {/if}
</section>
