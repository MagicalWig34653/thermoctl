<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError, createApi, runtimePrefix } from '$lib/api';
  import { createRouter } from '$lib/router.svelte';

  // Basispfad zur Laufzeit aus der Seite, nicht zur Build-Zeit.
  const prefix = runtimePrefix();
  const api = createApi({ fetch: window.fetch.bind(window), prefix, cookies: () => document.cookie });
  const router = createRouter(prefix);

  let bootstrap = $state<unknown>(null);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      bootstrap = await api.get('/ui-api/bootstrap');
    } catch (e) {
      error = e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
    }
  });
</script>

<main class="mx-auto max-w-3xl p-6">
  <h1 class="text-xl font-semibold">thermoctl</h1>
  <p class="text-sm text-neutral-500">Pfad: {router.path} · Präfix: "{prefix}"</p>
  {#if error}
    <p class="mt-4 text-red-600">Fehler: {error}</p>
  {:else if bootstrap === null}
    <p class="mt-4">Lade ...</p>
  {:else}
    <pre class="mt-4 overflow-auto rounded bg-neutral-100 p-3 text-xs">{JSON.stringify(bootstrap, null, 2)}</pre>
  {/if}
</main>
