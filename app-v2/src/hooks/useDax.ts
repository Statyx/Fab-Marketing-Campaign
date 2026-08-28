/**
 * Run a DAX query and expose it as React state.
 *
 * The error path is deliberately as prominent as the success path: this app's numbers all come
 * from a semantic model in another region, and a card that silently renders 0 when the query
 * failed is worse than one that says it failed — it looks like an answer.
 */
import { useCallback, useEffect, useState } from 'react';

import { executeDax, type DaxRow } from '@/services/powerbi';

export interface DaxState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useDax<T>(dax: string, map: (rows: DaxRow[]) => T): DaxState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    executeDax(dax)
      .then((rows) => {
        if (cancelled) return;
        setData(map(rows));
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e instanceof Error ? e.message : e));
        setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // `map` is a module-level function in practice; depending on it would re-run the query on
    // every render if a caller ever passed an inline lambda.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dax, nonce]);

  return { data, loading, error, reload };
}
