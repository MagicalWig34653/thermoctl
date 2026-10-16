<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { createOperationResource } from '$lib/operations/operation.svelte';
  import { operationHeadline } from '$lib/operations/logic';
  import type { ArmedResult } from '$lib/operations/types';
  import ArmPanel from './ArmPanel.svelte';
  import OpsPage from './OpsPage.svelte';
  import RoomDecisions from './RoomDecisions.svelte';
  import StateCards from './StateCards.svelte';

  const { api } = useApp();
  const { client, resource } = createOperationResource(api);
  onMount(() => resource.start());

  const page = $derived(resource.data);
  const headline = $derived(page ? operationHeadline(page.operation.actuation) : null);

  // Nach einer Freigabeänderung gilt die Antwort des Servers sofort; danach wird neu gelesen.
  function applied(result: ArmedResult) {
    if (resource.data) resource.adopt({ ...resource.data, operation: { ...resource.data.operation, actuation: result.actuation } });
    void resource.refresh();
  }
</script>

<OpsPage title="Betrieb & Freigaben" description="Ob die Regelung schaltet, hängt von mehreren getrennten Zuständen ab. Sie stehen hier nebeneinander und werden nicht zusammengefasst.">
  {#if page && headline}
    {#if resource.failure}<Notice tone="warn">Aktualisierung fehlgeschlagen: {resource.failure.message} Angezeigt wird der letzte bekannte Stand.</Notice>{/if}
    <section aria-labelledby="state-title" class="space-y-3">
      <h2 id="state-title" class="mb-0">Zustand</h2>
      <StateCards actuation={page.operation.actuation} />
    </section>
    <section aria-labelledby="explain-title" class="max-w-[820px]" data-testid="headline">
      <h2 id="explain-title" class="mb-2">Was das bedeutet</h2>
      <Notice tone={headline.tone === 'warn' ? 'warn' : 'info'}>{headline.text}</Notice>
    </section>
    {#if page.operation.may_arm}
      <ArmPanel operation={page.operation} {client} onresult={applied} />
    {:else}
      <div class="max-w-[820px]"><Notice tone="info">Die Freigabe ändern dürfen nur Personen mit dem Recht „Freigabe“. Du kannst die Zustände ansehen.</Notice></div>
    {/if}
    <RoomDecisions operation={page.operation} timezone={page.timezone} />
  {:else if resource.loading}
    <p class="text-muted-foreground" role="status">Lade …</p>
  {:else}
    <Notice tone="error">{resource.failure?.message ?? 'Der Betriebszustand konnte nicht geladen werden.'}</Notice>
  {/if}
</OpsPage>
