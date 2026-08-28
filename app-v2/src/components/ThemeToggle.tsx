import { useTheme } from '@/hooks/useTheme';

/** V1's header control: one button, rotates on hover, swaps the whole palette. */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggle } = useTheme();
  const next = theme === 'dark' ? 'clair' : 'sombre';

  return (
    <button
      type="button"
      onClick={toggle}
      title={`Passer en mode ${next}`}
      aria-label={`Passer en mode ${next}`}
      className={`flex h-9 w-9 items-center justify-center rounded-full text-base transition-transform duration-200 hover:rotate-[20deg] ${className}`}
      style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
    >
      {theme === 'dark' ? '☀️' : '🌙'}
    </button>
  );
}
