<script lang="ts">
  import type { VisibleArea, VisiblePage } from '$lib/navigation';
  import Placeholder from '$lib/components/Placeholder.svelte';

  let { area, page }: { area: VisibleArea; page: VisiblePage } = $props();
</script>

<Placeholder title={page.label} description={area.label} links={page.links} />
