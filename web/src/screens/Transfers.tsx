import { useEffect, useState } from "react";
import { api, PlayerRow } from "../api";
import { useFetch, useGame } from "../context";
import { Badge, col, Empty, Fetched, money, MoneyInput, Panel, PlayerName, PotCell, Stat, Table, Tabs } from "../components/ui";
import { TeamPicker } from "./Squad";

/** How the user could pay ``salary`` (mirrors contracts.payment_route). */
export function route(salary: number, f: { cap_room: number; mle_available: boolean; mle: number; min_salary: number; max_salary: number }) {
  if (salary < f.min_salary) return null;
  if (salary > f.max_salary) return null;
  if (f.cap_room >= salary) return "cap space";
  if (f.mle_available && salary <= f.mle) return "mid-level exception";
  if (salary === f.min_salary) return "minimum";
  return null;
}

function FreeAgents() {
  const { act, busy } = useGame();
  const f = useFetch<any>("/api/free_agents");
  const [sel, setSel] = useState<PlayerRow | null>(null);
  const [salary, setSalary] = useState(0);
  const [years, setYears] = useState(1);
  useEffect(() => {
    if (sel) { setSalary(sel.ask ?? 0); setYears(sel.ask_years ?? 1); }
  }, [sel]);
  return (
    <Fetched f={f} what="Loading free agents">
      {(d) => {
        const how = sel ? route(salary, d) : null;
        return (
          <div className="fa-grid">
            <Panel title="Free agents" actions={<span className="muted small">{d.players.length} unsigned players</span>}>
              <Table rows={d.players} rowKey={(r) => r.id} initialSort="ca" onRowClick={setSel}
                rowClass={(r) => (sel?.id === r.id ? "row-selected" : "")}
                empty="Nobody's available."
                columns={[col.name(), col.pos, col.age, col.ht, col.ca, col.pot,
                  col.rating("three", "3PT"), col.rating("def", "DEF"), col.rating("reb", "REB"),
                  { key: "ask", label: "Asks", num: true, render: (r) => <b>{money(r.ask, 2)}</b> },
                  { key: "ask_years", label: "Yrs", num: true }]} />
            </Panel>
            <div className="stack">
              <Panel title="Your situation">
                <div className="stat-strip stat-strip-tight">
                  <Stat label="Roster" value={`${d.roster_size}/${d.max_roster}`} />
                  <Stat label="Cap room" value={money(Math.max(0, d.cap_room))} />
                  <Stat label="MLE" value={d.mle_available ? money(d.mle) : "used"} />
                </div>
                <p className="muted small">Over the cap you can only sign with the mid-level exception (once a year) or a minimum deal ({money(d.min_salary, 2)}). In season unsigned players take less than in the summer.</p>
              </Panel>
              <Panel title="Make an offer">
                {d.offseason_free_agency ? (
                  <Empty>It's the offseason: bid on free agents in the free-agency rounds of the Offseason screen.</Empty>
                ) : !sel ? (
                  <Empty>Pick a player from the list.</Empty>
                ) : (
                  <div className="form">
                    <div><PlayerName p={sel} /> <span className="muted small">{sel.pos}, {sel.age}, CA {sel.ca}</span></div>
                    <div className="muted small">Potential <PotCell p={sel.potential} /> · asks <b>{money(sel.ask, 2)}</b> for {sel.ask_years}y</div>
                    <label>Salary per season <MoneyInput key={sel.id} value={salary} onChange={setSalary} /></label>
                    <label>Years
                      <select value={years} onChange={(e) => setYears(+e.target.value)}>
                        {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{y}</option>)}
                      </select>
                    </label>
                    <div className={how ? "good small" : "bad small"}>
                      {how ? `Paid via ${how}.` : "You can't pay that under the cap rules."}
                      {salary < (sel.ask ?? 0) ? <span className="bad"> Below his ask: he'll say no.</span> : null}
                    </div>
                    <div className="row gap">
                      <button className="btn btn-small" onClick={() => setSalary(d.min_salary)}>Minimum</button>
                      {d.mle_available && <button className="btn btn-small" onClick={() => setSalary(Math.min(d.mle, Math.max(sel.ask ?? 0, d.min_salary)))}>Use MLE</button>}
                      <button className="btn btn-primary btn-small" disabled={busy || d.roster_size >= d.max_roster}
                        onClick={async () => { if (await act(() => api.post("/api/sign", { player_id: sel.id, salary, years }), (r: any) => r.message)) setSel(null); }}>
                        Offer contract
                      </button>
                    </div>
                    {d.roster_size >= d.max_roster && <span className="bad small">Your roster is full: release someone first.</span>}
                  </div>
                )}
              </Panel>
            </div>
          </div>
        );
      }}
    </Fetched>
  );
}

function PickList({ rows, picked, toggle }: { rows: PlayerRow[]; picked: number[]; toggle: (id: number) => void }) {
  return (
    <Table dense rows={rows} rowKey={(r) => r.id} initialSort="ca" onRowClick={(r) => toggle(r.id)}
      rowClass={(r) => (picked.includes(r.id) ? "row-selected" : "")}
      columns={[
        { key: "pick", label: "", sort: (r) => (picked.includes(r.id) ? 1 : 0), render: (r) => <input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} onClick={(e) => e.stopPropagation()} aria-label={`Include ${r.name}`} /> },
        col.name(), col.pos, col.age, col.ca, col.pot, col.salary, col.thru]} />
  );
}

function TradeBuilder({ params }: { params: URLSearchParams }) {
  const { state, act, busy } = useGame();
  const [other, setOther] = useState<number | null>(params.get("team") ? +params.get("team")! : null);
  const [give, setGive] = useState<number[]>([]);
  const [get, setGet] = useState<number[]>(params.get("get") ? [+params.get("get")!] : []);
  const [check, setCheck] = useState<any>(null);
  const mine = useFetch<any>("/api/roster");
  const theirs = useFetch<any>(other != null ? `/api/roster?team_id=${other}` : null);
  useEffect(() => {
    if (other == null || (!give.length && !get.length)) { setCheck(null); return; }
    let live = true;
    const t = setTimeout(() => {
      api.post("/api/trade/check", { give, team_id: other, get }).then((r) => live && setCheck(r)).catch((e) => live && setCheck({ ok: false, message: e.message }));
    }, 150);
    return () => { live = false; clearTimeout(t); };
  }, [give, get, other, state?.date, mine.data, theirs.data]);
  const toggle = (list: number[], set: (l: number[]) => void) => (id: number) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  return (
    <div className="trade">
      <div className="trade-grid">
        <Panel title={`You give (${state?.team?.abbr})`}>
          <Fetched f={mine} what="Loading your roster">{(d) => <PickList rows={d.players} picked={give} toggle={toggle(give, setGive)} />}</Fetched>
        </Panel>
        <Panel title="You get" actions={<TeamPicker value={other} includeMine={false} onChange={(id) => { setOther(id); setGet([]); }} />}>
          {other == null ? <Empty>Choose a team to trade with.</Empty> :
            <Fetched f={theirs} what="Loading their roster">{(d) => <PickList rows={d.players} picked={get} toggle={toggle(get, setGet)} />}</Fetched>}
        </Panel>
      </div>
      <Panel title="The deal">
        {!check ? <Empty>Tick players on either side to build a trade. AI teams accept deals that help them by their own priorities: contenders want ability now, rebuilders want youth and cap relief.</Empty> : (
          <div className="deal">
            <div className="stat-strip stat-strip-tight">
              <Stat label="Salary out" value={money(check.salary_out)} />
              <Stat label="Salary in" value={money(check.salary_in)} sub={check.salary_in > check.max_take_back ? <span className="bad">over 125% + $0.1M</span> : `limit if over the cap ${money(check.max_take_back)}`} />
              <Stat label="Salaries" value={check.salary_match ? <span className="good">match</span> : <span className="bad">don't match</span>} />
              <Stat label="Your payroll after" value={money(check.your_payroll_after)} sub={check.your_payroll_after > check.cap ? "over the cap" : "under the cap"} />
              <Stat label="Rosters after" value={`${check.your_roster_after} / ${check.their_roster_after}`} sub="you / them" />
            </div>
            <div className={`verdict ${check.ok ? "verdict-ok" : "verdict-no"}`}>
              {check.ok ? <><Badge kind="good">Likely yes</Badge> They'd accept this.</> : <><Badge kind="bad">No</Badge> {check.message}</>}
            </div>
            <div className="row gap">
              <button className="btn btn-primary" disabled={!check.ok || busy}
                onClick={async () => { if (await act(() => api.post("/api/trade", { give, team_id: other, get }), (r: any) => r.message)) { setGive([]); setGet([]); } }}>
                Propose trade
              </button>
              <button className="btn btn-ghost" onClick={() => { setGive([]); setGet([]); }}>Clear</button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

export function Transfers({ params }: { params: URLSearchParams }) {
  const [tab, setTab] = useState<"fa" | "trade">(params.get("team") ? "trade" : "fa");
  useEffect(() => { if (params.get("team")) setTab("trade"); }, [params.get("team")]);
  return (
    <div>
      <div className="screen-head">
        <h1>Transfers</h1>
        <Tabs value={tab} onChange={setTab} tabs={[{ key: "fa", label: "Free agents" }, { key: "trade", label: "Trade builder" }]} />
      </div>
      {tab === "fa" ? <FreeAgents /> : <TradeBuilder key={params.toString()} params={params} />}
    </div>
  );
}

