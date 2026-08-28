/**
 * Foundry agent client — browser-side.
 *
 * The exact HTTP surface below was not guessed: it was read off the Python SDK by printing
 * `AIProjectClient.get_openai_client(agent_name=...).base_url`, which yields
 *   {project_endpoint}/agents/{agent}/endpoint/protocols/openai/
 * with `api-version=v1` as a query parameter. A CORS preflight against this exact
 * agent-scoped path returns `Access-Control-Allow-Origin: *` and allows POST + authorization,
 * which is why this call can run from the browser with no relay in between.
 *
 * The supervisor orchestrates two A2A subordinates (numbers via Fabric, verbatims via the VoC
 * corpus). It is deliberately slow — tens of seconds per answer — because routing happens on
 * the data side. Callers must render a pending state rather than assume a hung request.
 */
import { FOUNDRY_SCOPES, getToken } from './msal';

const endpoint = import.meta.env.VITE_FOUNDRY_ENDPOINT;
const supervisor = import.meta.env.VITE_FOUNDRY_SUPERVISOR ?? 'Marketing-Supervisor';
const model = import.meta.env.VITE_FOUNDRY_MODEL ?? 'gpt-5.4';

export const foundryConfigured = Boolean(endpoint);

export interface AgentAnswer {
  text: string;
  /** Connection names of the A2A calls that fired, in order. */
  toolsFired: string[];
  raw: unknown;
}

function agentUrl(agent: string): string {
  const base = endpoint?.replace(/\/$/, '');
  return `${base}/agents/${agent}/endpoint/protocols/openai/responses?api-version=v1`;
}

function extractText(resp: any): string {
  if (typeof resp?.output_text === 'string' && resp.output_text) return resp.output_text;
  const parts: string[] = [];
  for (const item of resp?.output ?? []) {
    if (item?.type === 'message') {
      for (const c of item.content ?? []) {
        if (c?.type === 'output_text' && typeof c.text === 'string') parts.push(c.text);
      }
    }
  }
  return parts.join('\n');
}

/**
 * Which subordinate actually ran.
 *
 * Both subordinates surface as `a2a_preview_call`, so the item *type* no longer identifies
 * which one answered — only `name` (the connection name) does. Reading the type alone would
 * happily report success while the supervisor asked the document corpus for a number.
 */
function toolsFired(resp: any): string[] {
  return (resp?.output ?? [])
    .filter(
      (i: any) =>
        typeof i?.type === 'string' &&
        i.type.startsWith('a2a_preview_call') &&
        !i.type.endsWith('_output')
    )
    .map((i: any) => String(i?.name ?? ''))
    .filter(Boolean);
}

/**
 * What the project actually contains — used to verify the architecture diagram against the
 * tenant instead of against a file.
 *
 * Both routes were probed against the live project rather than inferred from the SDK:
 *   GET {endpoint}/agents?api-version=v1        -> 200, the three A2A participants
 *   GET {endpoint}/vectorStores?api-version=v1  -> 404   (the camelCase guess is wrong)
 *   GET {endpoint}/vector_stores?api-version=v1 -> 200, the VoC corpus
 * The 404 is the reason this is written down: the plausible spelling is the broken one, and a
 * caller that assumed it would have reported a deployed corpus as missing.
 *
 * `api-version=v1` is the literal string, not a date — a date-shaped value returns 400 and
 * reads like a broken route.
 */
async function foundryList(path: string): Promise<{ name?: string; id?: string }[]> {
  if (!endpoint) throw new Error('VITE_FOUNDRY_ENDPOINT is not set.');
  const token = await getToken(FOUNDRY_SCOPES);
  const base = endpoint.replace(/\/$/, '');
  const res = await fetch(`${base}${path}?api-version=v1`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await res.text();
  if (!res.ok) throw new Error(`Foundry ${res.status}: ${body.slice(0, 300)}`);
  const json = JSON.parse(body);
  return json?.value ?? json?.data ?? [];
}

export const listAgents = () => foundryList('/agents');
export const listVectorStores = () => foundryList('/vector_stores');

export async function askSupervisor(
  question: string,
  agent: string = supervisor
): Promise<AgentAnswer> {
  if (!endpoint) throw new Error('VITE_FOUNDRY_ENDPOINT is not set.');
  const token = await getToken(FOUNDRY_SCOPES);

  const res = await fetch(agentUrl(agent), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, input: question }),
  });

  const body = await res.text();
  if (!res.ok) throw new Error(`Foundry ${res.status}: ${body.slice(0, 600)}`);

  const json = JSON.parse(body);
  return { text: extractText(json), toolsFired: toolsFired(json), raw: json };
}
