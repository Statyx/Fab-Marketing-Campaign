/**
 * Loading / error / empty wrapper for anything backed by a DAX query.
 *
 * Failures are rendered in place, with the message the model returned. The alternative —
 * swallowing the error and drawing zeros — produces a screen that is confidently wrong, which
 * in a demo is far more damaging than a visible gap.
 */
import type { ReactNode } from 'react';

interface Props {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  onRetry?: () => void;
  children: ReactNode;
}

export function QueryState({ loading, error, empty, onRetry, children }: Props) {
  if (loading) {
    return (
      <div
        className="flex items-center gap-3 p-6 text-sm"
        style={{ color: 'var(--text-secondary)' }}
      >
        <span
          className="h-4 w-4 animate-spin rounded-full border-2 border-t-transparent"
          style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }}
        />
        Chargement des données…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-sm font-medium text-red-800">La requête a échoué</p>
        <pre className="mt-2 whitespace-pre-wrap break-all text-xs text-red-700">{error}</pre>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
          >
            Réessayer
          </button>
        )}
      </div>
    );
  }

  if (empty) {
    return (
      <div className="p-6 text-sm" style={{ color: 'var(--text-secondary)' }}>
        Aucune donnée retournée.
      </div>
    );
  }

  return <>{children}</>;
}
