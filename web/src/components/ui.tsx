import { useMemo, useState } from "react";
import { PlayerRow, Potential } from "../api";
import { useGame } from "../context";

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
export const money = (n: number | null | undefined, digits = 1) =>
  n == null ? "–" : `${n < 0 ? "-" : ""}$${(Math.abs(n) / 1e6).toFixed(digits)}M`;

export const pct = (x: number) => (x >= 1 ? x.toFixed(3) : x.toFixed(3).replace(/^0/, ""));

export const shortDate = (iso: string) =>
  new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const longDate = (iso: string) =>
  new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "long", day: "numeric", year: "numeric" });

export function potText(p: Potential | null | undefined): string {
  if (!p) return "–";
  if (p.peaked) return "peaked";
  return `${p.est} (${p.lo}–${p.hi})`;
}

export function PotCell({ p }: { p: Potential }) {
  if (p.peaked) return <span className="muted">peaked</span>;
  return (
    <span title={`Scouts' estimate ${p.est}, range ${p.lo}–${p.hi}`}>
      <b>{p.est}</b> <span className="muted small">{p.lo}–{p.hi}</span>
    </span>
  );
}

/** A 1-20 rating, coloured like FM. */
export function Rating({ v }: { v: number }) {
  const cls = v >= 16 ? "r-elite" : v >= 13 ? "r-good" : v >= 9 ? "r-avg" : v >= 6 ? "r-poor" : "r-bad";
  return <span className={`rating ${cls}`}>{v}</span>;
}

export function CA({ v }: { v: number }) {
  const cls = v >= 140 ? "r-elite" : v >= 115 ? "r-good" : v >= 95 ? "r-avg" : v >= 80 ? "r-poor" : "r-bad";
  return <span className={`ca ${cls}`}>{v}</span>;
}

export function PlayerName({ p, extra }: { p: { id: number; name: string; injury?: PlayerRow["injury"] }; extra?: React.ReactNode }) {
  const { openPlayer } = useGame();
  return (
    <span className="pname">
      <button className="link" onClick={(e) => { e.stopPropagation(); openPlayer(p.id); }}>
        {p.name}
      </button>
      {p.injury ? (
        <span className="badge badge-bad" title={`${p.injury.description}, out ${p.injury.games} games`}>
          INJ {p.injury.games}
        </span>
      ) : null}
      {extra}
    </span>
  );
}

export function Badge({ kind = "neutral", children, title }: { kind?: string; children: React.ReactNode; title?: string }) {
  return <span className={`badge badge-${kind}`} title={title}>{children}</span>;
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------
export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="state-box">
      <span className="spinner" aria-hidden /> {what}…
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className="state-box state-error" role="alert">
      <b>Something went wrong.</b> {error}
      {retry && <button className="btn btn-small" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="state-box state-empty">{children}</div>;
}

/** Shows loading/error states around a fetch, then renders children(data). */
export function Fetched<T>({ f, children, what }: {
  f: { data: T | null; error: string | null; loading: boolean; reload: () => void };
  children: (d: T) => React.ReactNode;
  what?: string;
}) {
  if (f.error && !f.data) return <ErrorBox error={f.error} retry={f.reload} />;
  if (!f.data) return <Loading what={what} />;
  return <>{children(f.data)}</>;
}

export function Panel({ title, actions, children, className = "" }: {
  title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <h2>{title}</h2>
          <div className="panel-actions">{actions}</div>
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { key: T; label: React.ReactNode }[]; value: T; onChange: (t: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={t.key === value}
          className={`tab ${t.key === value ? "tab-on" : ""}`} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sortable table
// ---------------------------------------------------------------------------
export interface Column<R> {
  key: string;
  label: React.ReactNode;
  title?: string;
  /** Value to sort by (defaults to row[key]). */
  sort?: (r: R) => number | string | null | undefined;
  render?: (r: R) => React.ReactNode;
  num?: boolean;
  className?: string;
  /** Sort descending first (default for numeric columns). */
  desc?: boolean;
}

export function Table<R extends { [k: string]: any }>({
  rows, columns, rowKey, initialSort, initialDesc, onRowClick, rowClass, empty = "Nothing to show.", dense,
}: {
  rows: R[];
  columns: Column<R>[];
  rowKey: (r: R) => string | number;
  initialSort?: string;
  initialDesc?: boolean;
  onRowClick?: (r: R) => void;
  rowClass?: (r: R) => string;
  empty?: React.ReactNode;
  dense?: boolean;
}) {
  const [sortKey, setSortKey] = useState<string | undefined>(initialSort);
  const [desc, setDesc] = useState<boolean>(initialDesc ?? true);
  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    const val = col.sort ?? ((r: R) => r[col.key]);
    return [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const c = typeof x === "string" && typeof y === "string" ? x.localeCompare(y) : (x as number) - (y as number);
      return desc ? -c : c;
    });
  }, [rows, columns, sortKey, desc]);

  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table className={`table ${dense ? "table-dense" : ""}`}>
        <thead>
          <tr>
            {columns.map((c) => {
              const on = c.key === sortKey;
              return (
                <th key={c.key} title={c.title} className={`${c.num ? "num" : ""} ${c.className ?? ""}`}
                  aria-sort={on ? (desc ? "descending" : "ascending") : "none"}>
                  <button className="th-btn" onClick={() => {
                    if (on) setDesc(!desc);
                    else { setSortKey(c.key); setDesc(c.desc ?? !!c.num); }
                  }}>
                    {c.label}
                    <span className="sort-mark">{on ? (desc ? "▾" : "▴") : ""}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)} className={`${onRowClick ? "clickable" : ""} ${rowClass?.(r) ?? ""}`}
              onClick={onRowClick ? () => onRowClick(r) : undefined}>
              {columns.map((c) => (
                <td key={c.key} className={`${c.num ? "num" : ""} ${c.className ?? ""}`}>
                  {c.render ? c.render(r) : r[c.key] ?? "–"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Columns most player tables share.
export const col = {
  name: (extra?: (r: PlayerRow) => React.ReactNode): Column<PlayerRow> => ({
    key: "name", label: "Name", sort: (r) => r.name, desc: false,
    render: (r) => <PlayerName p={r} extra={extra?.(r)} />, className: "col-name",
  }),
  pos: { key: "pos", label: "Pos", sort: (r: PlayerRow) => "PG SG SF PF C".indexOf(r.pos), desc: false } as Column<PlayerRow>,
  age: { key: "age", label: "Age", num: true, desc: false } as Column<PlayerRow>,
  ht: { key: "height", label: "Ht", sort: (r: PlayerRow) => r.height_cm, num: true } as Column<PlayerRow>,
  ca: { key: "ca", label: "CA", title: "Current ability (1-200)", num: true, render: (r: PlayerRow) => <CA v={r.ca} /> } as Column<PlayerRow>,
  pot: {
    key: "potential", label: "Pot", title: "Potential: your scouts' estimate (range)", num: true,
    sort: (r: PlayerRow) => (r.potential.peaked ? r.ca : r.potential.est), render: (r: PlayerRow) => <PotCell p={r.potential} />,
  } as Column<PlayerRow>,
  rating: (key: string, label: string, title?: string): Column<PlayerRow> => ({
    key, label, title, num: true, render: (r) => <Rating v={r[key]} />,
  }),
  salary: { key: "salary", label: "Salary", num: true, render: (r: PlayerRow) => money(r.salary) } as Column<PlayerRow>,
  thru: {
    key: "thru", label: "Thru", num: true, desc: false,
    render: (r: PlayerRow) => (r.thru ? <>{r.thru}{r.option ? <span className="muted small"> {r.option[0].toUpperCase()}O</span> : null}</> : "–"),
  } as Column<PlayerRow>,
  stat: (key: string, label: string): Column<PlayerRow> => ({ key, label, num: true, render: (r) => (r.gp ? (r[key] as number).toFixed(1) : "–") }),
  morale: {
    key: "morale", label: "Morale", num: true,
    render: (r: PlayerRow) => <span className={`morale m-${r.morale_word}`}>{r.morale_word}</span>,
  } as Column<PlayerRow>,
};

export function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: React.ReactNode; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="modal-back" onClick={onClose}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/** Salary input in $M; value in dollars. */
export function MoneyInput({ value, onChange, id }: { value: number; onChange: (v: number) => void; id?: string }) {
  const [text, setText] = useState((value / 1e6).toFixed(2));
  return (
    <span className="money-input">
      $<input id={id} type="number" step="0.05" min="0" value={text}
        onChange={(e) => { setText(e.target.value); const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(Math.round(v * 1e6)); }} />M
    </span>
  );
}
