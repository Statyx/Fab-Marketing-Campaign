/**
 * The ontology, drawn.
 *
 * Derived, never typed: entities and relationships are read out of `src/deploy_ontology.py` by
 * AST at build time, so the picture is the deploy script's own declaration rather than a second
 * description of it that can drift. If someone adds an entity to the deploy script, a rebuild
 * shows it; if nobody does, nothing here invents one.
 *
 * Geometry is V1's, unchanged: 760x420, nodes on an ellipse inset 110x58 so the widest pill
 * still clears the frame. One deliberate addition — the relationship labels get a background
 * halo (`paint-order: stroke`), because nine chords across eight points put several midpoints
 * in the same neighbourhood and V1's flat text became unreadable where they met. The halo fixes
 * legibility without moving anything, so the layout claim stays the same one V1 made.
 */
import type { OntologyEntity, OntologyRelationship } from '@/services/topology';

const W = 760;
const H = 420;

export function GraphDiagram({
  entities,
  relationships,
}: {
  entities: OntologyEntity[];
  relationships: OntologyRelationship[];
}) {
  if (!entities.length) return <p className="text-sm text-slate-500">Graphe indisponible.</p>;

  const cx = W / 2;
  const cy = H / 2;
  const rx = W / 2 - 110;
  const ry = H / 2 - 58;

  const pos = new Map<string, { x: number; y: number }>();
  entities.forEach((e, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / entities.length;
    pos.set(e.name, { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) });
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="graph-svg w-full h-auto" role="img"
         aria-label="Graphe des relations">
      <defs>
        <marker id="gArrow" viewBox="0 0 10 10" refX="26" refY="5" markerWidth="6"
                markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" className="graph-arrow-head" />
        </marker>
      </defs>

      {relationships.map((r, i) => {
        const a = pos.get(r.from);
        const b = pos.get(r.to);
        if (!a || !b) return null;
        return (
          <g key={`${r.name}-${i}`}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="graph-edge"
                  strokeWidth={1.4} markerEnd="url(#gArrow)" />
            <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} textAnchor="middle"
                  fontSize="8.5" className="graph-edge-label">
              {r.name}
            </text>
          </g>
        );
      })}

      {entities.map((e) => {
        const p = pos.get(e.name)!;
        const w = Math.max(74, e.name.length * 8 + 22);
        return (
          <g key={e.name} className="graph-node">
            <title>{`${e.name} ← ${e.table} · ${e.properties} propriétés`}</title>
            <rect x={p.x - w / 2} y={p.y - 15} width={w} height={30} rx={15} />
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="11.5" fontWeight="600">
              {e.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
