<script lang="ts">
  import { LogOut } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button';
  import Placeholder from '$lib/components/Placeholder.svelte';
  import { PASSKEYS_PATH } from '$lib/navigation';
  import { useApp } from '$lib/context';

  const { session } = useApp();
  let error = $state<string | null>(null);

  async function logout() {
    error = await session.logout();
  }
</script>

<Placeholder
  title="Konto & Sicherheit"
  description="Passwort, Passkeys und Sitzungen"
  links={[
    { path: '/account', label: 'Konto (bisherige Seite)' },
    { path: PASSKEYS_PATH, label: 'Passkeys (bisherige Seite)' },
  ]}
/>
<div class="mt-6">
  <Button variant="outline" onclick={logout}><LogOut size={16} aria-hidden="true" />Abmelden</Button>
  {#if error}<p class="mt-3 text-sm text-destructive" role="alert">{error}</p>{/if}
</div>
