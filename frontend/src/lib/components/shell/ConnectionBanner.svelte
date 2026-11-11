<script lang="ts">
  import { WifiOff } from '@lucide/svelte';
  import { OFFLINE_HINT, bannerText } from '$lib/connection';
  import { useApp } from '$lib/context';

  const { connection } = useApp();
</script>

<!-- Bleibt unter der Kopfleiste stehen, solange die Verbindung unterbrochen ist. -->
<div class="sticky top-[60px] z-10" role="status" aria-live="polite" data-testid="connection-banner">
  {#if connection.down}
    <div
      class="flex items-start gap-3 border-b border-destructive bg-destructive px-4 py-3 text-sm font-medium text-white sm:px-7 dark:text-background"
    >
      <WifiOff size={18} class="mt-0.5 shrink-0" aria-hidden="true" />
      <p class="min-w-0">
        {bannerText(connection.lastOkAt, connection.startedAt)}.
        <span class="block font-normal sm:inline"
          >{OFFLINE_HINT}: Änderungen sind gesperrt, bis die Verbindung wieder steht.</span
        >
      </p>
    </div>
  {/if}
</div>
