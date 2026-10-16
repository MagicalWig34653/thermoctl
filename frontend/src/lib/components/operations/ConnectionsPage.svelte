<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { Button } from '$lib/components/ui/button';
  import { browserClipboardEnv, copyText } from '$lib/operations/clipboard';
  import { Badge } from '$lib/components/ui/badge';
  import { createOperationsClient } from '$lib/operations/client';
  import { displayDetailValue, opsFailure, type OpsFailure } from '$lib/operations/logic';
  import type { Interfaces } from '$lib/operations/types';
  import LoadGate from './LoadGate.svelte';
  import OpsPage from './OpsPage.svelte';

  // Schnittstellen und ihr Zustand, nur lesend. Zugangsdaten stehen in der Umgebung des Servers
  // und erscheinen hier nie im Klartext, nur als „hinterlegt“.
  const { api, prefix } = useApp();
  const client = createOperationsClient(api);
  let data = $state<Interfaces | null>(null);
  let failure = $state<OpsFailure | null>(null);
  let loading = $state(true);
  async function load() {
    loading = true;
    try {
      data = await client.interfaces();
      failure = null;
    } catch (error) {
      failure = opsFailure(error);
    } finally {
      loading = false;
    }
  }
  onMount(() => void load());

  // Rückmeldung je Raum nach „Konfiguration kopieren“; verschwindet nach fünf Sekunden.
  let copyState = $state<Record<number, { ok: boolean; text: string }>>({});
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  async function copyConfig(zoneId: number, json: string) {
    const result = await copyText(json, browserClipboardEnv());
    copyState[zoneId] = result;
    clearTimeout(timers.get(zoneId));
    timers.set(zoneId, setTimeout(() => delete copyState[zoneId], 5000));
  }
  onMount(() => () => timers.forEach((t) => clearTimeout(t)));
</script>

<OpsPage title="Verbindungen" description="Welche Schnittstellen der Dienst nutzt, ob sie erreichbar sind und woher ihre Einstellungen kommen. Die Werte stehen in der Umgebung des Servers und werden hier nur angezeigt.">
  <LoadGate loaded={data !== null} {loading} {failure} onretry={load}>
    {#if data}
      <ul class="m-0 grid list-none gap-4 p-0 lg:grid-cols-2" data-testid="interfaces">
        {#each data.interfaces as item (item.key)}
          <li class="rounded-xl border bg-card p-5" data-testid={`interface-${item.key}`}>
            <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
              <h2 class="mb-0">{item.name}</h2>
              <Badge variant="secondary">{item.state}</Badge>
            </div>
            <p class="mb-2 text-sm text-muted-foreground">{item.purpose}</p>
            <p class="mb-3 text-sm font-medium">{item.finding}</p>
            {#if item.details.length}
              <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {#each item.details as detail (detail.name + (detail.variable ?? ''))}
                  <dt class="text-muted-foreground">{detail.name}</dt>
                  <dd class="m-0 min-w-0 break-words">
                    {displayDetailValue(detail)}
                    <span class="block text-xs text-muted-foreground">{detail.source}{detail.variable ? ` · ${detail.variable}` : ''}</span>
                  </dd>
                {/each}
              </dl>
            {/if}
            {#if item.hint}<p class="mt-3 mb-0 text-sm text-muted-foreground">{item.hint}</p>{/if}
            {#if item.more}<p class="mt-3 mb-0 text-sm"><a class="underline" href={appHref(prefix, item.more.path)}>{item.more.label}</a></p>{/if}
          </li>
        {/each}
      </ul>

      {#if data.homebridge_zones.length}
        <section class="max-w-[820px] rounded-xl border bg-card p-5" aria-labelledby="hb-title" data-testid="homebridge">
          <h2 id="hb-title" class="mb-1">Homebridge-Konfiguration je Raum</h2>
          <p class="mb-3 text-sm text-muted-foreground">
            Fertige Konfiguration je Raum für das Homebridge-Plugin
            <a class="underline" href="https://github.com/arachnetech/homebridge-mqttthing">mqtt-thing</a>, mit den echten Topics dieser Anlage.
          </p>
          <p class="mb-3 text-sm" data-testid="homebridge-warning">
            <Badge variant="secondary">Achtung</Badge>
            <code>username</code> und <code>password</code> in jeder Konfiguration sind Platzhalter. Homebridge braucht einen <strong>eigenen</strong> Broker-Zugang mit möglichst engen Rechten, niemals den von thermoctl.
            {#if !data.homebridge_broker_configured}
              Ohne eingetragene Broker-Adresse (<code>THERMOCTL_MQTT_HOST</code>) steht auch <code>url</code> als Platzhalter; vor dem Einsatz durch die echte Adresse ersetzen.
            {/if}
          </p>
          <div class="space-y-2">
            {#each data.homebridge_zones as zone (zone.zone_id)}
              <details class="rounded-lg border px-3 py-2">
                <summary class="flex min-h-10 cursor-pointer items-center font-medium">{zone.zone_name}</summary>
                <pre class="mt-2 mb-0 text-xs whitespace-pre-wrap break-all" id={`hb-config-${zone.zone_id}`}>{zone.config_json}</pre>
                <div class="mt-2 flex flex-wrap items-center gap-2">
                  <Button variant="outline" size="sm" class="min-h-10" data-testid={`hb-copy-${zone.zone_id}`} onclick={() => copyConfig(zone.zone_id, zone.config_json)}>Konfiguration kopieren</Button>
                  {#if copyState[zone.zone_id]}
                    <span class="text-sm {copyState[zone.zone_id].ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-destructive'}" role="status">{copyState[zone.zone_id].text}</span>
                  {/if}
                </div>
              </details>
            {/each}
          </div>
        </section>
      {/if}
    {/if}
  </LoadGate>
</OpsPage>
