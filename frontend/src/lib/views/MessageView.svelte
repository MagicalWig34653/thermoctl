<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import { appHref } from '$lib/paths';
  import { useApp } from '$lib/context';

  let { title, text }: { title: string; text: string } = $props();
  const { prefix } = useApp();
</script>

<section class="max-w-xl">
  <h1>{title}</h1>
  <p class="text-muted-foreground">{text}</p>
  <Button href={appHref(prefix, '/')} variant="outline">Zur Übersicht</Button>
</section>
