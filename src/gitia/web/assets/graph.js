"use strict";

/* Commit graph: lanes laid out over the rows actually displayed, then painted in one SVG pass.
   `layout` stays free of the DOM so tests/test_graph_layout.py can exercise it under node. */

const Graph = (() => {
  const PALETTE = [
    "#4c9aff", "#5ed69a", "#f2994a", "#c792ea",
    "#35c9c0", "#ff7b8a", "#e5c04b", "#7f8cff",
  ];

  const SVG_NS = "http://www.w3.org/2000/svg";
  const PAD = 14;
  const GAP_MIN = 9;
  const GAP_MAX = 16;
  const FADE = 22;

  const shapeOf = (row) =>
    (row.kind !== "commit" ? "pending" : (row.parents || []).length > 1 ? "merge" : "commit");

  function laneLayout(rows) {
    const lanes = [];
    const laid = [];
    let seed = 0;
    let columns = 1;

    const findLane = (sha) => lanes.findIndex((lane) => lane && lane.sha === sha);
    const freeColumn = () => {
      const hole = lanes.indexOf(null);
      if (hole !== -1) return hole;
      lanes.push(null);
      return lanes.length - 1;
    };
    // A colour already on screen would read as the same branch, so an unused one comes first.
    const nextColor = () => {
      const taken = new Set(lanes.filter(Boolean).map((lane) => lane.color));
      for (let step = 0; step < PALETTE.length; step += 1) {
        const color = PALETTE[(seed + step) % PALETTE.length];
        if (!taken.has(color)) {
          seed += step + 1;
          return color;
        }
      }
      return PALETTE[seed++ % PALETTE.length];
    };

    rows.forEach((row, index) => {
      const before = lanes.slice();
      const edges = [];
      let node;

      if (row.kind === "commit") {
        let column = findLane(row.sha);
        let lane = lanes[column];
        // No column expects this commit: it is a branch tip, so it opens one.
        if (column === -1) {
          lane = { sha: row.sha, color: nextColor() };
          column = freeColumn();
        }
        lanes[column] = null;

        const outgoing = [];
        (row.parents || []).forEach((parent, position) => {
          const existing = findLane(parent);
          if (existing !== -1) {
            outgoing.push({ column: existing, color: lanes[existing].color });
            return;
          }
          // The first parent keeps the column and the colour, so a branch reads as one line.
          const target = position === 0 && lanes[column] === null ? column : freeColumn();
          const color = position === 0 ? lane.color : nextColor();
          lanes[target] = { sha: parent, color };
          outgoing.push({ column: target, color });
        });

        before.forEach((entry, source) => {
          if (!entry) return;
          if (entry === lane) {
            edges.push({ from: source, to: source, kind: "in", color: lane.color });
            return;
          }
          const destination = findLane(entry.sha);
          if (destination !== -1) {
            edges.push({ from: source, to: destination, kind: "pass", color: entry.color });
          }
        });
        outgoing.forEach((out) => {
          edges.push({ from: column, to: out.column, kind: "out", color: out.color });
        });

        node = { column, color: lane.color, shape: shapeOf(row) };
      } else {
        // Changes and the working tree are not commits: they ride the lane of the commit below.
        const next = rows.slice(index + 1).find((candidate) => candidate.kind === "commit");
        let column = next ? findLane(next.sha) : -1;
        if (column === -1) {
          const hole = lanes.indexOf(null);
          column = hole === -1 ? lanes.length : hole;
        }
        before.forEach((entry, source) => {
          if (entry) edges.push({ from: source, to: source, kind: "pass", color: entry.color });
        });
        if (!before[column]) edges.push({ from: column, to: column, kind: "stub" });
        node = { column, shape: shapeOf(row) };
      }

      while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
      const width = Math.max(before.length, lanes.length, node.column + 1);
      columns = Math.max(columns, width);
      laid.push({ row, node, edges, width });
    });

    return { rows: laid, columns };
  }

  /* A filtered list is no longer contiguous history: drawing edges through the gaps would lie. */
  function flatLayout(rows) {
    return {
      rows: rows.map((row) => ({
        row,
        node: { column: 0, color: row.kind === "commit" ? PALETTE[0] : undefined, shape: shapeOf(row) },
        edges: [],
        width: 1,
      })),
      columns: 1,
    };
  }

  const layout = (rows, { flat = false } = {}) => (flat ? flatLayout(rows) : laneLayout(rows));

  /* How wide the gutter has to be, never more than the budget the caller allows. */
  function measure(columns, budget) {
    const span = Math.max(0, columns - 1);
    const room = Math.max(0, budget - PAD * 2);
    const gap = span === 0 ? GAP_MAX : Math.min(GAP_MAX, Math.max(GAP_MIN, Math.floor(room / span)));
    return { gap, width: PAD * 2 + span * gap };
  }

  function node(svg, attributes) {
    const shape = document.createElementNS(SVG_NS, attributes.tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (name !== "tag") shape.setAttribute(name, String(value));
    }
    svg.append(shape);
    return shape;
  }

  function edgePath(edge, x1, x2, top, height) {
    const middle = top + height / 2;
    const bottom = top + height;
    if (edge.kind === "in") return `M${x1} ${top} L${x1} ${middle}`;
    if (edge.kind === "stub") return `M${x1} ${middle} L${x1} ${bottom}`;
    if (edge.kind === "out") {
      if (x1 === x2) return `M${x1} ${middle} L${x1} ${bottom}`;
      return `M${x1} ${middle} C${x1} ${middle + height * 0.34}, ${x2} ${bottom - height * 0.22}, ${x2} ${bottom}`;
    }
    if (x1 === x2) return `M${x1} ${top} L${x1} ${bottom}`;
    return `M${x1} ${top} C${x1} ${top + height * 0.58}, ${x2} ${bottom - height * 0.28}, ${x2} ${bottom}`;
  }

  function paintNode(svg, placed, x, y, radius, options) {
    const color = placed.node.color || options.pendingColor(placed.row);
    if (placed.node.shape === "pending") {
      const size = radius * 1.25;
      node(svg, {
        tag: "path",
        d: `M${x} ${y - size} L${x + size} ${y} L${x} ${y + size} L${x - size} ${y} Z`,
        fill: color, stroke: color, "stroke-width": 1.5, "stroke-linejoin": "round",
      });
    } else {
      if (placed.node.shape === "merge") {
        node(svg, {
          tag: "circle", cx: x, cy: y, r: radius + 2.5,
          fill: "none", stroke: color, "stroke-width": 1.5, "stroke-opacity": 0.35,
        });
      }
      node(svg, { tag: "circle", cx: x, cy: y, r: radius, fill: color });
    }
    if (options.isSelected(placed.row)) {
      node(svg, {
        tag: "circle", cx: x, cy: y, r: radius + 4.5,
        fill: "none", stroke: "#e8ecf4", "stroke-width": 1.5, "stroke-opacity": 0.6,
      });
    }
  }

  /* Paints the whole list in one SVG: every band ends where the next one starts, so lines join. */
  function paint(svg, laid, options) {
    const { rowHeight, gap, width } = options;
    const height = Math.max(rowHeight, laid.rows.length * rowHeight);
    const x = (column) => PAD + column * gap;
    const radius = Math.max(3.5, Math.min(5, gap * 0.32));

    while (svg.firstChild) svg.firstChild.remove();
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

    const defs = document.createElementNS(SVG_NS, "defs");
    const gradient = node(defs, { tag: "linearGradient", id: "graph-fade", x1: 0, y1: 0, x2: 0, y2: 1 });
    node(gradient, { tag: "stop", offset: 0, "stop-color": "#fff" });
    node(gradient, { tag: "stop", offset: 1, "stop-color": "#000" });
    const mask = node(defs, { tag: "mask", id: "graph-mask" });
    const solid = Math.max(0, height - FADE);
    node(mask, { tag: "rect", x: 0, y: 0, width, height: solid, fill: "#fff" });
    node(mask, { tag: "rect", x: 0, y: solid, width, height: FADE, fill: "url(#graph-fade)" });
    svg.append(defs);

    // Lines fade into the bottom edge: history continues past the window instead of stopping dead.
    const lines = document.createElementNS(SVG_NS, "g");
    lines.setAttribute("mask", "url(#graph-mask)");
    lines.setAttribute("fill", "none");
    lines.setAttribute("stroke-width", "1.75");
    lines.setAttribute("stroke-linecap", "round");
    svg.append(lines);

    laid.rows.forEach((placed, index) => {
      const top = index * rowHeight;
      for (const edge of placed.edges) {
        const color = edge.color || options.pendingColor(placed.row);
        const path = node(lines, {
          tag: "path",
          d: edgePath(edge, x(edge.from), x(edge.to), top, rowHeight),
          stroke: color,
        });
        if (edge.kind === "stub") path.setAttribute("stroke-dasharray", "2 4");
      }
    });

    laid.rows.forEach((placed, index) => {
      paintNode(svg, placed, x(placed.node.column), index * rowHeight + rowHeight / 2, radius, options);
    });
  }

  return { layout, measure, paint };
})();

if (typeof module !== "undefined" && module.exports) module.exports = Graph;
