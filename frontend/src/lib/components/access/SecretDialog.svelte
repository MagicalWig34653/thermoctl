<script lang="ts" module>
  export interface SecretDialogApi {
    show(plaintext: string, label: string): void;
  }
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import { Copy, TriangleAlert } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';
  import { createSecretHolder } from '$lib/access/secret';

  // Einmalige Anzeige eines Klartexts. Der Klartext lebt ausschließlich im Halter dieses
  // Dialogs: nicht im Zustand der Seite, nicht in Storage, Adresse oder Konsole. Beim Schließen
  // (und beim Verlassen der Seite) wird er freigegeben; der Dialoginhalt verschwindet aus dem DOM.
  let {
    title,
    intro,
    secretLabel = 'Klartext',
    linkLabel = null,
    link = null,
    api = $bindable(),
  }: {
    title: string;
    intro: string;
    secretLabel?: string;
    /** Zweite Zeile, z. B. die Adresse fürs Tablett; aus dem Klartext gebildet, ebenfalls geheim. */
    linkLabel?: string | null;
    link?: ((plaintext: string) => string) | null;
    api?: SecretDialogApi;
  } = $props();

  const holder = createSecretHolder();
  let tick = $state(0); // macht den (nicht reaktiven) Halter für die Anzeige beobachtbar
  let copied = $state<string | null>(null);
  let copyFailed = $state(false);

  const view = $derived.by(() => {
    void tick;
    const value = holder.value;
    return value === null ? null : { value, label: holder.label ?? '', link: link ? link(value) : null };
  });

  api = {
    show(plaintext, label) {
      holder.show(plaintext, label);
      copied = null;
      copyFailed = false;
      tick += 1;
    },
  };

  function close() {
    holder.clear();
    copied = null;
    copyFailed = false;
    tick += 1;
  }
  onDestroy(() => holder.clear());

  async function copy(text: string, which: string, field: HTMLInputElement | null) {
    copyFailed = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = which;
    } catch {
      // Ohne gesicherten Kontext (http im Heimnetz) gibt es keine Zwischenablage-API:
      // markieren und dem Browser das Kopieren überlassen.
      field?.focus();
      field?.select();
      let ok = false;
      try {
        ok = document.execCommand('copy');
      } catch {
        ok = false;
      }
      copied = ok ? which : null;
      copyFailed = !ok;
    }
  }
  let secretField = $state<HTMLInputElement | null>(null);
  let linkField = $state<HTMLInputElement | null>(null);
</script>

<Dialog.Root
  open={view !== null}
  onOpenChange={(o) => {
    if (!o) close();
  }}
>
  <Dialog.Content
    class="sm:max-w-lg"
    interactOutsideBehavior="ignore"
    escapeKeydownBehavior="ignore"
    data-testid="secret-dialog"
  >
    {#if view}
      <Dialog.Header>
        <Dialog.Title>{title}</Dialog.Title>
        <Dialog.Description>{intro}</Dialog.Description>
      </Dialog.Header>

      <div
        class="flex items-start gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm"
        role="note"
      >
        <TriangleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
        <p class="m-0">
          <strong>Das siehst du nur jetzt.</strong> Nach dem Schließen ist der Klartext weg und lässt sich nirgends
          wieder abrufen. Kopiere ihn jetzt an einen sicheren Ort. Geht er verloren, stellst du einen neuen aus und
          widerrufst den alten.
        </p>
      </div>

      <div class="grid gap-3">
        <div class="grid gap-1">
          <label for="secret-value" class="text-sm font-medium">{secretLabel} für „{view.label}“</label>
          <div class="flex gap-2">
            <input
              id="secret-value"
              bind:this={secretField}
              class="h-11 min-w-0 flex-1 rounded-lg border bg-background px-3 font-mono text-sm"
              readonly
              autocomplete="off"
              spellcheck="false"
              value={view.value}
              onfocus={(e) => e.currentTarget.select()}
            />
            <Button variant="outline" class="h-11" onclick={() => copy(view.value, 'secret', secretField)}>
              <Copy size={16} aria-hidden="true" />Kopieren
            </Button>
          </div>
        </div>

        {#if view.link && linkLabel}
          <div class="grid gap-1">
            <label for="secret-link" class="text-sm font-medium">{linkLabel}</label>
            <div class="flex gap-2">
              <input
                id="secret-link"
                bind:this={linkField}
                class="h-11 min-w-0 flex-1 rounded-lg border bg-background px-3 font-mono text-sm"
                readonly
                autocomplete="off"
                spellcheck="false"
                value={view.link}
                onfocus={(e) => e.currentTarget.select()}
              />
              <Button variant="outline" class="h-11" onclick={() => copy(view.link ?? '', 'link', linkField)}>
                <Copy size={16} aria-hidden="true" />Kopieren
              </Button>
            </div>
          </div>
        {/if}

        <p class="m-0 min-h-5 text-sm" role="status">
          {#if copied}Kopiert.{:else if copyFailed}Kopieren hat nicht geklappt – markiere den Text und kopiere ihn von Hand.{/if}
        </p>
      </div>

      <Dialog.Footer>
        <Button onclick={close}>Fertig – ich habe es gesichert</Button>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
