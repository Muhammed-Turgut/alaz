import type { ReactNode } from "react";

import styles from "./aether.module.css";

/** Brifing Markdown'ı için küçük, güvenli (innerHTML kullanmayan) çözümleyici. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`(.+?)`|_(.+?)_/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      const level = /^(EXTREME|ELEVATED|NORMAL|SUPPRESSED|UNRATED)$/.test(m[1]) ? m[1] : null;
      out.push(
        level ? (
          <span key={k++} className={`${styles.lvl} ${styles["lvl" + level]}`}>{m[1]}</span>
        ) : (
          <strong key={k++}>{m[1]}</strong>
        ),
      );
    } else if (m[2] !== undefined) out.push(<code key={k++}>{m[2]}</code>);
    else out.push(<em key={k++}>{m[3]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const nodes: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const head = cells(rows[0]);
      const body = rows.slice(2).map(cells);
      nodes.push(
        <table key={key++}>
          <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, j) => <tr key={j}>{r.map((c, n) => <td key={n}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>,
      );
      continue;
    }
    if (line.startsWith("# ")) nodes.push(<h1 key={key++}>{inline(line.slice(2))}</h1>);
    else if (line.startsWith("## ")) nodes.push(<h2 key={key++}>{inline(line.slice(3))}</h2>);
    else if (/^- /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^- /.test(lines[i])) items.push(lines[i++].slice(2));
      nodes.push(<ul key={key++}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ul>);
      continue;
    } else if (/^\d+\. /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) items.push(lines[i++].replace(/^\d+\. /, ""));
      nodes.push(<ol key={key++}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ol>);
      continue;
    } else if (line.trim() !== "") nodes.push(<p key={key++}>{inline(line.trim())}</p>);
    i++;
  }
  return <div className={styles.brief}>{nodes}</div>;
}
