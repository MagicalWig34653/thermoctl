<script lang="ts">
  import { clip, layoutDiagram, summary, type Diagram } from '$lib/devices/diagram';

  // Gezeichnetes Diagramm (SVG) aus der strukturierten Serverantwort. Texte stehen als
  // Textknoten im SVG; es wird kein HTML eingesetzt. Die Listenalternative liefert der Aufrufer.
  let { diagram, title }: { diagram: Diagram; title: string } = $props();
  const layout = $derived(layoutDiagram(diagram));
  const uid = $props.id();
</script>

<!-- Ein scrollbarer Bereich muss per Tastatur erreichbar sein, sonst ist das Diagramm auf schmalen Geräten nicht lesbar. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="max-w-full overflow-x-auto rounded-lg border bg-background focus-visible:outline-2 focus-visible:outline-offset-2"
  role="region"
  aria-label={`${title}: Diagramm`}
  tabindex="0"
  data-testid="flow-diagram"
>
  <svg
    width={layout.width}
    height={layout.height}
    viewBox={`0 0 ${layout.width} ${layout.height}`}
    role="img"
    aria-labelledby={`${uid}-t`}
    class="block"
  >
    <title id={`${uid}-t`}>{summary(diagram, title)}</title>
    <defs>
      <marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" class="fill-muted-foreground" />
      </marker>
    </defs>
    {#each layout.edges as e (e.key)}
      <path
        d={e.path}
        fill="none"
        class="stroke-muted-foreground"
        stroke-width="1.6"
        stroke-dasharray={e.dashed ? '6 5' : undefined}
        marker-end={`url(#${uid}-arrow)`}
        data-testid="flow-edge"
      />
      <text
        x={e.labelX}
        y={e.labelY - 5}
        text-anchor="middle"
        font-size="11"
        class="fill-foreground stroke-background"
        stroke-width="4"
        paint-order="stroke"
      >
        {clip(e.label, 24)}<title>{e.label}</title>
      </text>
    {/each}
    {#each layout.nodes as n (n.id)}
      <g data-testid="flow-node" data-tone={n.tone}>
        <title>{n.detail ? `${n.label} · ${n.detail}` : n.label}</title>
        <rect
          x={n.x}
          y={n.y}
          width={n.width}
          height={n.height}
          rx="10"
          stroke-width={n.tone === 'zone' ? 2 : 1.2}
          stroke-dasharray={n.dashed ? '5 4' : undefined}
          class="{n.tone === 'zone' ? 'fill-secondary stroke-foreground' : n.tone === 'missing' ? 'fill-background stroke-destructive' : 'fill-card stroke-border'}"
        />
        <text x={n.x + 12} y={n.y + (n.detail ? 23 : 33)} font-size="13" font-weight="600" class={n.tone === 'missing' ? 'fill-destructive' : 'fill-foreground'}>
          {clip(n.label, 26)}
        </text>
        {#if n.detail}
          <text x={n.x + 12} y={n.y + 41} font-size="11" class="fill-muted-foreground">{clip(n.detail, 32)}</text>
        {/if}
      </g>
    {/each}
  </svg>
</div>
