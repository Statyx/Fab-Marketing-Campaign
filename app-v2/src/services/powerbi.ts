/**
 * Power BI REST client — DAX against the semantic model, browser-side, read-only.
 *
 * This is how the app gets its numbers, and the reason is a rule rather than a preference:
 * every aggregate must be evaluated by the semantic model, never recomputed in TypeScript.
 * A number the app derives itself is a second definition of the business logic that can drift
 * from the model the report and the data agent both read — the "second source of truth" trap.
 *
 * The Foundry supervisor answers the same questions in natural language, but it takes ~40-60 s
 * per answer. That is fine for an explanation and unusable for a figure rendered on page load,
 * which is why both routes exist side by side rather than one replacing the other.
 *
 * `executeQueries` cannot write: DAX is an evaluation language, so this endpoint is read-only
 * by construction, not merely by convention.
 */
import { getToken, POWERBI_SCOPES } from './msal';

const API = 'https://api.powerbi.com/v1.0/myorg';

export const semanticModelId = import.meta.env.VITE_SEMANTIC_MODEL_ID;

export const powerbiConfigured = Boolean(semanticModelId);

export type DaxValue = string | number | boolean | null;
export type DaxRow = Record<string, DaxValue>;

/** Run a DAX query and return its first result table's rows. */
export async function executeDax(dax: string, datasetId = semanticModelId): Promise<DaxRow[]> {
  if (!datasetId) throw new Error('VITE_SEMANTIC_MODEL_ID is not set.');

  const token = await getToken(POWERBI_SCOPES);
  const res = await fetch(`${API}/datasets/${datasetId}/executeQueries`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      queries: [{ query: dax }],
      serializerSettings: { includeNulls: true },
    }),
  });

  const body = await res.text();
  if (!res.ok) throw new Error(`Power BI ${res.status}: ${body.slice(0, 600)}`);

  const parsed = JSON.parse(body) as {
    results?: Array<{ tables?: Array<{ rows?: DaxRow[] }> }>;
  };
  return parsed.results?.[0]?.tables?.[0]?.rows ?? [];
}

/** Convenience for the common case: a query shaped `EVALUATE ROW(...)` with one scalar. */
export async function executeScalar(dax: string): Promise<DaxValue> {
  const [row] = await executeDax(dax);
  if (!row) throw new Error('La requête DAX n’a retourné aucune ligne.');
  const values = Object.values(row);
  if (values.length !== 1) {
    throw new Error(`Attendu une seule colonne, ${values.length} reçues.`);
  }
  return values[0];
}

/**
 * Customers in the High or Critical risk bands — asked twice, on purpose.
 *
 * `[Customers at Risk]` is the model measure the app's screens actually bind to. The explicit
 * CALCULATE next to it reaches the underlying *column*, which a measure reference never
 * exercises: a broken column reference kills a visual just as hard as a broken measure, and
 * `EVALUATE ROW("v",[M])` would sail straight past it. Both must agree — and both must equal
 * the figure the Foundry supervisor reaches through its own route.
 *
 * The filter is spelled out rather than left implicit because "at risk" has three defensible
 * readings in this model (risk_band='High', churn_risk_score >= 65, lifecycle_stage='at_risk'),
 * and a figure quoted without saying which one it used is not reproducible.
 */
export const DAX_AT_RISK = `
EVALUATE
ROW(
  "Measure", [Customers at Risk],
  "Column", CALCULATE(
    COUNTROWS('crm_customer_profile'),
    'crm_customer_profile'[risk_band] IN {"High","Critical"}
  )
)`;
