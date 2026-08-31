/**
 * Split a supervisor reply into what a marketing lead reads and what an analyst asks for.
 *
 * ── Why this exists ─────────────────────────────────────────────────────────────────────
 * The supervisor prompt requires every figure to travel with its scope, and the model obeyed
 * that literally: the lead sentence came back built out of `crm_customer_profile[risk_band] IN
 * {"High","Critical"}` and friends, followed by a six-bullet form restating the same fields.
 * True, sourced, and unreadable on a screen in front of a customer.
 *
 * Deleting the provenance was never an option — it is what separates this demo from a chatbot
 * that sounds confident. So the prompt now emits it as a trailing block behind a fixed marker,
 * and this function moves that block out of the prose. The app puts it behind a button.
 *
 * ── The one rule that governs the parsing ───────────────────────────────────────────────
 * **A marker that fails to match must never hide content.** Everything the splitter does not
 * recognise stays in `body`, visible. That is why the match is deliberately asymmetric:
 *
 *  - The prompt is STRICT: the block opens with exactly `### SOURCE`, in English, in capitals,
 *    whatever the language of the answer.
 *  - The parser is TOLERANT: heading hashes, bold markers, a trailing colon and the non-breaking
 *    space French typography puts before it are all stripped before comparison, and the match is
 *    case-insensitive. A model that emits `**Source :**` still gets its detail filed correctly
 *    rather than printed as prose.
 *
 * It splits on the LAST marker line, not the first. The word "source" is ordinary French and
 * English and can legitimately appear mid-answer ("selon la source…"); splitting on the first
 * match would then swallow the rest of the answer into a collapsed panel, which is exactly the
 * failure this rule exists to prevent. The block is always last, so the last match is it.
 */

/**
 * Is this line nothing but the word SOURCE, however it was dressed?
 *
 * Written as a normalise-then-compare rather than one regex on purpose. The first version was a
 * single pattern and it missed `**Source :**` — the French typographic form puts the colon
 * INSIDE the bold markers, and the pattern expected it outside. That class of near-miss is
 * invisible in review and silent at runtime, so the decoration is stripped instead of enumerated
 * in an order that has to be guessed right.
 *
 * `\s` covers the non-breaking space French typography puts before a colon.
 */
const DECORATION = /[#*_\s:\u2014-]/g;

function isMarkerLine(line: string): boolean {
  const bare = line.replace(DECORATION, '').toLowerCase();
  return bare === 'source' || bare === 'sources';
}

export interface SplitAnswer {
  /** The prose. Always non-empty when the input was non-empty. */
  body: string;
  /** The provenance block, marker line excluded. `null` when the model emitted none. */
  source: string | null;
}

export function splitAnswer(text: string): SplitAnswer {
  const raw = text ?? '';
  const lines = raw.split('\n');

  let at = -1;
  for (let i = 0; i < lines.length; i += 1) if (isMarkerLine(lines[i])) at = i;

  if (at === -1) return { body: raw.trim(), source: null };

  const body = lines.slice(0, at).join('\n').trim();
  const source = lines.slice(at + 1).join('\n').trim();

  // A marker with nothing under it, or with everything above it missing, is a malformed reply.
  // In both cases the safe read is "this was not the block" — show the text as written rather
  // than render an empty panel or, worse, an empty answer.
  if (!source || !body) return { body: raw.trim(), source: null };

  return { body, source };
}
