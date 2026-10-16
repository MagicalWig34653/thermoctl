<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';
  import { dayView, dayTimesIntent } from '$lib/schedule/daytimes';
  import { clockValue } from '$lib/schedule/inspector';
  import type { Plan } from '$lib/schedule/types';

  // „Einrichten“ für einen Raum ganz ohne Wochenplan: zwei Zeiten, die wärmere Temperatur
  // zuerst; auf Wunsch gleich für alle Tage. Die Zeiten prüft der Server.
  let {
    plan,
    open = $bindable(false),
    busy,
    onsetup,
    onmessage,
  }: {
    plan: Plan;
    open: boolean;
    busy: boolean;
    onsetup: (first: number, second: number, allDays: boolean) => void;
    onmessage: (text: string) => void;
  } = $props();

  const view = $derived(dayView({ ...plan, points: [] }, 1));
  let first = $state('06:30');
  let second = $state('22:00');
  let allDays = $state(true);
  $effect(() => {
    if (open && view.kind === 'empty') {
      first = clockValue(view.fields[0].minute);
      second = clockValue(view.fields[1].minute);
    }
  });

  function submit() {
    const planned = dayTimesIntent(1, first, second);
    if ('error' in planned || planned.intent.type !== 'dayTimes') {
      onmessage('error' in planned ? planned.error : 'Bitte gültige Uhrzeiten eingeben.');
      return;
    }
    open = false;
    onsetup(planned.intent.first, planned.intent.second, allDays);
  }
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>Wochenplan einrichten</Dialog.Title>
      <Dialog.Description>
        Lege zwei Zeiten fest: ab wann es wärmer wird und ab wann es kühler wird.
      </Dialog.Description>
    </Dialog.Header>
    {#if view.kind === 'empty'}
      <form class="grid gap-4" onsubmit={(e) => { e.preventDefault(); submit(); }}>
        <div class="flex flex-wrap gap-3">
          <label class="grid gap-1 text-sm">
            <span>{view.fields[0].label}</span>
            <input type="time" class="h-11 rounded-lg border bg-background px-3" bind:value={first} required data-testid="setup-first" />
          </label>
          <label class="grid gap-1 text-sm">
            <span>{view.fields[1].label}</span>
            <input type="time" class="h-11 rounded-lg border bg-background px-3" bind:value={second} required data-testid="setup-second" />
          </label>
        </div>
        <label class="flex min-h-11 items-center gap-3 text-sm">
          <input type="checkbox" class="size-5" bind:checked={allDays} />Für alle Tage der Woche übernehmen
        </label>
        <Dialog.Footer>
          <Button type="button" variant="outline" onclick={() => (open = false)}>Abbrechen</Button>
          <Button type="submit" disabled={busy} data-testid="setup-submit">Einrichten</Button>
        </Dialog.Footer>
      </form>
    {/if}
  </Dialog.Content>
</Dialog.Root>
