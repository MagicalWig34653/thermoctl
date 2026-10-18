<script lang="ts">
  import * as Dialog from '$lib/components/ui/dialog';
  import { Button } from '$lib/components/ui/button';
  import type { UserRow } from '$lib/access/types';
  import UserPreviewBody from './UserPreviewBody.svelte';

  let { open = $bindable(false), user }: { open?: boolean; user: UserRow | null } = $props();
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
    <Dialog.Header>
      <Dialog.Title>Vorschau: {user?.display_name || user?.username}</Dialog.Title>
      <Dialog.Description>Nur Ansicht. Navigation, Rechte und Übersicht dieser Person.</Dialog.Description>
    </Dialog.Header>
    {#if user}
      {#key user.id}<UserPreviewBody {user} />{/key}
    {/if}
    <Dialog.Footer>
      <Button type="button" variant="outline" onclick={() => (open = false)} data-testid="preview-close">Schließen</Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
