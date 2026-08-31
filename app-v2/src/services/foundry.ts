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

/**
 * A failed turn, in two registers.
 *
 * `message` is a sentence for the person in front of the screen; `detail` is the raw body for
 * the fold underneath. Same split as the answers themselves — the reason it exists here too is
 * that the app used to print `Foundry 400: {"error":{"message":"Agent task resp_0079… failed.
 * Troubleshooting guide: https://learn.microsoft.com/…"}}` on stage.
 */
export class SupervisorError extends Error {
  constructor(
    message: string,
    readonly detail: string,
    readonly attempts: number,
    readonly transient: boolean
  ) {
    super(message);
    this.name = 'SupervisorError';
  }
}

/** Error codes that mean "the hop died in flight", not "your request is malformed". */
const TRANSIENT_CODES = ['tool_user_error', 'server_error', 'rate_limit_exceeded'];
const TRANSIENT_STATUS = new Set([408, 409, 429, 500, 502, 503, 504]);

/**
 * Whether a failure is worth retrying.
 *
 * The interesting case is 400. A 400 normally means the caller is wrong and retrying is
 * pointless — except that a subordinate dying mid-A2A is *also* reported as 400, with
 * `"type":"invalid_request_error"` and `"code":"tool_user_error"`. So the type is useless here
 * and the CODE is the only discriminator. Measured on the app's own "why does Black Friday
 * Blast generate unsubscribes" suggestion: 4 successes and 1 `tool_user_error` in 5 identical
 * runs, the failure landing at 110s while successes ran 98–157s — so it is not a wall-clock
 * ceiling, and the same question genuinely answers on the next try.
 */
export function isTransient(status: number, body: string): boolean {
  if (TRANSIENT_STATUS.has(status)) return true;
  if (status !== 400) return false;
  return TRANSIENT_CODES.some((c) => body.includes(c));
}

/** What to tell the user. Never the payload — that goes to `detail`. */
export function humanMessage(status: number, body: string, attempts: number): string {
  if (status === 401 || status === 403)
    return "L’accès à l’agent a été refusé. La session a probablement expiré : rechargez la page pour vous reconnecter.";
  if (isTransient(status, body))
    return attempts > 1
      ? `L’appel entre agents s’est interrompu ${attempts} fois de suite. C’est intermittent côté service : reposez la question.`
      : "L’appel entre agents s’est interrompu en cours de route. C’est intermittent : reposez la question.";
  return 'Le superviseur n’a pas pu traiter cette question.';
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

/** One HTTP attempt. Resolves with the answer, or throws a `SupervisorError`. */
async function attemptOnce(
  agent: string,
  question: string,
  attempt: number
): Promise<AgentAnswer> {
  const token = await getToken(FOUNDRY_SCOPES);

  const res = await fetch(agentUrl(agent), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, input: question }),
  });

  const body = await res.text();
  if (!res.ok) {
    throw new SupervisorError(
      humanMessage(res.status, body, attempt),
      `Foundry ${res.status}: ${body.slice(0, 600)}`,
      attempt,
      isTransient(res.status, body)
    );
  }

  const json = JSON.parse(body);
  return { text: extractText(json), toolsFired: toolsFired(json), raw: json };
}

export interface AskOptions {
  agent?: string;
  /** How many HTTP attempts in total. Default 2 — see `isTransient`. */
  attempts?: number;
  /** Called before each attempt, so the caller can say "reprise" instead of spinning silently. */
  onAttempt?: (attempt: number) => void;
}

/**
 * Ask the supervisor, retrying a hop that died in flight.
 *
 * Two attempts, not more: a single answer to this question costs 98–157 seconds, so a third
 * attempt could leave someone watching a spinner for six minutes — worse on stage than the
 * failure it prevents. One retry takes the measured ~20% failure rate on the app's own
 * suggested RCA question down to roughly 4%.
 *
 * `onAttempt` is not optional decoration. A silent four-minute wait reads as a crash, so the
 * caller MUST surface the retry.
 */
export async function askSupervisor(
  question: string,
  opts: AskOptions = {}
): Promise<AgentAnswer> {
  if (!endpoint)
    throw new SupervisorError(
      'L’agent n’est pas configuré dans cette build.',
      'VITE_FOUNDRY_ENDPOINT is not set.',
      0,
      false
    );

  const agent = opts.agent ?? supervisor;
  const maxAttempts = Math.max(1, opts.attempts ?? 2);
  let last: SupervisorError | null = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    opts.onAttempt?.(attempt);
    try {
      return await attemptOnce(agent, question, attempt);
    } catch (e) {
      // A network-level throw (DNS, dropped socket, CORS) carries no status, and is exactly
      // the class of fault a second try clears. Treat it as transient.
      last =
        e instanceof SupervisorError
          ? e
          : new SupervisorError(
              attempt > 1
                ? `La connexion à l’agent a échoué ${attempt} fois de suite. Vérifiez le réseau, puis reposez la question.`
                : 'La connexion à l’agent a échoué. Reposez la question.',
              e instanceof Error ? e.message : String(e),
              attempt,
              true
            );
      if (!last.transient) break;
    }
  }

  throw last as SupervisorError;
}
