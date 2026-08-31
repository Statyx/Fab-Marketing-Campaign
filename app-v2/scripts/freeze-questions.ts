/**
 * Emit the questions worth freezing, computed from the registry by the *same* picker the UI uses.
 *
 * The list is not written by hand and must never be: the cockpit decides which questions it
 * offers by running `pickVaried` over `PERSONAS`, so any hand-maintained copy would drift the
 * day someone reorders a persona's suggestions — and the drift is silent. A frozen answer whose
 * question no longer appears is dead weight; a question that appears with no frozen answer just
 * falls through to a live call. Neither fails loudly, which is exactly why the list is derived.
 *
 * Depth 2, not 1. The user asked for the three opening questions, but the rail refills after the
 * first answer and the second click is just as much part of the demo. So this walks one click
 * further: the openers, plus the follow-ups reachable after any single first click. That is the
 * whole set a presenter can reach in two clicks — 20-25 questions rather than 12.
 *
 *   npx tsx scripts/freeze-questions.ts
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { PERSONAS, pickVaried, type Suggestion } from '../src/data/personas';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, '../src/data/frozen-questions.generated.json');

interface Entry {
  persona: string;
  q: string;
  expects: string;
  /** 1 = shown before anyone asks anything. 2 = reachable on the next click. */
  depth: number;
}

const entries: Entry[] = [];
const seen = new Set<string>();

function add(persona: string, s: Suggestion, depth: number) {
  const key = `${persona}::${s.q}`;
  if (seen.has(key)) return;
  seen.add(key);
  entries.push({ persona, q: s.q, expects: s.expects, depth });
}

for (const p of PERSONAS) {
  const openers = pickVaried(p.suggestions, 3);
  for (const s of openers) add(p.key, s, 1);

  // One click deep: whichever opener was clicked, the rail recomputes over the rest.
  for (const clicked of openers) {
    const rest = p.suggestions.filter((s) => s.q !== clicked.q);
    for (const s of pickVaried(rest, 3)) add(p.key, s, 2);
  }
}

writeFileSync(OUT, `${JSON.stringify({ generatedBy: 'scripts/freeze-questions.ts', entries }, null, 2)}\n`, 'utf8');

const d1 = entries.filter((e) => e.depth === 1).length;
console.log(`${entries.length} questions (${d1} openers, ${entries.length - d1} follow-ups) -> ${OUT}`);
