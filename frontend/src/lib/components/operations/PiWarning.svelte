<script lang="ts">
  import { useApp } from '$lib/context';
  import { appHref } from '$lib/paths';
  import { switchingTable, wearRatioSentence } from '$lib/operations/logic';
  import type { PiSwitchingModel } from '$lib/operations/types';

  // Verschleißhinweis an der PI-Regelung: dort lesbar, wo man umschaltet. Die Zahlen der
  // Modellrechnung und das Verhältnis kommen vom Server; hier wird nichts gerechnet.
  let { model }: { model: PiSwitchingModel } = $props();
  const { prefix } = useApp();
  const rows = $derived(switchingTable(model));
  const wear = $derived(appHref(prefix, '/auswertung/verschleiss'));
  const settings = $derived(appHref(prefix, '/heizung/regelvorgaben'));
</script>

<div class="rounded-lg border border-amber-500/60 bg-amber-500/10 p-4 text-sm" role="note" data-testid="pi-warning">
  <p class="m-0">
    <strong>PI (Beta) kann je nach Einstellung mehr Schaltspiele erzeugen als die Hysterese.</strong>
    Jedes Schaltspiel zählt gegen die angenommene Lebensdauer des Relais. Wie oft dieser Raum tatsächlich wechselt, hängt von den Parametern ab; gezählt wird auf der Seite
    <a class="underline" href={wear} data-testid="pi-wear-link">Relaisverschleiß</a> mit Jahreshochrechnung je Gerät. Für einen Kessel oder Verdichter ohne eigenen Taktschutz wird PI nicht empfohlen; häufiger Wechsel kann dort Lebensdauer und Wirkungsgrad kosten.
  </p>
  <details class="mt-3">
    <summary class="min-h-9 cursor-pointer font-medium">Modellrechnung ansehen</summary>
    <div class="mt-2 overflow-x-auto">
      <table class="w-full text-left" data-testid="pi-switching-table">
        <caption class="sr-only">Schaltspiele je Stunde, Tag und Jahr (Modellrechnung)</caption>
        <thead>
          <tr>
            <th scope="col"></th>
            <th scope="col" class="text-right">je Stunde</th>
            <th scope="col" class="text-right">je Tag</th>
            <th scope="col" class="text-right">je Jahr</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.id)}
            <tr>
              <th scope="row" class="pr-3 font-normal">{row.label}</th>
              <td class="text-right tabular-nums">{row.perHour}</td>
              <td class="text-right tabular-nums">{row.perDay}</td>
              <td class="text-right tabular-nums">{row.perYear}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="mt-2 mb-0 text-xs" data-testid="pi-wear-ratio">
      Modellrechnung, keine Messung dieses Raums. Schaltspiele, nicht Zustandswechsel. Der Höchstwert der Hysterese gilt für die Vorgabe von je 300 Sekunden Mindest-Ein- und -Ausschaltdauer; mit kürzeren Mindestdauern (zulässig ab 30 Sekunden) und kurzem Regelzyklus sind rechnerisch mehr möglich.
      {wearRatioSentence(model)} Der ungünstigste Fall ist ausdrücklich nicht der Regelfall; die Modellzeile mit 30 % Tastgrad und der tatsächliche, gemessene Verschleiß je Gerät stehen unter
      <a class="underline" href={wear}>Relaisverschleiß</a>. Die Annahme ändert man in den <a class="underline" href={settings}>Regelvorgaben</a>.
    </p>
  </details>
</div>
