/**
 * A deliberately small Markdown renderer.
 *
 * It renders to React elements, never to an HTML string, so there is no `dangerouslySetInnerHTML`
 * anywhere in this app. That matters more than the feature count: the text being rendered is
 * produced by a language model reading a customer's data, and handing model output to an HTML
 * parser turns every answer into an injection surface. Anything this renderer does not
 * recognise degrades to plain text, which is the correct failure for untrusted content.
 *
 * The supported subset is what the supervisor actually emits: headings, bold, italics, inline
 * code, fenced code, bullet and numbered lists, and pipe tables — tables above all, because
 * that is how it returns cohorts and campaign comparisons.
 */
import type { ReactNode } from 'react';

/** `**bold**`, `*italic*`, `` `code` `` — one pass, no nesting. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith('**')) {
      out.push(<strong key={k++}>{t.slice(2, -2)}</strong>);
    } else if (t.startsWith('`')) {
      out.push(
        <code
          key={k++}
          className="rounded px-1 py-0.5 font-mono text-[0.85em]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
        >
          {t.slice(1, -1)}
        </code>
      );
    } else {
      out.push(<em key={k++}>{t.slice(1, -1)}</em>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isTableRow = (l: string) => l.trim().startsWith('|') && l.trim().endsWith('|');
const isTableRule = (l: string) => /^\s*\|[\s:|-]+\|\s*$/.test(l);
const cells = (l: string) =>
  l
    .trim()
    .slice(1, -1)
    .split('|')
    .map((c) => c.trim());

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    // Fenced code
    if (line.trim().startsWith('```')) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push(
        <pre
          key={key++}
          className="my-2 overflow-x-auto rounded-lg p-3 text-xs"
          style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
        >
          <code>{buf.join('\n')}</code>
        </pre>
      );
      continue;
    }

    // Table: a header row, a separator rule, then body rows.
    if (isTableRow(line) && i + 1 < lines.length && isTableRule(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const body: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        body.push(cells(lines[i]));
        i += 1;
      }
      blocks.push(
        <div key={key++} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {head.map((h, n) => (
                  <th
                    key={n}
                    className="border-b px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide"
                    style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                  >
                    {inline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((c, n) => (
                    <td
                      key={n}
                      className="border-b px-3 py-1.5 align-top"
                      style={{ borderColor: 'var(--border)' }}
                    >
                      {inline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // Headings.
    //
    // Rendered as a labelled divider rather than as bigger bold text, because in a supervised
    // answer the headings *are* the structure: they separate what a subordinate measured from
    // what the supervisor read into it. Styled generically off the heading level — nothing here
    // matches on the wording, so a prompt that renames its sections still renders correctly and
    // a plain unstructured answer simply has no dividers.
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      blocks.push(
        <p
          key={key++}
          className="mt-5 mb-2 border-t pt-3 text-[11px] font-bold uppercase tracking-[0.14em] first:mt-0 first:border-0 first:pt-0"
          style={{ borderColor: 'var(--border)', color: level <= 2 ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          {inline(h[2])}
        </p>
      );
      i += 1;
      continue;
    }

    // Lists
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''));
        i += 1;
      }
      const cls = 'my-2 space-y-1 pl-5 text-sm';
      blocks.push(
        ordered ? (
          <ol key={key++} className={`list-decimal ${cls}`}>
            {items.map((t, n) => (
              <li key={n}>{inline(t)}</li>
            ))}
          </ol>
        ) : (
          <ul key={key++} className={`list-disc ${cls}`}>
            {items.map((t, n) => (
              <li key={n}>{inline(t)}</li>
            ))}
          </ul>
        )
      );
      continue;
    }

    // Paragraph: consume until a blank line or the start of another block.
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,4}\s|\s*([-*+]|\d+\.)\s)/.test(lines[i]) &&
      !isTableRow(lines[i]) &&
      !lines[i].trim().startsWith('```')
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={key++} className="my-2 text-sm leading-relaxed">
        {inline(buf.join(' '))}
      </p>
    );
  }

  return <div className="markdown">{blocks}</div>;
}
