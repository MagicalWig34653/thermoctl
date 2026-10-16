<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import * as Dialog from '$lib/components/ui/dialog';

  // Bestätigung vor einer destruktiven Aktion. Die Aktion läuft genau einmal; schlägt sie fehl,
  // zeigt die aufrufende Ansicht die Meldung, der Dialog schließt in jedem Fall.
  let {
    open = $bindable(false),
    title,
    description,
    confirmLabel,
    busy = false,
    onconfirm,
  }: {
    open: boolean;
    title: string;
    description: string;
    confirmLabel: string;
    busy?: boolean;
    onconfirm: () => void | Promise<void>;
  } = $props();
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>{title}</Dialog.Title>
      <Dialog.Description>{description}</Dialog.Description>
    </Dialog.Header>
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (open = false)}>Abbrechen</Button>
      <Button
        variant="destructive"
        disabled={busy}
        onclick={async () => {
          open = false;
          await onconfirm();
        }}>{confirmLabel}</Button
      >
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
