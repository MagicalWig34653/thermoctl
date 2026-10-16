<script lang="ts">
  import { SOURCE_LABEL, SOURCE_LINK } from '$lib/source';

  let { class: className = '' }: { class?: string } = $props();
</script>

<a {...SOURCE_LINK} class="underline decoration-dotted underline-offset-4 hover:text-foreground {className}" data-testid="source-link">{SOURCE_LABEL}</a>
