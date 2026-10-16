<script lang="ts">
  import { Switch } from '$lib/components/ui/switch';
  import { buildMatrix, roomFieldsLocked } from '$lib/access/matrix';
  import type { Grant, PermissionArea, ZoneRef } from '$lib/access/types';

  // Rechte je Anlage und Raum. Reine Darstellung und Eingabe: was gespeichert wird und ob es
  // zulässig ist, entscheidet der Server (PUT .../permissions).
  let {
    areas,
    zones,
    grants,
    disabled = false,
    ontoggle,
    idPrefix = 'perm',
  }: {
    areas: PermissionArea[];
    zones: ZoneRef[];
    grants: Grant[];
    disabled?: boolean;
    ontoggle: (code: string, zoneId: number | null) => void;
    idPrefix?: string;
  } = $props();

  const matrix = $derived(buildMatrix(areas, zones, grants));
</script>

<div class="grid gap-6" data-testid="permission-matrix">
  {#each matrix as area (area.title)}
    <section aria-labelledby={`${idPrefix}-${area.title}`}>
      <h3 id={`${idPrefix}-${area.title}`} class="m-0 text-base">{area.title}</h3>
      <p class="mb-2 mt-0 text-sm text-muted-foreground">{area.hint}</p>
      <ul class="m-0 grid list-none gap-2 p-0">
        {#each area.rows as row (row.code)}
          <li class="rounded-lg border p-3" data-testid="permission-row" data-code={row.code}>
            <div class="flex items-center justify-between gap-3">
              <label for={`${idPrefix}-${row.code}`} class="min-w-0 flex-1 text-sm font-medium">
                {row.description}
              </label>
              <span class="flex shrink-0 items-center gap-2 text-sm">
                <span class="text-muted-foreground" aria-hidden="true">Anlage</span>
                <Switch
                  id={`${idPrefix}-${row.code}`}
                  checked={row.plantWide}
                  {disabled}
                  aria-label={`${row.description} – für die ganze Anlage`}
                  onCheckedChange={() => ontoggle(row.code, null)}
                />
              </span>
            </div>
            {#if row.zoneScoped && row.rooms.length}
              {@const locked = roomFieldsLocked(row, disabled)}
              <fieldset class="m-0 mt-2 border-0 p-0" disabled={locked} data-locked={locked}>
                <legend class="mb-1 p-0 text-xs text-muted-foreground">
                  {#if row.plantWide}Gilt bereits für alle Räume. Einzelne Räume sind dann nicht nötig.{:else}Oder nur für einzelne Räume{/if}
                </legend>
                <div class="flex flex-wrap gap-2">
                  {#each row.rooms as room (room.zoneId)}
                    <label
                      class="inline-flex min-h-10 items-center gap-2 rounded-lg border px-3 text-sm {row.plantWide ? 'opacity-60' : ''}"
                    >
                      <input
                        type="checkbox"
                        class="size-5"
                        checked={room.granted}
                        disabled={locked}
                        onchange={() => ontoggle(row.code, room.zoneId)}
                      />
                      {room.zoneName}
                    </label>
                  {/each}
                </div>
              </fieldset>
            {/if}
          </li>
        {/each}
      </ul>
    </section>
  {/each}
</div>
