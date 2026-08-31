/**
 * Which questions the cockpit offers before anyone has typed anything.
 *
 * These tests exist because of a regression that broke nothing. The starter rail was capped at
 * three with `slice(0, 3)`, and the persona registry happens to list every persona's numeric
 * questions first and its graph questions last. The cap therefore removed the ontology from the
 * product: no click anywhere in the cockpit could reach it any more, the graph stopped being
 * demonstrable, and not one test, type or build complained — the openers simply became three
 * variations of the same question. It was caught by a human looking at the screen.
 *
 * A cap over an ordered list does not sample the list, it truncates it. So the invariant pinned
 * here is not "pickVaried returns three items", it is *coverage*: whatever the registry grows
 * into, and whatever order it is written in, a capability that exists must remain reachable from
 * the openers.
 */
import { describe, expect, it } from 'vitest';

import { PERSONAS, pickVaried, type Source, type Suggestion } from '@/data/personas';

/** How many chips the cockpit shows, in both the opening rail and the follow-up row. */
const SHOWN = 3;

function suggestion(q: string, expects: Source): Suggestion {
  return { q, expects };
}

describe('pickVaried', () => {
  it('reaches a family that sorts last, which slice(0, n) could not', () => {
    // The exact shape that hid the graph: three numeric questions ahead of the only graph one.
    const pool = [
      suggestion('a', 'model'),
      suggestion('b', 'model'),
      suggestion('c', 'model'),
      suggestion('d', 'ontology'),
    ];
    expect(pool.slice(0, SHOWN).map((s) => s.expects)).not.toContain('ontology');
    expect(pickVaried(pool, SHOWN).map((s) => s.expects)).toContain('ontology');
  });

  it('leads with a figure, then the graph, then the cross-source question', () => {
    // The order is the pitch: something true and fast, then the capability nothing else shows,
    // then the question neither subordinate could answer alone.
    const pool = [
      suggestion('voc', 'voc'),
      suggestion('mixed', 'mixed'),
      suggestion('ontology', 'ontology'),
      suggestion('model', 'model'),
    ];
    expect(pickVaried(pool, SHOWN).map((s) => s.expects)).toEqual(['model', 'ontology', 'mixed']);
  });

  it('still returns n chips when the pool is short of families', () => {
    // A persona missing a family must get a third chip, not a hole where one should be.
    const pool = [suggestion('a', 'model'), suggestion('b', 'model'), suggestion('c', 'model')];
    expect(pickVaried(pool, SHOWN)).toHaveLength(SHOWN);
  });

  it('never repeats a suggestion, and never invents one', () => {
    const pool = [suggestion('a', 'model'), suggestion('b', 'ontology')];
    const picked = pickVaried(pool, SHOWN);
    expect(picked).toHaveLength(2);
    expect(new Set(picked).size).toBe(picked.length);
    for (const s of picked) expect(pool).toContain(s);
  });

  it('returns nothing when everything has been asked', () => {
    expect(pickVaried([], SHOWN)).toEqual([]);
  });
});

describe('the openers each persona actually shows', () => {
  it.each(PERSONAS.map((p) => [p.key, p] as const))(
    '%s offers its graph question, if it has one',
    (_key, persona) => {
      const hasOntology = persona.suggestions.some((s) => s.expects === 'ontology');
      if (!hasOntology) return; // nothing to reach; the coverage test below owns that case
      const shown = pickVaried(persona.suggestions, SHOWN);
      expect(shown.map((s) => s.expects)).toContain('ontology');
    }
  );

  it.each(PERSONAS.map((p) => [p.key, p] as const))(
    '%s opens on three distinct kinds of question',
    (_key, persona) => {
      const shown = pickVaried(persona.suggestions, SHOWN);
      expect(shown).toHaveLength(SHOWN);
      expect(new Set(shown.map((s) => s.expects)).size).toBe(SHOWN);
    }
  );

  it('keeps every persona able to demonstrate the graph', () => {
    // Asserted over the registry rather than per persona: a persona added later without an
    // ontology question would silently pass the two tests above, and this one fails instead.
    for (const p of PERSONAS) {
      expect(
        p.suggestions.filter((s) => s.expects === 'ontology').length,
        `persona "${p.key}" has no graph question to offer`
      ).toBeGreaterThan(0);
    }
  });
});
