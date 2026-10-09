"""Locally bundled interactive record graph for the dashboard.

The component is an :class:`anywidget.AnyWidget` rendered through marimo's
``mo.ui.anywidget`` bridge. It contains its own SVG renderer, keyboard
navigation, and pan/zoom behavior; no CDN or network asset is used.

The widget receives a fully computed ``tree`` payload from
:func:`tree_payload` and reports user intent through three synced traits:
``selected`` (record ID), ``expanded`` (record IDs), and ``query`` (search
text). The Python side recomputes the visible layout and writes ``tree`` back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import traitlets
from anywidget import AnyWidget

from .data import RecordView
from .tree import TreeLayout

_ESM = r"""
function iconPath(kind) {
  if (kind === "expand") {
    return '<path d="M8 4v8l6-4z"/>';
  }
  if (kind === "collapse") {
    return '<path d="M4 6l6 4 6-4z"/>';
  }
  return "";
}

function el(tag, attrs, children) {
  const node = document.createElementNS(
    tag === "svg" || tag === "path" || tag === "g" || tag === "rect"
      || tag === "circle" || tag === "text"
      ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml",
    tag
  );
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else if (value !== null && value !== undefined) {
      node.setAttribute(key, value);
    }
  }
  for (const child of children || []) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function render({ model, el: root }) {
  root.classList.add("hk-record-graph");
  root.innerHTML = "";
  const state = {
    tx: 0,
    ty: 0,
    k: 1,
    width: 800,
    height: 400,
  };

  const toolbar = el("div", { class: "hk-tree-toolbar" });
  const search = el("input", {
    class: "hk-tree-search",
    type: "search",
    placeholder: "Search records",
    "aria-label": "Search records",
    value: model.get("query") || "",
  });
  search.addEventListener("input", () => {
    model.set("query", search.value);
    model.save_changes();
  });
  const fitButton = el(
    "button",
    { class: "hk-tree-button", type: "button" },
    ["Fit"]
  );
  const resetButton = el(
    "button",
    { class: "hk-tree-button", type: "button" },
    ["Reset"]
  );
  toolbar.append(search, fitButton, resetButton);

  const svg = el("svg", { class: "hk-tree-svg", role: "tree", tabindex: "0" });
  const viewport = el("g", { class: "hk-tree-viewport" });
  svg.appendChild(viewport);

  const list = el("div", { class: "hk-tree-list", role: "tree" });

  root.append(toolbar, svg, list);

  function nextFocus(dir) {
    const nodes = Array.from(svg.querySelectorAll(".hk-tree-node")).filter(
      (n) => n.getAttribute("aria-hidden") !== "true"
    );
    if (!nodes.length) return;
    const index = nodes.indexOf(document.activeElement);
    const next = index === -1 ? 0 : (index + dir + nodes.length) % nodes.length;
    nodes[next].focus();
  }

  function updateTransform() {
    viewport.setAttribute(
      "transform",
      `translate(${state.tx} ${state.ty}) scale(${state.k})`
    );
  }

  function fit() {
    const rect = root.getBoundingClientRect();
    const availableWidth = Math.max(rect.width - 8, 240);
    const availableHeight = Math.max((model.get("height") || 360) - 48, 160);
    state.k = Math.min(
      availableWidth / state.width,
      availableHeight / state.height,
      1.4
    );
    state.tx = (availableWidth - state.width * state.k) / 2;
    state.ty = (availableHeight - state.height * state.k) / 2;
    updateTransform();
  }

  function reset() {
    state.k = 1;
    state.tx = 24;
    state.ty = 24;
    updateTransform();
  }

  fitButton.addEventListener("click", fit);
  resetButton.addEventListener("click", reset);
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.min(4, Math.max(0.2, state.k * factor));
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    state.tx = px - ((px - state.tx) / state.k) * next;
    state.ty = py - ((py - state.ty) / state.k) * next;
    state.k = next;
    updateTransform();
  });

  let dragging = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".hk-tree-node")) return;
    dragging = { x: event.clientX, y: event.clientY, tx: state.tx, ty: state.ty };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    state.tx = dragging.tx + event.clientX - dragging.x;
    state.ty = dragging.ty + event.clientY - dragging.y;
    updateTransform();
  });
  svg.addEventListener("pointerup", () => {
    dragging = null;
  });

  svg.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      nextFocus(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    const node = event.target.closest?.(".hk-tree-node");
    if (!node) return;
    const id = node.getAttribute("data-id");
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      model.set("selected", id);
      model.save_changes();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      toggleExpanded(id, true);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      toggleExpanded(id, false);
    }
  });

  function toggleExpanded(id, force) {
    const expanded = new Set(model.get("expanded") || []);
    const hasChildren = model.get("tree").some(
      (n) => n.id === id && n.has_children
    );
    if (!hasChildren) return;
    if (force === true) expanded.add(id);
    else if (force === false) expanded.delete(id);
    else if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    model.set("expanded", Array.from(expanded));
    model.save_changes();
  }

  function bindNode(node, item) {
    node.addEventListener("click", (event) => {
      if (event.target.closest(".hk-tree-toggle")) {
        event.stopPropagation();
        toggleExpanded(item.id);
        return;
      }
      model.set("selected", item.id);
      model.save_changes();
    });
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        model.set("selected", item.id);
        model.save_changes();
      } else if (event.key === "ArrowRight" && item.has_children) {
        event.preventDefault();
        toggleExpanded(item.id, true);
      } else if (event.key === "ArrowLeft" && item.has_children) {
        event.preventDefault();
        toggleExpanded(item.id, false);
      }
    });
  }

  function renderNodes() {
    const tree = model.get("tree") || [];
    const visibleNodes = tree.filter((n) => !n.disconnected);
    const disconnectedNodes = tree.filter((n) => n.disconnected);
    const width = Math.max(
      800,
      ...visibleNodes.map((n) => n.x * 180 + 260),
      disconnectedNodes.length ? 260 : 0
    );
    const height = Math.max(220, (tree.length + 2) * 64);
    state.width = width;
    state.height = height;
    const focusedId = svg.querySelector(".hk-tree-node:focus")?.getAttribute("data-id");
    viewport.innerHTML = "";

    const edgeGroup = el("g", { class: "hk-tree-edges" });
    const nodeGroup = el("g", { class: "hk-tree-nodes" });
    const byId = new Map(tree.map((n) => [n.id, n]));

    for (const item of tree) {
      if (!item.parent_id || !byId.has(item.parent_id)) continue;
      const parent = byId.get(item.parent_id);
      if (!parent || parent.disconnected || item.disconnected) continue;
      const x1 = parent.x * 180 + 120;
      const y1 = parent.y * 64 + 32;
      const x2 = item.x * 180 + 60;
      const y2 = item.y * 64 + 32;
      edgeGroup.appendChild(
        el("path", {
          class: "hk-tree-edge",
          d: `M ${x1} ${y1} C ${x1 + 60} ${y1}, ${x2 - 60} ${y2}, ${x2} ${y2}`,
        })
      );
    }

    if (disconnectedNodes.length) {
      const labelY = disconnectedNodes[0].y * 64 + 8;
      nodeGroup.appendChild(
        el(
          "text",
          { class: "hk-tree-section", x: 60, y: labelY },
          ["Disconnected records"]
        )
      );
    }

    for (const item of tree) {
      const x = item.x * 180 + 60;
      const y = item.y * 64 + 32;
      const classes = [
        "hk-tree-node",
        item.selected ? "is-selected" : "",
        item.ancestor ? "is-ancestor" : "",
        item.disconnected ? "is-disconnected" : "",
        item.search_match ? "is-search-match" : "",
      ].filter(Boolean).join(" ");
      const node = el("g", {
        class: classes,
        "data-id": item.id,
        tabindex: "0",
        role: "treeitem",
        "aria-selected": item.selected ? "true" : "false",
        "aria-expanded": item.has_children ? String(item.expanded) : null,
      });
      node.appendChild(
        el("rect", {
          class: "hk-tree-node-bg",
          x: x - 56,
          y: y - 18,
          width: 112,
          height: 36,
          rx: 8,
        })
      );
      node.appendChild(
        el(
          "text",
          { class: "hk-tree-label", x, y: y + 5, "text-anchor": "middle" },
          [item.id]
        )
      );
      if (item.has_children) {
        const toggle = el("g", {
          class: "hk-tree-toggle",
          transform: `translate(${x + 52} ${y})`,
        });
        toggle.appendChild(el("circle", { r: 9 }));
        toggle.innerHTML += iconPath(item.expanded ? "collapse" : "expand");
        node.appendChild(toggle);
      }
      bindNode(node, item);
      nodeGroup.appendChild(node);
      if (focusedId === item.id) queueMicrotask(() => node.focus());
    }

    viewport.append(edgeGroup, nodeGroup);

    list.innerHTML = "";
    for (const item of tree) {
      const row = el("div", {
        class: [
          "hk-tree-list-row",
          item.selected ? "is-selected" : "",
          item.ancestor ? "is-ancestor" : "",
          item.disconnected ? "is-disconnected" : "",
        ].filter(Boolean).join(" "),
        "data-id": item.id,
      });
      const toggle = el("button", {
        class: "hk-tree-toggle hk-tree-list-toggle",
        type: "button",
        "aria-label": item.expanded ? "Collapse" : "Expand",
        disabled: item.has_children ? null : "disabled",
      }, [item.expanded ? "−" : "+"]);
      const label = el(
        "button",
        { class: "hk-tree-list-label", type: "button" },
        [item.id]
      );
      toggle.addEventListener("click", () => toggleExpanded(item.id));
      label.addEventListener("click", () => {
        model.set("selected", item.id);
        model.save_changes();
      });
      row.append(toggle, label);
      list.appendChild(row);
    }
    updateTransform();
  }

  model.on("change:tree", renderNodes);
  model.on("change:selected", renderNodes);
  model.on("change:expanded", renderNodes);
  model.on("change:query", renderNodes);
  renderNodes();
  reset();
  return () => {
    model.off("change:tree", renderNodes);
    model.off("change:selected", renderNodes);
    model.off("change:expanded", renderNodes);
    model.off("change:query", renderNodes);
  };
}
export default { render };
"""

_CSS = r"""
.hk-record-graph {
  font-family: var(--hk-font-body, "Switzer", "Avenir Next", "Segoe UI", sans-serif);
  color: var(--hk-text, #26245c);
  background: var(--hk-surface, #ffffff);
  border: 1px solid var(--hk-border, #dddeec);
  border-radius: 10px;
  padding: 10px;
  position: relative;
}
.hk-tree-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.hk-tree-search {
  flex: 1 1 180px;
  min-width: 120px;
  padding: 6px 10px;
  border: 1px solid var(--hk-border, #dddeec);
  border-radius: 8px;
  font: inherit;
}
.hk-tree-button {
  padding: 6px 12px;
  border: 1px solid var(--hk-interactive, #5461a6);
  background: var(--hk-interactive, #5461a6);
  color: #ffffff;
  border-radius: 8px;
  cursor: pointer;
  font: inherit;
}
.hk-tree-svg {
  width: 100%;
  height: var(--hk-tree-height, 360px);
  min-height: 200px;
  touch-action: none;
  outline: none;
  background: var(--hk-surface-muted, #f6f6fb);
  border-radius: 8px;
  cursor: grab;
}
.hk-tree-svg:active { cursor: grabbing; }
.hk-tree-viewport { transition: none; }
.hk-tree-edge {
  fill: none;
  stroke: var(--hk-interactive, #5461a6);
  stroke-width: 1.5;
  opacity: 0.5;
}
.hk-tree-node { cursor: pointer; outline: none; }
.hk-tree-node:focus .hk-tree-node-bg {
  stroke: var(--hk-accent, #90ca42);
  stroke-width: 2.5;
}
.hk-tree-node-bg {
  fill: var(--hk-surface, #ffffff);
  stroke: var(--hk-interactive, #5461a6);
  stroke-width: 1.5;
}
.hk-tree-node.is-ancestor .hk-tree-node-bg { fill: #e8eaf6; }
.hk-tree-node.is-selected .hk-tree-node-bg {
  fill: var(--hk-interactive, #5461a6);
  stroke: var(--hk-dark, #26245c);
}
.hk-tree-node.is-selected .hk-tree-label { fill: #ffffff; font-weight: 700; }
.hk-tree-node.is-search-match .hk-tree-node-bg {
  stroke: var(--hk-accent, #90ca42);
  stroke-width: 3;
}
.hk-tree-node.is-disconnected .hk-tree-node-bg {
  stroke-dasharray: 5 4;
  opacity: 0.75;
}
.hk-tree-label {
  fill: var(--hk-text, #26245c);
  font-size: 12px;
  user-select: none;
}
.hk-tree-toggle { cursor: pointer; }
.hk-tree-toggle circle {
  fill: var(--hk-surface, #ffffff);
  stroke: var(--hk-interactive, #5461a6);
  stroke-width: 1.5;
}
.hk-tree-toggle path { fill: var(--hk-interactive, #5461a6); }
.hk-tree-section {
  fill: var(--hk-muted, #6f6d8a);
  font-size: 11px;
  font-style: italic;
}
.hk-tree-list { display: none; }
@media (max-width: 640px) {
  .hk-tree-svg { display: none; }
  .hk-tree-list { display: block; max-height: 360px; overflow: auto; }
  .hk-tree-list-row {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 6px 8px;
    border-bottom: 1px solid var(--hk-border, #dddeec);
  }
  .hk-tree-list-row.is-selected { background: var(--hk-interactive, #5461a6); }
  .hk-tree-list-row.is-selected .hk-tree-list-label { color: #ffffff; }
  .hk-tree-list-row.is-ancestor { background: #e8eaf6; }
  .hk-tree-list-label {
    border: none;
    background: transparent;
    cursor: pointer;
    font: inherit;
    color: var(--hk-text, #26245c);
  }
  .hk-tree-toggle {
    width: 26px;
    height: 26px;
    border-radius: 50%;
    border: 1px solid var(--hk-interactive, #5461a6);
    background: #ffffff;
    color: var(--hk-interactive, #5461a6);
    cursor: pointer;
  }
  .hk-tree-toggle:disabled { opacity: 0.35; cursor: default; }
}
"""


def tree_payload(layout: TreeLayout) -> list[dict[str, object]]:
    """Convert a :class:`TreeLayout` into the widget's wire payload."""
    payload: list[dict[str, object]] = []
    for record_id in layout.visible:
        node = layout.nodes[record_id]
        payload.append(
            {
                "id": node.id,
                "parent_id": node.parent_id,
                "x": node.x,
                "y": node.y,
                "selected": node.selected,
                "ancestor": node.ancestor_of_selection,
                "disconnected": node.disconnected,
                "expanded": node.expanded,
                "has_children": bool(node.children),
                "search_match": node.search_match,
            }
        )
    return payload


def record_descriptions(
    records: Sequence[RecordView],
) -> Mapping[str, str]:
    """Return a compact label for each record, preferring the description."""
    labels: dict[str, str] = {}
    for record in records:
        description = " ".join(record.markdown.split())
        labels[record.id] = description[:80] if description else record.id
    return labels


class RecordGraph(AnyWidget):
    """Interactive record tree with selection, expansion, and search."""

    _esm = _ESM
    _css = _CSS

    tree = traitlets.List(traitlets.Dict(), default_value=[]).tag(sync=True)
    selected = traitlets.Unicode(default_value="", allow_none=True).tag(sync=True)
    expanded = traitlets.List(traitlets.Unicode(), default_value=[]).tag(sync=True)
    query = traitlets.Unicode(default_value="").tag(sync=True)
    height = traitlets.Int(default_value=360).tag(sync=True)
