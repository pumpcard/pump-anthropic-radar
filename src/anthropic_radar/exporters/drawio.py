"""draw.io (mxGraph XML) architecture diagram export.

Layout: Organization -> Workspaces -> API Keys, with dashed edges out to
any detected external-service relationships.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from anthropic_radar.models.base import RunResult

_COLORS = {
    "org": "#1F2937",
    "workspace": "#2563EB",
    "api_key": "#059669",
    "service": "#D97706",
}


def _cell(
    cell_id: str,
    value: str,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: str,
    parent: str = "1",
) -> str:
    style = f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};fontColor=#FFFFFF;"
    return (
        f'<mxCell id="{cell_id}" value="{escape(value)}" style="{style}" '
        f'vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def _edge(edge_id: str, source: str, target: str, dashed: bool = False) -> str:
    style = "edgeStyle=orthogonalEdgeStyle;html=1;" + ("dashed=1;" if dashed else "")
    return (
        f'<mxCell id="{edge_id}" style="{style}" edge="1" parent="1" '
        f'source="{source}" target="{target}">'
        f'<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


def export_drawio(result: RunResult, out_path: str | Path) -> Path:
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    cells: list[str] = []
    org_id = "org_root"
    org_name = result.organization.name if result.organization else "Anthropic Org"
    cells.append(_cell(org_id, org_name, 40, 40, 200, 60, _COLORS["org"]))

    ws_y = 160
    ws_ids: dict[str, str] = {}
    for index, workspace in enumerate(result.workspaces):
        node_id = f"ws_{workspace.id}"
        ws_ids[workspace.id] = node_id
        cells.append(
            _cell(
                node_id,
                workspace.name or workspace.id,
                40 + index * 220,
                ws_y,
                180,
                50,
                _COLORS["workspace"],
            )
        )
        cells.append(_edge(f"e_org_{node_id}", org_id, node_id))

    key_y = 280
    default_ws_node = None
    keys_by_ws: dict[str, list] = {}
    for key in result.api_keys:
        keys_by_ws.setdefault(key.workspace_id or "__default__", []).append(key)

    node_index = 0
    key_node_ids: dict[str, str] = {}
    for ws_key, keys in keys_by_ws.items():
        parent_node = ws_ids.get(ws_key)
        if parent_node is None:
            if default_ws_node is None:
                default_ws_node = "ws_default"
                cells.append(
                    _cell(default_ws_node, "(org default)", 40, ws_y, 180, 50, _COLORS["workspace"])
                )
                cells.append(_edge(f"e_org_{default_ws_node}", org_id, default_ws_node))
            parent_node = default_ws_node
        for key in keys:
            node_id = f"key_{key.id}"
            key_node_ids[key.id] = node_id
            cells.append(
                _cell(
                    node_id,
                    key.name or key.id,
                    40 + node_index * 200,
                    key_y,
                    170,
                    40,
                    _COLORS["api_key"],
                )
            )
            cells.append(_edge(f"e_{parent_node}_{node_id}", parent_node, node_id))
            node_index += 1

    svc_y = 400
    svc_nodes: dict[str, str] = {}
    for index, rel in enumerate(result.relationships):
        svc_key = rel.kind.value
        if svc_key not in svc_nodes:
            svc_node_id = f"svc_{svc_key}"
            svc_nodes[svc_key] = svc_node_id
            cells.append(
                _cell(
                    svc_node_id,
                    svc_key,
                    40 + len(svc_nodes) * 200,
                    svc_y,
                    160,
                    40,
                    _COLORS["service"],
                )
            )
        source_node = (
            key_node_ids.get(rel.source_id)
            if rel.source_kind == "api_key"
            else ws_ids.get(rel.source_id)
        )
        if source_node:
            cells.append(_edge(f"e_rel_{index}", source_node, svc_nodes[svc_key], dashed=True))

    body = "".join(cells)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxfile host="pump-anthropic-radar"><diagram name="Anthropic Org">'
        '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" '
        'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1400" '
        'pageHeight="900" math="0" shadow="0">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
        "</mxGraphModel></diagram></mxfile>"
    )
    destination.write_text(xml, encoding="utf-8")
    return destination
