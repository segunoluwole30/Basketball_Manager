import { useEffect, useMemo, useState } from "react";
import { api, PlayerRow } from "../api";
import { useFetch, useGame } from "../context";
import { Badge, CA, col, Column, Empty, Fetched, money, MoneyInput, Panel, PlayerName, Stat, Table } from "../components/ui";
import { route } from "./Transfers";

function useAnswer() {
  const { act, busy } = useGame();
  return {
    busy,
    answer: (answer: unknown, msg?: string) => act(() => api.post("/api/offseason/answer", { answer }), msg),
    auto: (scope: "one" | "step" | "all") =>
      act(() => api.post("/api/offseason/auto", { scope }), scope === "all" ? "Your assistant handled the rest of the summer." : "Your assistant decided."),
  };
}

function Finances({ f }: { f: any }) {
  return (
    <div className="stat-strip stat-strip-tight">
      <Stat label="Payroll" value={money(f.payroll)} />
      <Stat label="Cap room" value={money(Math.max(0, f.cap_room))} sub={`cap ${money(f.cap)}`} />
      <Stat label="Tax line" value={money(f.tax_line)} />
      <Stat label="MLE" value={f.mle_available ? money(f.mle) : "used"} />
      <Stat label="Roster" value={`${f.roster_size}/${f.max_roster}`} sub={`min ${f.min_roster}`} />
    </div>
  );
}

const baseCols = (): Column<PlayerRow>[] => [col.name(), col.pos, col.age, col.ca, col.pot];

// ---------------------------------------------------------------------------
function Options({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const [choice, setChoice] = useState<Record<number, boolean>>({});
  useEffect(() => setChoice(Object.fromEntries(p.players.map((r: any) => [r.id, r.salary <= r.fair_salary * 1.25]))), [p]);
  return (
    <>
      <Table rows={p.players} rowKey={(r) => r.id} columns={[...baseCols(),
        { key: "salary", label: "Owed", num: true, render: (r) => money(r.salary, 2) },
        { key: "fair_salary", label: "Market value", num: true, render: (r) => money(r.fair_salary, 2) },
        { key: "choice", label: "Decision", render: (r) => (
          <div className="seg">
            <button className={`seg-btn ${choice[r.id] ? "seg-on" : ""}`} onClick={() => setChoice({ ...choice, [r.id]: true })}>Exercise</button>
            <button className={`seg-btn ${choice[r.id] === false ? "seg-on" : ""}`} onClick={() => setChoice({ ...choice, [r.id]: false })}>Decline</button>
          </div>) }]} />
      <div className="row gap">
        <button className="btn btn-primary" disabled={busy} onClick={() => answer(choice, "Options decided")}>Confirm options</button>
        <span className="muted small">Declining makes him a free agent now.</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
function Resign({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const [offers, setOffers] = useState<Record<number, { on: boolean; salary: number; years: number }>>({});
  useEffect(() => setOffers(Object.fromEntries(p.players.map((r: any) => [r.id, { on: false, salary: r.ask, years: r.ask_years }]))), [p]);
  const total = Object.values(offers).filter((o) => o.on).reduce((s, o) => s + o.salary, 0);
  const payrollAfter = p.finances.payroll + total;
  const set = (id: number, v: Partial<{ on: boolean; salary: number; years: number }>) => setOffers({ ...offers, [id]: { ...offers[id], ...v } });
  return (
    <>
      <Table rows={p.players} rowKey={(r) => r.id} initialSort="ca" columns={[...baseCols(),
        col.morale,
        { key: "ask", label: "Asks", num: true, render: (r) => <>{money(r.ask, 2)} <span className="muted small">× {r.ask_years}y</span></> },
        { key: "chance", label: "Stays at ask", title: "Chance he re-signs if you offer his ask; paying more helps", num: true, render: (r) => `${Math.round(r.chance * 100)}%` },
        { key: "offer", label: "Your offer", render: (r) => offers[r.id] && (
          <div className="row gap nowrap">
            <label className="toggle"><input type="checkbox" checked={offers[r.id].on} onChange={(e) => set(r.id, { on: e.target.checked })} aria-label={`Offer ${r.name}`} /> offer</label>
            {offers[r.id].on && (
              <>
                <MoneyInput key={r.id} value={offers[r.id].salary} onChange={(v) => set(r.id, { salary: v })} />
                <select value={offers[r.id].years} onChange={(e) => set(r.id, { years: +e.target.value })} aria-label="Years">
                  {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{y}y</option>)}
                </select>
                {offers[r.id].salary < (r.ask ?? 0) * 0.9 && <span className="bad small">too low</span>}
              </>
            )}
          </div>) }]} />
      <p className="small">Next season's payroll with these deals: <b>{money(payrollAfter)}</b>
        {payrollAfter > p.finances.tax_line ? <span className="bad"> (over the luxury-tax line)</span> : payrollAfter > p.finances.cap ? " (over the cap: you can still re-sign your own players)" : ""}</p>
      <div className="row gap">
        <button className="btn btn-primary" disabled={busy}
          onClick={() => answer(Object.fromEntries(Object.entries(offers).filter(([, o]) => o.on).map(([id, o]) => [id, [o.salary, o.years]])))}>
          Make offers ({Object.values(offers).filter((o) => o.on).length})
        </button>
        <span className="muted small">Everyone without an offer walks. Offers below 90% of the ask are refused.</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
function Draft({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const [sel, setSel] = useState<number | null>(null);
  useEffect(() => setSel(null), [p.pick]);
  const chosen = p.players.find((r: any) => r.id === sel);
  return (
    <div className="draft-grid">
      <div>
        <Table rows={p.players} rowKey={(r) => r.id} initialSort="rank" initialDesc={false} onRowClick={(r) => setSel(r.id)}
          rowClass={(r) => (r.id === sel ? "row-selected" : "")}
          columns={[{ key: "rank", label: "Board", title: "Your scouts' board", num: true, desc: false },
            ...baseCols(), col.ht,
            col.rating("three", "3PT"), col.rating("fin", "FIN"), col.rating("def", "DEF"), col.rating("ath", "ATH"),
            { key: "notes", label: "Scouting notes", sort: (r) => r.notes.join(), render: (r) => <span className="small">{r.notes.join(", ") || "–"}</span> }]} />
      </div>
      <div className="stack">
        <Panel title={`Pick #${p.pick} (round ${p.round})`}>
          {chosen ? (
            <div className="form">
              <div><PlayerName p={chosen} /> <span className="muted small">{chosen.pos}, {chosen.age}, {chosen.height}</span></div>
              <div className="small">CA <CA v={chosen.ca} /> · potential {chosen.potential.est} ({chosen.potential.lo}–{chosen.potential.hi})</div>
              <button className="btn btn-primary" disabled={busy} onClick={() => answer(chosen.id, `You drafted ${chosen.name}`)}>Draft {chosen.name}</button>
            </div>
          ) : <Empty>Pick a prospect from the board (click the name for the full scouting report).</Empty>}
          <p className="muted small">First-rounders get a rookie-scale deal with a team option; second-rounders a minimum deal.</p>
        </Panel>
        <Panel title="Picks so far">
          {p.picks.length ? <ol className="picks small">{[...p.picks].reverse().map((l: string, i: number) => <li key={i}>{l}</li>)}</ol> : <Empty>You're first up.</Empty>}
        </Panel>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function FreeAgency({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const [bids, setBids] = useState<Record<number, { salary: number; years: number }>>({});
  useEffect(() => setBids({}), [p.round]);
  const f = p.finances;
  const byId = useMemo(() => Object.fromEntries(p.players.map((r: any) => [r.id, r])), [p.players]);
  const total = Object.values(bids).reduce((s, b) => s + b.salary, 0);
  const add = (r: any) => setBids({ ...bids, [r.id]: { salary: Math.min(r.ask, f.max_salary), years: r.ask_years } });
  return (
    <div className="fa-grid">
      <Table rows={p.players} rowKey={(r) => r.id} initialSort="ca" onRowClick={(r) => !bids[r.id] && add(r)}
        rowClass={(r) => (bids[r.id] ? "row-selected" : "")}
        columns={[...baseCols(), col.ht, col.rating("three", "3PT"), col.rating("def", "DEF"),
          { key: "ask", label: "Asks", num: true, render: (r) => <b>{money(r.ask, 2)}</b> },
          { key: "ask_years", label: "Yrs", num: true },
          { key: "bid", label: "", render: (r) => bids[r.id] ? <Badge kind="info">bid</Badge> : <button className="btn btn-small" onClick={(e) => { e.stopPropagation(); add(r); }}>Bid</button> }]} />
      <div className="stack">
        <Panel title="Your bids this round">
          {!Object.keys(bids).length ? <Empty>No bids: you'll pass this round. Click players to bid.</Empty> : (
            <ul className="bids">
              {Object.entries(bids).map(([id, b]) => {
                const r = byId[+id];
                const how = route(b.salary, f);
                return (
                  <li key={id} className="bid">
                    <div className="row gap"><PlayerName p={r} /><span className="muted small">asks {money(r.ask, 2)}</span>
                      <button className="icon-btn" aria-label="Remove bid" onClick={() => { const n = { ...bids }; delete n[+id]; setBids(n); }}>×</button></div>
                    <div className="row gap">
                      <MoneyInput key={id} value={b.salary} onChange={(v) => setBids({ ...bids, [id]: { ...b, salary: v } })} />
                      <select value={b.years} onChange={(e) => setBids({ ...bids, [id]: { ...b, years: +e.target.value } })} aria-label="Years">
                        {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{y}y</option>)}
                      </select>
                      <span className={how ? "good small" : "bad small"}>{how ?? "can't pay that"}</span>
                    </div>
                    {b.salary < r.ask * 0.85 && <div className="bad small">Under 85% of his ask: he won't take it.</div>}
                  </li>
                );
              })}
            </ul>
          )}
          {total > Math.max(0, f.cap_room) && Object.keys(bids).length > 1 && (
            <p className="warn small">Your bids add up to {money(total)}, more than your cap room. Each is checked as it's accepted: later ones may fall through.</p>
          )}
          <div className="row gap">
            <button className="btn btn-primary" disabled={busy || Object.values(bids).some((b) => !route(b.salary, f))}
              onClick={() => answer(Object.entries(bids).map(([id, b]) => [+id, b.salary, b.years]))}>
              {Object.keys(bids).length ? `Submit ${Object.keys(bids).length} bid${Object.keys(bids).length > 1 ? "s" : ""}` : "Pass this round"}
            </button>
          </div>
          <p className="muted small">Players take the best mix of money and team quality, so weaker teams have to outbid stronger ones. Unsigned players lower their asks each round.</p>
        </Panel>
        <Panel title="Signings so far">
          {p.log.length ? <ul className="picks small">{[...p.log].reverse().slice(0, 40).map((l: string, i: number) => <li key={i} className={l.toLowerCase().includes("your") ? "me" : ""}>{l}</li>)}</ul> : <Empty>Nobody has signed yet.</Empty>}
        </Panel>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function TradeOffer({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const sum = (rows: PlayerRow[]) => rows.reduce((s, r) => s + (r.salary ?? 0), 0);
  const cols = [...baseCols(), col.salary, col.thru, col.stat("ppg", "PPG")];
  return (
    <>
      <div className="trade-grid">
        <Panel title={`You give (${money(sum(p.give))})`}><Table dense rows={p.give} rowKey={(r) => r.id} columns={cols} /></Panel>
        <Panel title={`You get from ${p.team.abbr} (${money(sum(p.get))})`}><Table dense rows={p.get} rowKey={(r) => r.id} columns={cols} /></Panel>
      </div>
      <div className="row gap">
        <button className="btn btn-primary" disabled={busy} onClick={() => answer(true, "Trade accepted")}>Accept</button>
        <button className="btn" disabled={busy} onClick={() => answer(false, "Offer declined")}>Decline</button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
function Cuts({ p }: { p: any }) {
  const { answer, busy } = useAnswer();
  const [sel, setSel] = useState<number[]>([]);
  const toggle = (id: number) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  return (
    <>
      <Table rows={p.players} rowKey={(r) => r.id} initialSort="ca" initialDesc={false} onRowClick={(r) => toggle(r.id)}
        rowClass={(r) => (sel.includes(r.id) ? "row-selected" : "")}
        columns={[{ key: "pick", label: "", render: (r) => <input type="checkbox" checked={sel.includes(r.id)} onChange={() => toggle(r.id)} onClick={(e) => e.stopPropagation()} aria-label={`Waive ${r.name}`} /> },
          ...baseCols(), col.salary, col.thru]} />
      <div className="row gap">
        <button className="btn btn-danger" disabled={busy || sel.length !== p.count} onClick={() => answer(sel, "Players waived")}>
          Waive {sel.length}/{p.count}
        </button>
        <span className="muted small">Waived players' salaries come off your books.</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
function ReportView({ r, title }: { r: any; title: string }) {
  const [mine, setMine] = useState(true);
  const abbr = r.user_abbr;
  const about = new RegExp(`\\b${abbr}\\b|\\byou(r)?\\b`, "i");
  const filt = (lines: string[]) => (mine ? lines.filter((l) => about.test(l)) : lines);
  const sections: [string, string[]][] = [
    ["Team options", r.options], ["Re-signing", r.resigned], ["Draft", r.draft], ["Free agency", r.free_agency],
    ["Trades", r.trades], ["Waivers", r.waived],
  ];
  return (
    <Panel title={title} actions={<label className="toggle"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> only my team</label>}>
      {r.development.length > 0 && (
        <div className="dev">
          <h4>Summer development</h4>
          <div className="chips">{r.development.slice(0, 10).map((m: any) => (
            <span key={m.player_id} className={`chip ${m.change > 0 ? "chip-good" : m.change < 0 ? "chip-bad" : ""}`}><PlayerName p={{ id: m.player_id, name: m.name }} /> {m.change > 0 ? "+" : ""}{m.change}</span>
          ))}</div>
        </div>
      )}
      {sections.map(([label, lines]) => lines && filt(lines).length > 0 && (
        <details key={label} open={mine}>
          <summary>{label} <span className="muted small">({filt(lines).length})</span></summary>
          <ul className="small">{filt(lines).map((l, i) => <li key={i}>{l}</li>)}</ul>
        </details>
      ))}
      {r.retired.length > 0 && (
        <details>
          <summary>Retirements <span className="muted small">({r.retired.length})</span></summary>
          <ul className="small">{r.retired.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
        </details>
      )}
      {r.notes.map((n: string, i: number) => <p key={i} className="warn small">{n}</p>)}
    </Panel>
  );
}

const DECISIONS: Record<string, (p: { p: any }) => JSX.Element> = {
  options: Options, resign: Resign, draft: Draft, free_agency: FreeAgency, trade_offer: TradeOffer, cuts: Cuts,
};

export function Offseason({ go }: { go: (r: string) => void }) {
  const { act, busy, state } = useGame();
  const f = useFetch<any>("/api/offseason");
  const { auto } = useAnswer();
  return (
    <div>
      <div className="screen-head"><h1>Offseason</h1></div>
      <Fetched f={f} what="Loading the offseason">
        {(d) => {
          const stepIndex = d.active ? d.step_index : -1;
          return (
            <>
              <ol className="stepper">
                <li className={d.active ? "step-done" : ""}>Retirements &amp; development</li>
                {d.steps.map((s: any, i: number) => (
                  <li key={s.key} className={i < stepIndex ? "step-done" : i === stepIndex ? "step-on" : ""}>{s.title}</li>
                ))}
                <li>New season</li>
              </ol>
              {!d.active && d.can_start && (
                <Panel title={`Summer ${d.season}`}>
                  <p>The season is over. Players retire and develop over the summer, then you'll handle team options, re-signing, the draft, free agency, trade offers and roster cuts, one decision at a time.</p>
                  <p className="muted small">You can save at any point and pick up where you left off.</p>
                  <button className="btn btn-primary" disabled={busy} onClick={() => act(() => api.post("/api/offseason/start"), "The offseason has begun")}>Begin the offseason</button>
                </Panel>
              )}
              {!d.active && !d.can_start && (
                <>
                  {state?.stage === "regular_season" && d.last_report && state.day === 0 ? (
                    <div className="callout">The summer is over: welcome to the {state.season_label} season. <button className="btn btn-primary btn-small" onClick={() => go("home")}>To the office</button></div>
                  ) : (
                    <Empty>The offseason starts after the playoffs.{" "}<button className="link" onClick={() => go("home")}>Back to the office</button></Empty>
                  )}
                  {d.last_report && <ReportView r={d.last_report} title={`Last summer (${d.last_report.season})`} />}
                </>
              )}
              {d.active && d.pending && (() => {
                const P = DECISIONS[d.pending.kind];
                return (
                  <Panel title={d.pending.title} className="decision" actions={
                    <div className="row gap wrap">
                      <button className="btn btn-small btn-ghost" disabled={busy} onClick={() => auto("one")} title="Your assistant answers this question as the AI would">Assistant decides</button>
                      <button className="btn btn-small btn-ghost" disabled={busy} onClick={() => auto("step")}>…for this whole step</button>
                      <button className="btn btn-small btn-ghost" disabled={busy} onClick={() => { if (window.confirm("Let your assistant handle every remaining decision this summer?")) auto("all"); }}>…for the rest of the summer</button>
                    </div>}>
                    <p className="prompt">{d.pending.prompt}</p>
                    <Finances f={d.pending.finances} />
                    {P ? <P p={d.pending} /> : <Empty>Unknown decision {d.pending.kind}.</Empty>}
                  </Panel>
                );
              })()}
              {d.active && d.report && <ReportView r={d.report} title="This summer so far" />}
            </>
          );
        }}
      </Fetched>
    </div>
  );
}
