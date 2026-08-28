/**
 * The deployed agent chain, drawn.
 *
 * This component owns pixels and nothing else. Layers, rows, labels and the character caps come
 * from `topology.generated.json`, serialised from the Python `build_workflow()` that
 * `test_workflow_view.py` guards with 34 tests — including "zero edge crossings", which is why
 * `row` is read from the data and never from insertion order. Ordering by insertion once put
 * `corpus` above `data_agent` and crossed two arrows in the middle of the picture; in a diagram
 * whose entire job is "who talks to whom", a crossing reads as a wiring mistake.
 *
 * THE GEOMETRY IS HALF OF A CONTRACT
 * ----------------------------------
 *   box 214 wide in a 304 column  -> 90px of clear space for the protocol + connection name
 *   row pitch 132 for a 62-high box -> 70px between two boxes in the same column
 * The other half is `MAX_LABEL = 29` / `MAX_DETAIL = 34` / `MAX_EDGE_LABEL = 20` in
 * `portal/backend/workflow.py`. SVG text does not clip and does not wrap — it overflows and
 * collides — so those caps are computed against exactly these numbers. Move NW, colW or a
 * font-size here and all three must move there.
 *
 * The status text drawn inside a box is therefore deliberately terse ('absent', 'non vérifié').
 * The sentence explaining *why* is long and lives in the list underneath, where the browser
 * wraps it properly — the same detail/contract split the Python module makes.
 */
import type { NodeCheck, NodeStatus, WorkflowEdge, WorkflowNode } from '@/services/topology';

const NW = 214;
const NH = 62;
const COL_W = 304;

/**
 * Plane colour is data, not styling.
 *
 * Foundry amber vs Fabric teal is the architectural argument — "the data world stays on the data
 * side" — made visible, so the class comes from `plane`/`kind` and this component is not free to
 * decide a node looks Fabric-ish. The actual colours live in main.css so they can follow the
 * theme; only the hue is fixed here.
 */
function tone(node: WorkflowNode, status: NodeStatus): string {
  if (status === 'unchecked') return 'is-unchecked';
  if (status === 'absent') return 'is-absent';
  if (node.plane === 'foundry') return 'is-foundry';
  if (node.kind === 'ontology') return 'is-ontology';
  if (node.kind === 'semantic_model') return 'is-semantic';
  return 'is-fabric';
}

/** What fits in the box. The explanation goes in the list below, never here. */
function boxSub(node: WorkflowNode, status: NodeStatus): string {
  if (status === 'live') return node.detail || 'présent';
  return status === 'absent' ? 'absent' : 'non vérifié';
}

export function WorkflowDiagram({
  nodes,
  edges,
  checks,
}: {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  checks: Record<string, NodeCheck>;
}) {
  if (!nodes.length) return <p className="text-sm text-slate-500">Chaîne indisponible.</p>;

  const maxLayer = Math.max(...nodes.map((n) => n.layer));
  const perLayer = new Map<number, number>();
  for (const n of nodes) perLayer.set(n.layer, (perLayer.get(n.layer) ?? 0) + 1);
  const tallest = Math.max(...perLayer.values());

  const W = COL_W * (maxLayer + 1);
  const H = Math.max(300, 132 * tallest + 96);

  const pos = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    pos.set(n.id, {
      x: COL_W * (n.layer + 0.5),
      y: (H * (n.row + 1)) / ((perLayer.get(n.layer) ?? 1) + 1),
    });
  }

  const status = (id: string): NodeStatus => checks[id]?.status ?? 'unchecked';

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="wf-svg w-full h-auto" role="img"
         aria-label="Chaîne d’agents déployée">
      <defs>
        <marker id="wfArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" className="wf-arrow-head" />
        </marker>
      </defs>

      {edges.map((e) => {
        const a = pos.get(e.from);
        const b = pos.get(e.to);
        if (!a || !b) return null;
        const x1 = a.x + NW / 2;
        const x2 = b.x - NW / 2;
        const mx = (x1 + x2) / 2;
        const my = (a.y + b.y) / 2;
        // A hop is only drawn solid when both ends were actually observed. An unverified end
        // makes the hop unverified too: the arrow must not look stronger than its weakest box.
        const firm = status(e.from) === 'live' && status(e.to) === 'live';
        return (
          <g key={`${e.from}-${e.to}`}>
            <path
              d={`M${x1},${a.y} C${x1 + 28},${a.y} ${x2 - 28},${b.y} ${x2},${b.y}`}
              fill="none"
              className="wf-edge"
              strokeDasharray={firm ? undefined : '5 3'}
              markerEnd="url(#wfArrow)"
            />
            <text x={mx} y={my - 15} textAnchor="middle" fontSize="10.5" fontWeight="700"
                  className="wf-edge-protocol">
              {e.protocol}
            </text>
            {e.short ? (
              <text x={mx} y={my - 4} textAnchor="middle" fontSize="8.5" className="wf-edge-name">
                {e.short}
              </text>
            ) : null}
          </g>
        );
      })}

      {nodes.map((n) => {
        const p = pos.get(n.id)!;
        const s = status(n.id);
        const check = checks[n.id];
        return (
          <g key={n.id} className={`wf-node ${tone(n, s)}`}>
            <title>
              {`${n.label} — ${n.contract}` + (check?.note ? `\n${check.note}` : '')}
            </title>
            <rect
              x={p.x - NW / 2}
              y={p.y - NH / 2}
              width={NW}
              height={NH}
              rx={12}
              strokeWidth={1.5}
              // Dotted for "we could not ask", dashed for "we asked and it is not there".
              // Two different failures must not share one appearance.
              strokeDasharray={s === 'live' ? undefined : s === 'absent' ? '5 3' : '2 3'}
            />
            <text x={p.x} y={p.y - 3} textAnchor="middle" fontSize="13" fontWeight="700">
              {n.label}
            </text>
            <text x={p.x} y={p.y + 14} textAnchor="middle" fontSize="9.5" opacity="0.85">
              {boxSub(n, s)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
