<script lang="ts">
  // Lädt den Code einer Ansicht beim Aufruf. Beim Laden steht ein ruhiger Platzhalter mit fester
  // Mindesthöhe (kein Layoutsprung), scheitert das Laden (Netz, veralteter Chunk nach einem Update),
  // gibt es einen Hinweis mit „Neu laden“ statt einer weißen Seite.
  import type { PageModule } from '$lib/views/pages';
  import { Button } from '$lib/components/ui/button';
  import { reloadOnce } from '$lib/reload';

  let { load, props = {} }: { load: () => Promise<PageModule>; props?: Record<string, unknown> } =
    $props();
  let attempt = $state(0);
  // Ein gescheiterter Abruf (kurzer Netzausfall) wird einmal von selbst wiederholt. Scheitert auch das
  // (meist: die Anwendung wurde aktualisiert, der Dateiname der Seite existiert nicht mehr), lädt die
  // Seite einmal neu und holt damit die aktuelle Hülle; erst danach kommt die Meldung.
  const promise = $derived.by(() => {
    attempt;
    return load().catch(
      () =>
        new Promise<PageModule>((resolve, reject) =>
          setTimeout(
            () =>
              load().then(resolve, (error) => {
                if (!reloadOnce()) reject(error);
              }),
            700,
          ),
        ),
    );
  });
</script>

{#await promise}
  <div class="min-h-[40vh]" aria-busy="true" aria-live="polite">
    <p class="m-0 text-sm text-muted-foreground" role="status">Wird geladen …</p>
  </div>
{:then mod}
  {@const View = mod.default}
  <View {...props} />
{:catch}
  <div class="max-w-[820px] rounded-xl border p-4" role="alert" data-testid="lazy-error">
    <h2 class="m-0 text-lg">Die Seite konnte nicht geladen werden</h2>
    <p class="mt-2 text-sm text-muted-foreground">
      Vermutlich ist die Verbindung unterbrochen oder die Anwendung wurde gerade aktualisiert.
    </p>
    <Button class="mt-3" variant="outline" onclick={() => (attempt += 1)}>Neu laden</Button>
  </div>
{/await}
