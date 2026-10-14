"""Mermaid-Quelltext aus einem `DataFlowGraph` (`domain/data_flow.py`).

Getrennt vom Graphen, weil der Graph nichts von der Darstellung wissen soll. Dieser
Renderer ist die einzige Stelle, die Mermaid-Syntax kennt.

**Der Quelltext wird im Browser gerendert, und Gerätenamen kommen von außen** (aus
Zigbee2MQTT bzw. dem Namen, den jemand vergeben hat). Ein Name mit Anführungszeichen,
`]`, `<` oder Zeilenumbruch darf deshalb weder die Mermaid-Syntax verlassen noch HTML
einschleusen. Dagegen stehen drei Dinge, die zusammen halten sollen:

1. Knoten-Kennungen sind erzeugt (`n0`, `n1`, ...) und enthalten nie einen Namen.
2. Jeder Text steht ausschließlich als **Beschriftung in Anführungszeichen** und geht durch
   `escape_label`: alles, was Mermaid oder HTML deuten könnte, wird zu einer
   Mermaid-Zeichenreferenz (`#60;`), die erst beim Zeichnen zu Text wird.
3. Die Seite rendert mit `securityLevel: "strict"` (`static/data_flow.js`).
"""

import re

from thermoctl.domain.data_flow import (
    EDGE_FROST,
    NODE_ACTUATOR,
    NODE_MISSING,
    NODE_SENSOR,
    NODE_SETPOINT,
    NODE_WINDOW,
    NODE_ZONE,
    DataFlowGraph,
    FlowEdge,
    FlowNode,
)

# Alles außer Buchstaben, Ziffern, Leerzeichen und einer kleinen Menge harmloser
# Satzzeichen wird zu einer Zeichenreferenz. Eine Positivliste statt einer Liste
# gefährlicher Zeichen: was wir nicht kennen, kommt nicht durch.
_SAFE = re.compile(r"[\w ()/.,:+\-äöüÄÖÜß°]", re.UNICODE)


def escape_label(text: str) -> str:
    """Macht `text` zu einer Beschriftung, die Mermaid nicht als Syntax und nie als HTML liest.

    Zeilenumbrüche und andere Steuerzeichen werden zu einem Leerzeichen, jedes nicht
    bekannte Zeichen zu `#<Codepunkt>;`.
    """
    out: list[str] = []
    for char in text:
        if char.isspace() or ord(char) < 32 or ord(char) == 127:
            out.append(" ")
        elif _SAFE.fullmatch(char):
            out.append(char)
        else:
            out.append(f"#{ord(char)};")
    return " ".join("".join(out).split())


_SHAPES = {
    NODE_SENSOR: ('(["', '"])'),
    NODE_WINDOW: ('(["', '"])'),
    NODE_SETPOINT: ('(["', '"])'),
    NODE_ZONE: ('["', '"]'),
    NODE_ACTUATOR: ('["', '"]'),
    NODE_MISSING: ('["', '"]'),
}


def _node_line(mermaid_id: str, node: FlowNode) -> str:
    opening, closing = _SHAPES[node.kind]
    text = escape_label(node.label)
    if node.detail:
        # `<br/>` ist unser eigener, fester Zeilenumbruch -- nie Teil eines Namens.
        text += "<br/>" + escape_label(node.detail)
    return f"    {mermaid_id}{opening}{text}{closing}"


def _edge_line(ids: dict[str, str], edge: FlowEdge) -> str:
    text = escape_label(edge.label)
    if edge.property_name:
        text += "<br/>" + escape_label(edge.property_name)
    arrow = "-.->" if edge.dashed else ("==>" if edge.kind == EDGE_FROST else "-->")
    return f'    {ids[edge.source]} {arrow}|"{text}"| {ids[edge.target]}'


def render_mermaid(graph: DataFlowGraph) -> str:
    """Mermaid-Flussdiagramm (links nach rechts) für den Graphen."""
    ids = {node.id: f"n{index}" for index, node in enumerate(graph.nodes)}
    lines = ["flowchart LR"]
    lines.extend(_node_line(ids[node.id], node) for node in graph.nodes)
    lines.extend(_edge_line(ids, edge) for edge in graph.edges)
    lines.append("    classDef missing stroke-dasharray: 5 5")
    missing = [ids[n.id] for n in graph.nodes if n.kind == NODE_MISSING]
    if missing:
        lines.append(f"    class {','.join(missing)} missing")
    return "\n".join(lines) + "\n"
