/**
 * The retry contract.
 *
 * Written because the failure it guards against was *measured*, not imagined: five identical
 * runs of the app's own "why does Black Friday Blast generate unsubscribes" suggestion returned
 * four answers and one `tool_user_error`, the failure landing at 110s while the successes ran
 * 98-157s. So a 400 here is not a malformed request, and the app must not treat it as one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/services/msal', () => ({
  FOUNDRY_SCOPES: ['scope'],
  getToken: vi.fn(async () => 'token'),
}));

/** The exact payload the app printed on stage, trimmed to what matters. */
const TOOL_USER_ERROR = JSON.stringify({
  error: {
    message: 'Agent task resp_007931f24f8a failed. Troubleshooting guide: https://learn.microsoft.com/...',
    type: 'invalid_request_error',
    param: null,
    code: 'tool_user_error',
    request_id: 'ffd717e1630552110ae07da04ece827a',
  },
});

const ANSWER = JSON.stringify({
  output_text: 'Deux cent clients.',
  output: [{ type: 'a2a_preview_call', name: 'FrontDoorA2A' }],
});

function reply(status: number, body: string): Response {
  return { ok: status >= 200 && status < 300, status, text: async () => body } as Response;
}

async function load() {
  vi.stubEnv('VITE_FOUNDRY_ENDPOINT', 'https://example.invalid/api/projects/p');
  return import('@/services/foundry');
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('isTransient', () => {
  it('treats a 400 carrying tool_user_error as worth retrying', async () => {
    const { isTransient } = await load();
    expect(isTransient(400, TOOL_USER_ERROR)).toBe(true);
  });

  it('does not retry a 400 that is genuinely a malformed request', async () => {
    const { isTransient } = await load();
    // Same status, same `type`, different `code`. Keying on the type would have retried this.
    const body = JSON.stringify({ error: { type: 'invalid_request_error', code: 'invalid_value' } });
    expect(isTransient(400, body)).toBe(false);
  });

  it.each([408, 409, 429, 500, 502, 503, 504])('retries %i whatever the body', async (status) => {
    const { isTransient } = await load();
    expect(isTransient(status, '')).toBe(true);
  });

  it.each([401, 403, 404])('never retries %i', async (status) => {
    const { isTransient } = await load();
    expect(isTransient(status, TOOL_USER_ERROR)).toBe(false);
  });
});

describe('askSupervisor', () => {
  it('answers on the second attempt when the first hop dies in flight', async () => {
    const { askSupervisor } = await load();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(reply(400, TOOL_USER_ERROR))
      .mockResolvedValueOnce(reply(200, ANSWER));
    vi.stubGlobal('fetch', fetchMock);

    const res = await askSupervisor('pourquoi ?');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(res.text).toBe('Deux cent clients.');
    expect(res.toolsFired).toEqual(['FrontDoorA2A']);
  });

  it('announces every attempt, so the retry is visible rather than a silent four-minute wait', async () => {
    const { askSupervisor } = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(reply(400, TOOL_USER_ERROR)).mockResolvedValueOnce(reply(200, ANSWER))
    );
    const seen: number[] = [];

    await askSupervisor('pourquoi ?', { onAttempt: (n) => seen.push(n) });

    expect(seen).toEqual([1, 2]);
  });

  it('stops at two attempts — a third would outlast the demo', async () => {
    const { askSupervisor, SupervisorError } = await load();
    const fetchMock = vi.fn().mockResolvedValue(reply(400, TOOL_USER_ERROR));
    vi.stubGlobal('fetch', fetchMock);

    await expect(askSupervisor('pourquoi ?')).rejects.toBeInstanceOf(SupervisorError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retry a refused token — retrying an auth failure only wastes two minutes', async () => {
    const { askSupervisor } = await load();
    const fetchMock = vi.fn().mockResolvedValue(reply(403, 'forbidden'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(askSupervisor('pourquoi ?')).rejects.toThrow(/session/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries a network-level throw, which carries no status at all', async () => {
    const { askSupervisor } = await load();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(reply(200, ANSWER));
    vi.stubGlobal('fetch', fetchMock);

    await expect(askSupervisor('pourquoi ?')).resolves.toMatchObject({ text: 'Deux cent clients.' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('never puts the payload in the sentence the user reads', async () => {
    const { askSupervisor, SupervisorError } = await load();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(400, TOOL_USER_ERROR)));

    const err = await askSupervisor('pourquoi ?').then(
      () => null,
      (e: unknown) => e as InstanceType<typeof SupervisorError>
    );
    if (!err) throw new Error('the call was expected to fail');

    // The two registers: a sentence for the room, the payload for the fold.
    for (const leak of ['resp_007931f24f8a', 'tool_user_error', 'learn.microsoft.com', 'ffd717e1']) {
      expect(err.message).not.toContain(leak);
    }
    expect(err.detail).toContain('tool_user_error');
    expect(err.attempts).toBe(2);
    expect(err.transient).toBe(true);
  });

  it('says it tried more than once, so a repeated failure does not read like a one-off', async () => {
    const { askSupervisor } = await load();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(400, TOOL_USER_ERROR)));

    await expect(askSupervisor('pourquoi ?')).rejects.toThrow(/2 fois de suite/);
  });
});
