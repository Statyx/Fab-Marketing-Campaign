/**
 * The recorded-answers contract.
 *
 * Two failure modes are being guarded here, and only one of them is about speed.
 *
 * The cheap one: a recording whose question no longer exists in the persona registry. Somebody
 * rewords a suggestion, the recording stops matching, and the demo quietly goes back to waiting a
 * minute -- with nothing on screen to say why.
 *
 * The expensive one: a recording that is not actually sourced, or that answers a question the
 * user did not ask. A stored answer is indistinguishable from a live one in the prose; the only
 * things standing between "instant demo" and "fabricated demo" are the capture script's refusal
 * to record an unsourced answer, and the strictness of the lookup key. Both are asserted below.
 */
import { describe, expect, it } from 'vitest';

import { PERSONAS } from '@/data/personas';
import questions from '@/data/frozen-questions.generated.json';
import raw from '@/data/frozen-answers.generated.json';
import { frozenAnswer, frozenQuestions, frozenDate, REPLAY_MS } from '@/services/frozen';

const answers = (raw as { answers: Record<string, { text: string; toolsFired: string[]; seconds: number; capturedAt: string }> }).answers;
const entries = (questions as { entries: { persona: string; q: string; depth: number }[] }).entries;

const registry = new Set(PERSONAS.flatMap((p) => p.suggestions.map((s) => s.q)));

describe('frozen answers', () => {
  it('only ever holds questions the app still offers', () => {
    // A recording that drifts away from the registry is dead weight that also looks like a
    // working cache. Reword a suggestion and this fails until the capture is re-run.
    for (const q of frozenQuestions()) {
      expect(registry.has(q), `recorded question is no longer in any persona: "${q}"`).toBe(true);
    }
  });

  it('never holds an answer no subordinate produced', () => {
    // The whole justification for replaying stored text is that it came out of the live agent
    // with real routing. An entry with no tool is an ungrounded answer wearing the same badges.
    for (const [q, a] of Object.entries(answers)) {
      expect(a.text.trim().length, `empty answer recorded for "${q}"`).toBeGreaterThan(0);
      expect(a.toolsFired.length, `unsourced answer recorded for "${q}"`).toBeGreaterThan(0);
      expect(a.seconds, `implausible duration for "${q}"`).toBeGreaterThan(0);
      expect(Number.isNaN(Date.parse(a.capturedAt))).toBe(false);
    }
  });

  it('matches a question the presenter retyped with different spacing or case', () => {
    const [first] = frozenQuestions();
    if (!first) return; // nothing captured yet -- the app simply stays live
    expect(frozenAnswer(first)).not.toBeNull();
    expect(frozenAnswer(`  ${first.toUpperCase()}  `)).not.toBeNull();
    expect(frozenAnswer(first.replace(/ /g, '  '))).not.toBeNull();
  });

  it('refuses a question it does not hold, rather than guessing', () => {
    // Serving a real answer to a question nobody asked is the one outcome worse than being slow.
    expect(frozenAnswer('Combien de clients ont un chien ?')).toBeNull();
    expect(frozenAnswer('')).toBeNull();
    const [first] = frozenQuestions();
    if (first) expect(frozenAnswer(`${first} par segment`)).toBeNull();
  });

  it('pauses long enough for the replay to announce itself', () => {
    // Revealing instantly reads as a hardcoded string; the pause is where the app says the
    // answer is a recording.
    expect(REPLAY_MS).toBeGreaterThanOrEqual(3000);
    expect(REPLAY_MS).toBeLessThanOrEqual(8000);
  });

  it('formats a capture date a French reader can act on', () => {
    expect(frozenDate('2025-06-04T10:00:00Z')).toContain('juin');
    expect(frozenDate('not-a-date')).toBe('not-a-date');
  });
});

describe('the question list driving the capture', () => {
  it('is derived from the registry, not typed by hand', () => {
    expect(entries.length).toBeGreaterThan(0);
    for (const e of entries) {
      expect(registry.has(e.q), `extracted question is not in any persona: "${e.q}"`).toBe(true);
    }
  });

  it('covers the openers of every persona', () => {
    // The openers are the ones clicked on stage; a persona missing from the list would silently
    // never be recorded.
    const openers = new Set(entries.filter((e) => e.depth === 1).map((e) => e.persona));
    for (const p of PERSONAS) expect(openers.has(p.key), `no opener extracted for ${p.key}`).toBe(true);
  });

  it('asks for each question once', () => {
    const seen = entries.map((e) => `${e.persona}::${e.q}`);
    expect(new Set(seen).size).toBe(seen.length);
  });
});
