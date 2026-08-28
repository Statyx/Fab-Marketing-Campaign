/**
 * Light/dark theme, driven by `data-theme` on <html> — the same mechanism the V1 portal used.
 *
 * The initial value is applied by an inline script in index.html, before React mounts, so the
 * page never paints light and then flips to dark. This hook only keeps React in sync with an
 * attribute that already has the right value.
 */
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'c360-theme';

export function currentTheme(): Theme {
  const attr = document.documentElement.getAttribute('data-theme');
  return attr === 'dark' ? 'dark' : 'light';
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage is partitioned — and sometimes refused outright — inside the Fabric portal
      // iframe. Losing the preference across reloads is acceptable; throwing here is not.
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, toggle };
}
