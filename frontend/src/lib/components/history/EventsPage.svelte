<script lang="ts">
  import { onMount } from 'svelte';
  import { useApp } from '$lib/context';
  import { Button } from '$lib/components/ui/button';
  import Notice from '$lib/components/rooms/Notice.svelte';
  import { formatInstant } from '$lib/rooms/logic';
  import DeviceCommandsPanel from '$lib/components/devices/DeviceCommandsPanel.svelte';
  import { createQuery } from '$lib/history/query.svelte';
  import { buildQuery, classifyRevertError, type RevertOutcome } from '$lib/history/reports';
  import type { Audit, Change, Changes, RevertResult } from '$lib/history/types';

  const { api } = useApp();
  type Tab = 'audit' | 'changes' | 'commands';
  let tab = $state<Tab>('changes');

  // -- Filter ------------------------------------------------------------------------------
  let fromDate = $state('');
  let toDate = $state('');
  let user = $state('');
  let actionCode = $state('');
  let objectText = $state('');
  let source = $state('');
  let changeType = $state('');
  let hideReverted = $state(false);
  let auditPage = $state(1);
  let changesPage = $state(1);

  const audit = createQuery(() =>
    api.get<Audit>(
      `/ui-api/audit${buildQuery({ from_date: fromDate, to_date: toDate, user, action_code: actionCode, object: objectText, source, page: auditPage })}`,
    ),
  );
  const changes = createQuery(() =>
    api.get<Changes>(
      `/ui-api/changes${buildQuery({ from_date: fromDate, to_date: toDate, change_type: changeType, source, hide_reverted: hideReverted, page: changesPage })}`,
    ),
  );

  $effect(() => {
    if (tab === 'audit') {
      void [fromDate, toDate, user, actionCode, objectText, source, auditPage];
      audit.load();
    } else if (tab === 'changes') {
      void [fromDate, toDate, changeType, source, hideReverted, changesPage];
      changes.load();
    }
  });
  onMount(() => () => {
    audit.cancel();
    changes.cancel();
  });

  const resetPages = () => {
    auditPage = 1;
    changesPage = 1;
  };

  // -- Rücknahme ---------------------------------------------------------------------------
  // Ein Schreibaufruf wird nie automatisch wiederholt. Erzwingen geht nur über den
  // Bestätigungswert des Servers aus dem 409 UND nach ausdrücklichem Haken des Nutzers.
  let busyId = $state<number | null>(null);
  let confirmId = $state<number | null>(null);
  let outcomes = $state<Record<number, RevertOutcome>>({});
  let done = $state<Record<number, string>>({});
  let forceAck = $state<Record<number, boolean>>({});

  function patchChange(updated: Change) {
    const data = changes.data;
    if (!data) return;
    const index = data.entries.findIndex((c) => c.id === updated.id);
    if (index >= 0) data.entries[index] = updated;
  }

  async function revert(change: Change, force?: string) {
    if (busyId !== null) return;
    busyId = change.id;
    delete outcomes[change.id];
    try {
      const result = await api.post<RevertResult>(
        `/ui-api/changes/${change.id}/revert`,
        force ? { force: true, force_token: force } : { force: false },
      );
      done[change.id] =
        result.outcome === 'reverted'
          ? 'Die Änderung wurde zurückgenommen.'
          : 'Die Wirkung war schon beendet; der Eintrag wurde als erledigt markiert.';
      confirmId = null;
      delete forceAck[change.id];
      changes.load(); // frisch lesen: es gibt einen neuen Eintrag
    } catch (error) {
      outcomes[change.id] = classifyRevertError(error);
      if (outcomes[change.id].kind !== 'conflict') confirmId = null;
    } finally {
      busyId = null;
    }
  }

  const when = (iso: string, zone: string) => formatInstant(iso, zone);
  const who = (c: Change) => c.user_name ?? c.token_name ?? '—';
</script>

<header class="mb-6">
  <h1>Ereignisse</h1>
  <p class="text-muted-foreground">Protokoll der Vorgänge und Änderungen. Änderungen lassen sich hier zurücknehmen.</p>
</header>

<div class="mb-4 flex flex-wrap gap-1" role="group" aria-label="Ansicht">
  <Button variant={tab === 'changes' ? 'secondary' : 'outline'} aria-pressed={tab === 'changes'} onclick={() => (tab = 'changes')}>Änderungen</Button>
  <Button variant={tab === 'audit' ? 'secondary' : 'outline'} aria-pressed={tab === 'audit'} onclick={() => (tab = 'audit')}>Audit-Protokoll</Button>
  <Button variant={tab === 'commands' ? 'secondary' : 'outline'} aria-pressed={tab === 'commands'} onclick={() => (tab = 'commands')}>Schaltprotokoll</Button>
</div>

{#if tab === 'commands'}
  <DeviceCommandsPanel />
{:else}
<form class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onsubmit={(e) => e.preventDefault()} aria-label="Filter">
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Von</span>
    <input type="date" class="h-9 rounded-lg border bg-background px-2" bind:value={fromDate} onchange={resetPages} /></label>
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Bis</span>
    <input type="date" class="h-9 rounded-lg border bg-background px-2" bind:value={toDate} onchange={resetPages} /></label>
  {#if tab === 'audit'}
    <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Benutzer</span>
      <input type="search" class="h-9 rounded-lg border bg-background px-2" bind:value={user} onchange={resetPages} /></label>
    <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Aktion</span>
      <select class="h-9 rounded-lg border bg-background px-2" bind:value={actionCode} onchange={resetPages}>
        <option value="">Alle</option>
        {#each audit.data?.actions ?? [] as a (a)}<option value={a}>{a}</option>{/each}
      </select></label>
    <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Objekt</span>
      <input type="search" class="h-9 rounded-lg border bg-background px-2" bind:value={objectText} onchange={resetPages} /></label>
  {:else}
    <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Art</span>
      <select class="h-9 rounded-lg border bg-background px-2" bind:value={changeType} onchange={resetPages}>
        <option value="">Alle</option>
        {#each changes.data?.change_types ?? [] as t (t)}<option value={t}>{t}</option>{/each}
      </select></label>
    <label class="flex items-center gap-2 self-end pb-1.5 text-sm">
      <input type="checkbox" class="size-4" bind:checked={hideReverted} onchange={resetPages} /> Zurückgenommene ausblenden</label>
  {/if}
  <label class="grid gap-1 text-sm"><span class="text-muted-foreground">Quelle</span>
    <select class="h-9 rounded-lg border bg-background px-2" bind:value={source} onchange={resetPages}>
      <option value="">Alle</option>
      {#each (tab === 'audit' ? audit.data?.sources : changes.data?.sources) ?? [] as s (s.code)}<option value={s.code}>{s.label}</option>{/each}
    </select></label>
</form>

{#if tab === 'audit'}
  {@const data = audit.data}
  {#if audit.loading && !data}
    <p class="text-muted-foreground" role="status">Protokoll wird geladen …</p>
  {:else if !data}
    <div class="max-w-xl space-y-3">
      <Notice tone="error">{audit.failure?.message ?? 'Das Protokoll konnte nicht geladen werden.'}</Notice>
      <Button variant="outline" onclick={() => audit.load()}>Erneut versuchen</Button>
    </div>
  {:else}
    {#if audit.failure}<div class="mb-3"><Notice tone={audit.failure.kind === 'validation' ? 'error' : 'warn'}>{audit.failure.message}</Notice></div>{/if}
    {#if data.entries.length === 0}
      <p class="text-muted-foreground" role="status">Keine Einträge für diese Filter.</p>
    {:else}
      <div class="overflow-x-auto rounded-xl border">
        <table class="w-full min-w-[40rem] border-collapse text-sm" data-testid="audit-table">
          <caption class="sr-only">Audit-Protokoll, Seite {data.page}</caption>
          <thead>
            <tr class="bg-muted/50 text-left">
              <th scope="col" class="px-3 py-2 font-medium">Zeit</th>
              <th scope="col" class="px-3 py-2 font-medium">Quelle</th>
              <th scope="col" class="px-3 py-2 font-medium">Benutzer</th>
              <th scope="col" class="px-3 py-2 font-medium">Aktion</th>
              <th scope="col" class="px-3 py-2 font-medium">Objekt</th>
              <th scope="col" class="px-3 py-2 font-medium">Beschreibung</th>
            </tr>
          </thead>
          <tbody>
            {#each data.entries as e (e.id)}
              <tr class="border-t align-top">
                <td class="px-3 py-2 whitespace-nowrap">{when(e.occurred_at, data.timezone)}</td>
                <td class="px-3 py-2">{e.source.label}</td>
                <td class="px-3 py-2">{e.actor ?? '—'}</td>
                <td class="px-3 py-2">{e.action}</td>
                <td class="px-3 py-2">{e.object_type}{e.object_id ? ` ${e.object_id}` : ''}</td>
                <td class="px-3 py-2">
                  {e.summary}
                  {#if e.detail}<details class="text-xs text-muted-foreground"><summary class="cursor-pointer">Einzelheiten</summary><pre class="m-0 whitespace-pre-wrap">{e.detail}</pre></details>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <div class="mt-3 flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={auditPage <= 1} onclick={() => (auditPage -= 1)}>Neuere</Button>
        <span class="text-sm text-muted-foreground">Seite {data.page}</span>
        <Button variant="outline" size="sm" disabled={!data.has_more} onclick={() => (auditPage += 1)}>Ältere</Button>
      </div>
    {/if}
  {/if}
{:else}
  {@const data = changes.data}
  {#if changes.loading && !data}
    <p class="text-muted-foreground" role="status">Änderungen werden geladen …</p>
  {:else if !data}
    <div class="max-w-xl space-y-3">
      <Notice tone="error">{changes.failure?.message ?? 'Die Änderungen konnten nicht geladen werden.'}</Notice>
      <Button variant="outline" onclick={() => changes.load()}>Erneut versuchen</Button>
    </div>
  {:else}
    {#if changes.failure}<div class="mb-3"><Notice tone="warn">Aktualisieren fehlgeschlagen: {changes.failure.message}</Notice></div>{/if}
    {#if data.entries.length === 0}
      <p class="text-muted-foreground" role="status">Keine Änderungen für diese Filter.</p>
    {:else}
      <ul class="m-0 grid list-none gap-3 p-0" data-testid="changes-list">
        {#each data.entries as c (c.id)}
          {@const outcome = outcomes[c.id]}
          <li class="rounded-xl border bg-card p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="text-sm text-muted-foreground">
                  {when(c.occurred_at, data.timezone)} · {who(c)} · {c.source}{c.zone_name ? ` · ${c.zone_name}` : ''}
                </div>
                <h2 class="m-0 text-base">{c.summary}</h2>
                <div class="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <span class="rounded-md bg-muted px-1.5 py-0.5">{c.change_type}</span>
                  {#if c.reverted_at}<span class="rounded-md bg-muted px-1.5 py-0.5">Zurückgenommen {when(c.reverted_at, data.timezone)}</span>{/if}
                  {#if c.reverts_id}<span class="rounded-md bg-muted px-1.5 py-0.5">Nimmt Änderung {c.reverts_id} zurück</span>{/if}
                  {#if c.forced}<span class="rounded-md bg-amber-500/15 px-1.5 py-0.5">Erzwungen</span>{/if}
                </div>
              </div>
              <div class="flex flex-col items-end gap-1">
                {#if c.revertible && !done[c.id]}
                  {#if confirmId !== c.id}
                    <Button variant="outline" size="sm" disabled={busyId !== null} onclick={() => { confirmId = c.id; delete outcomes[c.id]; }}>Zurücknehmen</Button>
                  {/if}
                {:else if !c.revertible && c.not_revertible_reason}
                  <span class="max-w-60 text-right text-xs text-muted-foreground">Nicht rücknehmbar: {c.not_revertible_reason}</span>
                {/if}
              </div>
            </div>

            {#if c.fields.length}
              <details class="mt-2 text-sm">
                <summary class="cursor-pointer text-muted-foreground">Felder ({c.fields.length})</summary>
                <table class="mt-1 border-collapse text-xs">
                  <caption class="sr-only">Geänderte Felder</caption>
                  <thead><tr class="text-left"><th scope="col" class="pr-4 font-medium">Feld</th><th scope="col" class="pr-4 font-medium">Vorher</th><th scope="col" class="font-medium">Nachher</th></tr></thead>
                  <tbody>
                    {#each c.fields as f (f.field)}
                      <tr><th scope="row" class="pr-4 text-left font-normal">{f.field}</th><td class="pr-4">{f.before ?? '—'}</td><td>{f.after ?? '—'}</td></tr>
                    {/each}
                  </tbody>
                </table>
              </details>
            {/if}

            {#if done[c.id]}<div class="mt-3"><Notice tone="success">{done[c.id]}</Notice></div>{/if}

            {#if confirmId === c.id && !outcome}
              <div class="mt-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 p-3 text-sm">
                <span>Diese Änderung wirklich zurücknehmen?</span>
                <Button size="sm" disabled={busyId !== null} onclick={() => revert(c)}>{busyId === c.id ? 'Wird zurückgenommen …' : 'Ja, zurücknehmen'}</Button>
                <Button variant="ghost" size="sm" disabled={busyId !== null} onclick={() => (confirmId = null)}>Abbrechen</Button>
              </div>
            {/if}

            {#if outcome?.kind === 'conflict'}
              <div class="mt-3 space-y-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm" role="alert" data-testid="conflict">
                <p class="m-0 font-medium">Konflikt: {outcome.message}</p>
                <p class="m-0">Seit dieser Änderung wurde etwas anderes geändert. Zurücknehmen würde diese neueren Werte überschreiben:</p>
                <table class="border-collapse text-xs">
                  <caption class="sr-only">Konflikte</caption>
                  <thead><tr class="text-left"><th scope="col" class="pr-4 font-medium">Feld</th><th scope="col" class="pr-4 font-medium">Jetzt</th><th scope="col" class="font-medium">Erwartet</th></tr></thead>
                  <tbody>
                    {#each outcome.conflicts as k (k.field)}
                      <tr><th scope="row" class="pr-4 text-left font-normal">{k.field}</th><td class="pr-4">{k.current ?? '—'}</td><td>{k.expected ?? '—'}</td></tr>
                    {/each}
                  </tbody>
                </table>
                <label class="flex items-start gap-2">
                  <input type="checkbox" class="mt-1 size-4" bind:checked={forceAck[c.id]} />
                  <span>Ich habe das gelesen und will die neueren Werte trotzdem überschreiben.</span>
                </label>
                <div class="flex flex-wrap gap-2">
                  <Button variant="destructive" size="sm" disabled={!forceAck[c.id] || busyId !== null} onclick={() => revert(c, outcome.forceToken)}>
                    {busyId === c.id ? 'Wird zurückgenommen …' : 'Trotzdem zurücknehmen'}
                  </Button>
                  <Button variant="ghost" size="sm" disabled={busyId !== null} onclick={() => { delete outcomes[c.id]; delete forceAck[c.id]; confirmId = null; }}>Abbrechen</Button>
                </div>
              </div>
            {:else if outcome?.kind === 'message'}
              <div class="mt-3"><Notice tone={outcome.tone}>{outcome.message}</Notice></div>
            {/if}
          </li>
        {/each}
      </ul>
      <div class="mt-3 flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={changesPage <= 1} onclick={() => (changesPage -= 1)}>Neuere</Button>
        <span class="text-sm text-muted-foreground">Seite {data.page}</span>
        <Button variant="outline" size="sm" disabled={!data.has_more} onclick={() => (changesPage += 1)}>Ältere</Button>
      </div>
    {/if}
  {/if}
{/if}
{/if}
