<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createOperationResource } from '$lib/operations/operation.svelte';
  import OpsHelp from './OpsHelp.svelte';
  import OpsPage from './OpsPage.svelte';

  // Verbund: nur der Lesezustand dieser Instanz. Die Rolle wird vom Verbund vergeben, nicht hier.
  const { api, prefix } = useApp();
  const { resource } = createOperationResource(api);
  onMount(() => resource.start());
  const cluster = $derived(resource.data?.operation.cluster ?? null);
</script>

<OpsPage title="Verbund" description="Laufen mehrere Instanzen, führt genau eine. Hier steht, welche Rolle diese Instanz gerade hat.">
  {#if cluster}
    <section class="max-w-[820px] rounded-xl border bg-card p-5" aria-labelledby="cluster-title" data-testid="cluster">
      <h2 id="cluster-title" class="mb-3 flex items-center gap-1">Diese Instanz<OpsHelp term="role" /></h2>
      <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt class="text-muted-foreground">Kennung</dt><dd class="m-0 break-all font-mono text-xs" data-testid="cluster-id">{cluster.instance_id}</dd>
        <dt class="text-muted-foreground">Rolle</dt><dd class="m-0 font-medium" data-testid="cluster-role">{cluster.standby ? 'Reserve – schaltet nicht' : 'Führend'}</dd>
        <dt class="text-muted-foreground">Übernahme nach</dt><dd class="m-0">{cluster.takeover_timeout_seconds} Sekunden ohne Lebenszeichen der führenden Instanz</dd>
      </dl>
      <p class="mt-4 mb-0 text-sm text-muted-foreground">Die Rolle ist unabhängig von der gespeicherten Freigabe und der MQTT-Sendefreigabe; beide stehen unter <a class="underline" href={appHref(prefix, '/system/betrieb')}>Betrieb & Freigaben</a>.</p>
    </section>
  {:else if resource.loading}
    <p class="text-muted-foreground" role="status">Lade …</p>
  {:else}
    <Notice tone="error">{resource.failure?.message ?? 'Der Verbundzustand konnte nicht geladen werden.'}</Notice>
  {/if}
</OpsPage>
