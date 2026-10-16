<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import { dayName, dayTimesIntent, dayView } from '$lib/schedule/daytimes';
  import { clockValue } from '$lib/schedule/inspector';
  import type { Intent } from '$lib/schedule/intents';
  import type { Plan } from '$lib/schedule/types';

  // Vereinfachtes Bearbeiten eines Tages mit zwei Feldern („Warm ab“ / „Nacht ab“). Der Editor
  // darüber bleibt unverändert nutzbar; ob die Zeiten zulässig sind, entscheidet der Server.
  let {
    plan,
    activeDay,
    busy,
    onintent,
    onmessage,
  }: {
    plan: Plan;
    activeDay: number;
    busy: boolean;
    onintent: (intent: Intent) => void;
    onmessage: (text: string) => void;
  } = $props();

  const view = $derived(dayView(plan, activeDay));
  let first = $state('');
  let second = $state('');
  // Felder folgen dem Serverstand des gewählten Tages.
  $effect(() => {
    if (view.kind === 'empty' || view.kind === 'two') {
      first = clockValue(view.fields[0].minute);
      second = clockValue(view.fields[1].minute);
    }
  });

  function save() {
    const planned = dayTimesIntent(activeDay, first, second);
    if ('error' in planned) onmessage(planned.error);
    else onintent(planned.intent);
  }
</script>

<section aria-labelledby="daytimes-title" class="rounded-xl border bg-card p-4" data-testid="day-times">
  <h2 id="daytimes-title" class="mb-1 text-lg">{dayName(activeDay)}: zwei Zeiten</h2>
  {#if view.kind === 'empty' || view.kind === 'two'}
    <p class="m-0 mb-3 text-sm text-muted-foreground">
      {view.kind === 'empty'
        ? 'Für diesen Tag gibt es noch keine Schaltzeit. Mit zwei Zeiten richtest du ihn ein.'
        : 'Wann soll es wärmer und wann kühler werden? Der Tag wechselt zwischen diesen beiden Zeiten.'}
    </p>
    <form class="flex flex-wrap items-end gap-3" onsubmit={(e) => { e.preventDefault(); save(); }}>
      <label class="grid gap-1 text-sm">
        <span>{view.fields[0].label}</span>
        <input type="time" class="h-11 rounded-lg border bg-background px-3" bind:value={first} required />
      </label>
      <label class="grid gap-1 text-sm">
        <span>{view.fields[1].label}</span>
        <input type="time" class="h-11 rounded-lg border bg-background px-3" bind:value={second} required />
      </label>
      <Button type="submit" disabled={busy}>{view.kind === 'empty' ? 'Einrichten' : 'Speichern'}</Button>
    </form>
  {:else if view.kind === 'unavailable'}
    <p class="m-0 text-sm text-muted-foreground">
      Für diesen Raum sind noch keine zwei Temperaturen hinterlegt — ohne sie gibt es nichts, wozwischen ein Tag umschalten könnte.
    </p>
  {:else}
    <p class="m-0 text-sm text-muted-foreground">
      Dieser Tag hat {view.count} {view.count === 1 ? 'Schaltzeit' : 'Schaltzeiten'} und lässt sich hier nicht vereinfacht bearbeiten. Nutze dafür den Wochenplan oben.
    </p>
  {/if}
</section>
