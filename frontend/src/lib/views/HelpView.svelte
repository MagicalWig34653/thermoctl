<script lang="ts">
  import Placeholder from '$lib/components/Placeholder.svelte';
  import { HELP_PATH } from '$lib/navigation';
</script>

<Placeholder
  title="Glossar & Hilfe"
  description="Begriffe und Erklärungen"
  links={[{ path: HELP_PATH, label: 'Glossar (bisherige Seite)' }]}
/>
