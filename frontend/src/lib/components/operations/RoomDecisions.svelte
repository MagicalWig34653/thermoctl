<script lang="ts">
  import { appHref } from '$lib/paths';
  import { useApp } from '$lib/context';
  import { formatCelsius } from '$lib/rooms/decimal';
  import { formatInstant } from '$lib/rooms/logic';
  import { actuatorCycle, decisionEffect, sensorTimeoutText } from '$lib/operations/logic';
  import type { Operation } from '$lib/operations/types';
  import Notice from '$lib/components/rooms/Notice.svelte';

  // Letzte Regelentscheidung je Raum mit Begründung. Eine Entscheidung ist kein Schaltzustand;
  // ob sie ausgeführt wird, folgt allein aus den Zuständen der Anlage.
  let { operation, timezone }: { operation: Operation; timezone: string } = $props();
  const { prefix } = useApp();
  const effect = $derived(decisionEffect(operation.actuation));
</script>

<section aria-labelledby="decisions-title" data-testid="decisions">
  <h2 id="decisions-title" class="mb-1">Entscheidungen je Raum</h2>
  <p class="mb-4 text-sm text-muted-foreground">Warum geschaltet wurde oder nicht. {effect.text}</p>
  {#if operation.rooms.length === 0}
    <p class="text-muted-foreground">Keine Räume sichtbar.</p>
  {:else}
    <ul class="m-0 grid list-none gap-3 p-0 lg:grid-cols-2">
      {#each operation.rooms as room (room.id)}
        {@const d = room.decision}
        {@const e = room.emergency}
        <li class="rounded-xl border bg-card p-4" data-testid={`decision-${room.id}`}>
          <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3><a class="hover:underline" href={appHref(prefix, `/raeume/${room.id}`)}>{room.name}</a></h3>
            <span class="text-sm tabular-nums text-muted-foreground">Ist {formatCelsius(room.temperature_c)} · Soll {formatCelsius(room.setpoint_c)}</span>
          </div>
          {#if d}
            <p class="mb-1 text-sm font-medium">
              {d.would_heat ? 'Entscheidung: heizen' : 'Entscheidung: nicht heizen'}
              <span class="font-normal text-muted-foreground">({effect.executes ? 'wird ausgeführt' : 'wird nicht ausgeführt'})</span>
            </p>
            <p class="mb-3 text-sm" data-testid={`reason-${room.id}`}>{d.reason}</p>
            <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt class="text-muted-foreground">Entschieden</dt><dd class="m-0">{formatInstant(d.decided_at, timezone)}</dd>
              <dt class="text-muted-foreground">Sollwert aus</dt><dd class="m-0">{d.setpoint_reason}</dd>
              {#if d.planned_setpoint_c !== null}<dt class="text-muted-foreground">Plan</dt><dd class="m-0 tabular-nums">{formatCelsius(d.planned_setpoint_c)}</dd>{/if}
              {#if d.solar_setback_k !== null}<dt class="text-muted-foreground">Sonnenabsenkung</dt><dd class="m-0 tabular-nums">{d.solar_setback_k.replace('.', ',')} K</dd>{/if}
              <dt class="text-muted-foreground">Regler</dt>
              <dd class="m-0">{d.effective_controller}{d.requested_controller !== d.effective_controller ? ` (gewünscht: ${d.requested_controller})` : ''}</dd>
              {#if d.controller_fallback_label}<dt class="text-muted-foreground">Rückfall</dt><dd class="m-0">{d.controller_fallback_label}</dd>{/if}
              <dt class="text-muted-foreground">Ergebniscode</dt><dd class="m-0 font-mono text-xs">{d.outcome_code}</dd>
            </dl>
          {:else}
            <p class="mb-3 text-sm text-muted-foreground">Noch keine Entscheidung.</p>
          {/if}

          <div class="mt-3 border-t pt-3" data-testid={`emergency-${room.id}`}>
            <h4 class="mb-1 text-sm font-semibold">Notbetrieb: {e.stage_label}</h4>
            {#if e.stage !== 'normal'}
              <Notice tone="warn">
                {#if e.failure_started_at}Seit {formatInstant(e.failure_started_at, timezone)}. {/if}
                {#if e.active_source_device_name}Ersatzquelle: {e.active_source_device_name}. {/if}
                {#if e.emergency_setpoint_c !== null}Notsollwert {formatCelsius(e.emergency_setpoint_c)}. {/if}
                {#if e.outdoor_status_text}Außentemperatur: {e.outdoor_status_text}{e.outdoor_temperature_c !== null ? ` (${formatCelsius(e.outdoor_temperature_c)})` : ''}. {/if}
                {#if e.recovery_samples !== null}Rückkehr: {e.recovery_sample_count} von {e.recovery_samples} guten Messwerten.{/if}
              </Notice>
              {@const timeout = sensorTimeoutText(e.sensor_timeout_seconds)}
              {#if timeout}
                <p class="mt-2 mb-0 text-sm" data-testid={`emergency-timeout-${room.id}`}>
                  <span class="text-muted-foreground">Sensor-Timeout</span> {timeout}
                </p>
              {/if}
              {#if e.actuators.length}
                <ul class="mt-3 mb-0 list-none space-y-2 p-0 text-sm" aria-label="Aktoren im Notbetrieb">
                  {#each e.actuators as actuator (actuator.device_id)}
                    {@const c = actuatorCycle(actuator, timezone)}
                    <li class="rounded-lg bg-muted/60 px-3 py-2" data-testid={`emergency-actuator-${actuator.device_id}`}>
                      <strong>{actuator.device_name}</strong>
                      <dl class="m-0 mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
                        <dt class="text-muted-foreground">Takt-Phase</dt>
                        <dd class="m-0">{c.phase ?? '–'}</dd>
                        <dt class="text-muted-foreground">Taktquelle</dt>
                        <dd class="m-0">{c.source ?? '–'}</dd>
                        <dt class="text-muted-foreground">Frist</dt>
                        <dd class="m-0">{c.deadline ?? '–'}</dd>
                        {#if c.cycle}
                          <dt class="text-muted-foreground">Takt</dt>
                          <dd class="m-0">{c.cycle}</dd>
                        {/if}
                      </dl>
                      <div class="mt-1 text-muted-foreground">{actuator.handover_status_text} {actuator.restore_status_text}</div>
                    </li>
                  {/each}
                </ul>
              {/if}
              {#if e.comparisons.length}
                <ul class="mt-3 mb-0 list-none space-y-1 p-0 text-sm text-muted-foreground">
                  {#each e.comparisons as c (c.device_id)}
                    <li>{c.device_name}: {c.sample_count} Vergleichswerte{c.mean_deviation_k !== null ? `, mittlere Abweichung ${c.mean_deviation_k.replace('.', ',')} K` : ''}{c.suggested_offset_text ? `. ${c.suggested_offset_text}` : ''}</li>
                  {/each}
                </ul>
              {/if}
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>
