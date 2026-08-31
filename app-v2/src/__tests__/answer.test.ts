/**
 * The splitter's contract, pinned.
 *
 * This function decides what the reader sees. Every failure mode here is silent by nature —
 * a bad match does not throw, it hides an answer behind a collapsed button — so the tests are
 * written around the one invariant that matters: nothing the parser fails to recognise may
 * disappear from `body`.
 */
import { describe, expect, it } from 'vitest';

import { splitAnswer } from '@/services/answer';

const ANSWER = [
  'Environ un client acheteur sur treize est dans la cohorte actionnable.',
  '',
  'Les verbatims pointent une pression de contact jugée excessive.',
  '',
  '### SOURCE',
  'Modèle : SM_Marketing_Analytics',
  'Table : crm_customer_profile',
  'Colonne : risk_band',
].join('\n');

describe('splitAnswer', () => {
  it('files the block behind the marker and leaves the prose intact', () => {
    const { body, source } = splitAnswer(ANSWER);
    expect(body).toContain('un client acheteur sur treize');
    expect(body).not.toContain('crm_customer_profile');
    expect(body).not.toContain('SOURCE');
    expect(source).toContain('SM_Marketing_Analytics');
    expect(source).toContain('risk_band');
  });

  it('keeps the whole reply visible when no marker was emitted', () => {
    const plain = 'Une réponse sans bloc technique du tout.';
    expect(splitAnswer(plain)).toEqual({ body: plain, source: null });
  });

  it.each([
    'SOURCE',
    '## Source',
    // The form that broke the first implementation: French typography puts the colon inside
    // the bold markers, and a pattern expecting it outside misses silently.
    '**Source :**',
    '**Source:**',
    '### SOURCE :',
    '#### source',
    '   ### SOURCE   ',
    'Sources',
  ])('tolerates the marker written as %j', (marker) => {
    const { body, source } = splitAnswer(`Réponse en clair.\n\n${marker}\nTable : t`);
    expect(body).toBe('Réponse en clair.');
    expect(source).toBe('Table : t');
  });

  it('splits on the last marker, so the word used in prose cannot swallow the answer', () => {
    // "source" is an ordinary French word. Splitting on the FIRST match would file the entire
    // answer as provenance and leave the reader a collapsed button where the answer was.
    const text = 'Selon la source\ninterrogée, la cohorte est stable.\n\n### SOURCE\nTable : t';
    const { body, source } = splitAnswer(text);
    expect(body).toContain('la cohorte est stable');
    expect(source).toBe('Table : t');
  });

  it('does not treat a marker inside a sentence as a block opener', () => {
    const text = 'La source de cette mesure est le modèle sémantique, et rien d’autre.';
    expect(splitAnswer(text).source).toBeNull();
  });

  it('shows the text as written when the marker has nothing under it', () => {
    const text = 'Réponse complète.\n\n### SOURCE\n';
    const { body, source } = splitAnswer(text);
    expect(source).toBeNull();
    expect(body).toContain('Réponse complète.');
  });

  it('never hides the answer when the marker opens the reply', () => {
    // A model that emits the block first would otherwise leave `body` empty and the screen
    // blank — the exact outcome this parser exists to make impossible.
    const text = '### SOURCE\nTable : t';
    const { body, source } = splitAnswer(text);
    expect(source).toBeNull();
    expect(body).toContain('Table : t');
  });

  it('is not stateful across calls', () => {
    // The marker is a module-level /g regex: `lastIndex` survives between calls and would make
    // every second split silently miss. Reset is asserted rather than assumed.
    const first = splitAnswer(ANSWER);
    const second = splitAnswer(ANSWER);
    expect(second).toEqual(first);
    expect(second.source).not.toBeNull();
  });
});
