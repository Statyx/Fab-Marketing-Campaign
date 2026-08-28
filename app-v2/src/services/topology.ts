/**
 * Verify the architecture diagram against the tenant, not against a file.
 *
 * V1 drew this chain from `state.json` — the receipt the deploy scripts left behind — and its
 * own docstring drew the line: *deployment is not reachability*, so it never claimed more than
 * "a JSON file says this was created". V2 has no backend to read that file, but it does hold a
 * Fabric token and a Foundry token in the browser, so it can ask the services directly. That
 * makes the claim stronger than V1's, and it immediately earned its keep: `config.yaml`
 * declares the VoC corpus as `voc-corpus` while the project actually contains
 * `voc-marketing-churn`. V1 rendered the declared name in green because an id existed in the
 * receipt; this asks, and reports the mismatch.
 *
 * THREE STATES, NOT TWO
 * ---------------------
 * `live` and `absent` are claims about the tenant. `unchecked` is a claim about *us*: the probe
 * did not answer (expired token, CORS, a route that moved). Collapsing it into `absent` would
 * turn our own failure into an accusation against the deployment — the exact inversion the grey
 * "non déployé" box was invented to avoid. A failed probe is not evidence of absence.
 *
 * Nothing here decides the topology. Shape, labels, layers and rows come from
 * `topology.generated.json`, serialised from the Python `build_workflow()` that 34 tests guard.
 * This module only answers "is it there?".
 */
import { listItems, type FabricItem } from './fabric';
import { listAgents, listVectorStores } from './foundry';

import topology from '@/data/topology.generated.json';

export type NodeStatus = 'live' | 'absent' | 'unchecked';

export interface WorkflowNode {
  id: string;
  label: string;
  plane: 'foundry' | 'fabric';
  kind: string;
  detail: string;
  contract: string;
  deployed: boolean;
  ref: string;
  layer: number;
  row: number;
}

export interface WorkflowEdge {
  from: string;
  to: string;
  protocol: string;
  label: string;
  short: string;
  note: string;
}

export interface OntologyEntity {
  name: string;
  table: string;
  properties: number;
}
export interface OntologyRelationship {
  name: string;
  from: string;
  to: string;
}

export interface NodeCheck {
  status: NodeStatus;
  /** What the service returned for it — the Fabric item type, or the matched name. */
  observed?: string;
  /** Why it is `unchecked`, or what was found instead when it is `absent`. */
  note?: string;
}

export interface Verification {
  checks: Record<string, NodeCheck>;
  warnings: string[];
  /** True once every probe has answered — the diagram says so rather than implying it. */
  complete: boolean;
}

export const workflow = topology.workflow as unknown as {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  crossings: number;
  planes: Record<string, string>;
  projectEndpoint: string;
};

export const ontology = topology.ontology as unknown as {
  entities: OntologyEntity[];
  relationships: OntologyRelationship[];
};

/**
 * Node kind to the `type` the Fabric items API returns.
 *
 * Disambiguation, not decoration: `LH_Customer360` comes back **twice** — once as `Lakehouse`
 * and once as the `SQLEndpoint` that shadows its name. Matching on the name alone would report
 * whichever the API happened to list first.
 */
const FABRIC_TYPE: Record<string, string> = {
  data_agent: 'DataAgent',
  semantic_model: 'SemanticModel',
  ontology: 'Ontology',
  lakehouse: 'Lakehouse',
};

function matchFabric(node: WorkflowNode, items: FabricItem[]): NodeCheck {
  const byName = items.filter((i) => i.displayName === node.label);
  if (byName.length) {
    const want = FABRIC_TYPE[node.kind];
    const best = byName.find((i) => i.type === want) ?? byName[0];
    return { status: 'live', observed: best.type };
  }
  const sameKind = items.filter((i) => i.type === FABRIC_TYPE[node.kind]);
  return {
    status: 'absent',
    note: sameKind.length
      ? `absent du workspace ; il contient ${sameKind.map((i) => i.displayName).join(', ')}`
      : 'absent de la liste des items du workspace',
  };
}

function matchByName(
  node: WorkflowNode,
  found: { name?: string; id?: string }[],
  family: string
): NodeCheck {
  const names = found.map((f) => f.name ?? f.id ?? '').filter(Boolean);
  if (names.includes(node.label)) return { status: 'live', observed: node.label };
  return {
    status: 'absent',
    note: names.length
      ? `absent du projet ; il contient ${names.join(', ')}`
      : `aucun ${family} dans le projet`,
  };
}

/**
 * Ask the two services what exists, then answer per node.
 *
 * `allSettled`, never `all`: the Fabric and Foundry planes fail independently, and one dead
 * token must not blank the half of the picture that is still answerable.
 */
export async function verifyTopology(): Promise<Verification> {
  const [itemsR, agentsR, storesR] = await Promise.allSettled([
    listItems(),
    listAgents(),
    listVectorStores(),
  ]);

  const reason = (r: PromiseSettledResult<unknown>) =>
    r.status === 'rejected' ? String((r.reason as Error)?.message ?? r.reason) : '';

  const checks: Record<string, NodeCheck> = {};
  for (const node of workflow.nodes) {
    if (node.plane === 'fabric') {
      checks[node.id] =
        itemsR.status === 'fulfilled'
          ? matchFabric(node, itemsR.value)
          : { status: 'unchecked', note: `workspace non interrogeable — ${reason(itemsR)}` };
    } else if (node.kind === 'store') {
      checks[node.id] =
        storesR.status === 'fulfilled'
          ? matchByName(node, storesR.value, 'magasin vectoriel')
          : { status: 'unchecked', note: `projet non interrogeable — ${reason(storesR)}` };
    } else {
      checks[node.id] =
        agentsR.status === 'fulfilled'
          ? matchByName(node, agentsR.value, 'agent')
          : { status: 'unchecked', note: `projet non interrogeable — ${reason(agentsR)}` };
    }
  }

  return {
    checks,
    warnings: buildWarnings(checks),
    complete: [itemsR, agentsR, storesR].every((r) => r.status === 'fulfilled'),
  };
}

/**
 * The gaps worth naming.
 *
 * Ported from `workflow.py`, with one addition the Python could not make: it compared a config
 * name to a receipt written by the same run, so a name that matches nothing in the tenant looked
 * perfectly healthy. Here the comparison is against the service, so `absent` carries what the
 * service *does* contain — which is how the `voc-corpus` / `voc-marketing-churn` drift surfaced.
 */
function buildWarnings(checks: Record<string, NodeCheck>): string[] {
  const out: string[] = [];
  const byId = new Map(workflow.nodes.map((n) => [n.id, n]));
  const st = (id: string) => checks[id]?.status;

  // Silent and total: one connection name for both tools and the supervisor can no longer tell
  // the corpus from the front door — both surface as `a2a_preview_call`.
  const a2a = workflow.edges.filter((e) => e.protocol === 'A2A' && e.label);
  if (a2a.length === 2 && a2a[0].label === a2a[1].label) {
    out.push(
      `Les deux connexions A2A portent le même nom (${a2a[0].label}) : le superviseur ne peut ` +
        'plus distinguer ses deux outils.'
    );
  }

  if (st('supervisor') === 'live' && (st('front_door') !== 'live' || st('voc') !== 'live')) {
    out.push(
      'Le superviseur est déployé mais un subordonné manque : les questions routées vers lui ' +
        'échoueront à l’invocation.'
    );
  }

  for (const [id, c] of Object.entries(checks)) {
    const label = byId.get(id)?.label ?? id;
    if (c.status === 'absent') out.push(`${label} : ${c.note ?? 'absent'}.`);
    else if (c.status === 'unchecked') out.push(`${label} : non vérifié — ${c.note ?? ''}`.trim());
  }
  return out;
}
