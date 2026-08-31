/**
 * Replay of pre-recorded supervisor answers, for the opening clicks of a demo.
 *
 * A supervised answer costs 40-160 seconds because routing happens on the data side -- Fabric
 * owns the semantics, the measures and the DAX. That is the right trade for a real question and
 * the wrong one for the first click on stage, where a minute of spinner is a minute of dead air.
 *
 * So the questions the app itself suggests are answered from a recording.
 *
 * Two rules make that acceptable, and both are load-bearing:
 *
 *  1. **Nothing here is written by hand.** `src/capture_frozen_answers.py` invokes the live
 *     supervisor and stores what came back, with the subordinates that really fired and the time
 *     it really took. It refuses to record an answer where no subordinate fired. A hand-written
 *     answer would look exactly as sourced as a real one -- same prose, same provenance block,
 *     same routing badges -- while being a fabrication, and the audience could not tell.
 *
 *  2. **The replay says so.** The wait names it, and the answer carries the capture date and the
 *     duration the agent actually took. An undisclosed cache would break the same contract the
 *     muted third trace dot exists to protect: never present something as observed when it was
 *     not.
 *
 * A miss is fail-safe: the question goes to the live agent exactly as before. Missing a recording
 * costs latency, never correctness -- which is why a partial capture is still worth shipping.
 */
import raw from '@/data/frozen-answers.generated.json';

export interface FrozenAnswer {
  text: string;
  toolsFired: string[];
  /** Seconds the LIVE agent took when this was recorded -- never the replay delay. */
  seconds: number;
  capturedAt: string;
}

/** How long the replay pauses before revealing. Long enough to read the wait, short enough to
 *  feel instant next to the 40-160s it stands in for. */
export const REPLAY_MS = 5000;

const answers = ((raw as { answers?: Record<string, FrozenAnswer> })?.answers ?? {});

/**
 * Tolerant on typing, strict on meaning: case and whitespace are normalised so a presenter who
 * retypes a suggestion still gets the instant path, but nothing fuzzier. Matching a *different*
 * question would serve a real answer to the wrong question -- the one failure mode worse than
 * being slow.
 */
const norm = (q: string) => q.trim().replace(/\s+/g, ' ').toLowerCase();

const byNorm = new Map<string, FrozenAnswer>(
  Object.entries(answers).map(([q, a]) => [norm(q), a]),
);

export function frozenAnswer(question: string): FrozenAnswer | null {
  return byNorm.get(norm(question)) ?? null;
}

/** Questions held, in their original wording. Used by the drift test. */
export function frozenQuestions(): string[] {
  return Object.keys(answers);
}

/** "31 août" -- short enough for a chip, explicit enough to spot a stale figure. */
export function frozenDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' });
}
