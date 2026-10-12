/**
 * Minimal Markdown renderer for consolidation previews.
 * Builds React elements (never raw HTML), so document content cannot inject markup or scripts.
 */
import type { ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    parts.push(token.startsWith("**") ? <strong key={`${key}-${i++}`}>{token.slice(2, -2)}</strong> : <code key={`${key}-${i++}`}>{token.slice(1, -1)}</code>);
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (!line.trim()) {
      i++;
    } else if (line.startsWith("# ")) {
      blocks.push(<h1 key={key}>{inline(line.slice(2), key)}</h1>);
      i++;
    } else if (line.startsWith("## ")) {
      blocks.push(<h2 key={key}>{inline(line.slice(3), key)}</h2>);
      i++;
    } else if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={key}>{inline(quote.join(" "), key)}</blockquote>);
    } else if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i++].split(/(?<!\\)\|/).slice(1, -1).map((c) => c.trim().replace(/\\\|/g, "|"));
        if (!cells.every((c) => /^-+$/.test(c))) rows.push(cells);
      }
      const [head, ...body] = rows;
      blocks.push(
        <table key={key}>
          <thead>
            <tr>{head?.map((c, j) => <th key={j}>{inline(c, `${key}h${j}`)}</th>)}</tr>
          </thead>
          <tbody>
            {body.map((r, ri) => (
              <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `${key}${ri}${j}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      );
    } else if (/^\s*- /.test(line)) {
      const items: { text: string; nested: boolean }[] = [];
      while (i < lines.length && /^\s*- /.test(lines[i])) {
        items.push({ text: lines[i].replace(/^\s*- /, ""), nested: /^\s{2,}- /.test(lines[i]) });
        i++;
      }
      blocks.push(
        <ul key={key}>
          {items.map((it, j) => (
            <li key={j} style={it.nested ? { marginLeft: "1.25rem", listStyle: "circle" } : undefined}>
              {inline(it.text, `${key}${j}`)}
            </li>
          ))}
        </ul>,
      );
    } else {
      blocks.push(<p key={key}>{inline(line, key)}</p>);
      i++;
    }
  }
  return <div className="md-preview">{blocks}</div>;
}
