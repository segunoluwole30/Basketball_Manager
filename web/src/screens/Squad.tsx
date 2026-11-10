import { useState } from "react";
import { PlayerRow } from "../api";
import { useFetch, useGame } from "../context";
import { Badge, col, Column, Fetched, money, Panel, Stat, Table, Tabs } from "../components/ui";

type View = "overview" | "ratings" | "stats" | "contracts";

export function rosterColumns(view: View, showStarter = true): Column<PlayerRow>[] {
  const name = col.name((r) => (showStarter && r.starter ? <Badge kind="info" title="Starter">S</Badge> : null));
  const jersey: Column<PlayerRow> = { key: "jersey", label: "#", num: true, desc: false, render: (r) => r.jersey ?? "–" };
  switch (view) {
    case "ratings":
      return [jersey, name, col.pos, col.ca, col.pot,
        col.rating("three", "3PT", "Three-point shooting"), col.rating("mid", "MID", "Mid-range"),
        col.rating("fin", "FIN", "Finishing at the rim"), col.rating("pas", "PAS", "Passing"),
        col.rating("hnd", "HND", "Ball handling"), col.rating("def", "DEF", "Best of perimeter/interior defence"),
        col.rating("reb", "REB", "Defensive rebounding"), col.rating("ath", "ATH", "Athleticism: speed, vertical, strength"),
        col.rating("iq", "IQ", "Basketball IQ")];
    case "stats":
      return [jersey, name, col.pos, { key: "gp", label: "GP", num: true },
        { key: "minutes_target", label: "Target", title: "Minutes target", num: true },
        col.stat("mpg", "MPG"), col.stat("ppg", "PPG"), col.stat("rpg", "RPG"), col.stat("apg", "APG"), col.morale];
    case "contracts":
      return [name, col.pos, col.age, col.ca, col.salary, col.thru, col.morale];
    default:
      return [jersey, name, col.pos, col.age, col.ht, col.ca, col.pot,
        col.rating("three", "3PT"), col.rating("fin", "FIN"), col.rating("pas", "PAS"), col.rating("def", "DEF"),
        col.rating("reb", "REB"), col.salary, col.thru, col.stat("mpg", "MPG"), col.stat("ppg", "PPG"), col.morale];
  }
}

export function TeamPicker({ value, onChange, includeMine = true }: { value: number | null; onChange: (id: number) => void; includeMine?: boolean }) {
  const { state } = useGame();
  const teams = Object.values(state?.teams ?? {}).filter((t) => includeMine || t.id !== state?.team?.id)
    .sort((a, b) => a.name.localeCompare(b.name));
  return (
    <select value={value ?? ""} onChange={(e) => onChange(+e.target.value)} aria-label="Team">
      {value == null && <option value="">Choose a team…</option>}
      {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === state?.team?.id ? " (you)" : ""}</option>)}
    </select>
  );
}

export function Squad() {
  const { state } = useGame();
  const [teamId, setTeamId] = useState<number>(state?.team?.id ?? 0);
  const [view, setView] = useState<View>("overview");
  const f = useFetch<any>(`/api/roster?team_id=${teamId}`);
  const fin = useFetch<any>(teamId === state?.team?.id ? "/api/finances" : null);
  return (
    <div className="squad">
      <div className="screen-head">
        <h1>Squad</h1>
        <TeamPicker value={teamId} onChange={setTeamId} />
      </div>
      <Fetched f={f} what="Loading roster">
        {(d) => (
          <>
            <div className="stat-strip">
              <Stat label="Record" value={`${d.team.record[0]}-${d.team.record[1]}`} />
              <Stat label="Strength" value={d.strength} sub="top-8 average CA" />
              <Stat label="Payroll" value={money(d.payroll)} sub={`cap room ${money(Math.max(0, d.cap_room))}`} />
              <Stat label="Players" value={d.players.length} sub={`${d.players.filter((p: PlayerRow) => p.injury).length} injured`} />
            </div>
            <Panel title={d.team.name} actions={
              <Tabs value={view} onChange={setView} tabs={[
                { key: "overview", label: "Overview" }, { key: "ratings", label: "Ratings" },
                { key: "stats", label: "Stats" }, { key: "contracts", label: "Contracts" }]} />}>
              <Table rows={d.players} rowKey={(r) => r.id} columns={rosterColumns(view, d.is_mine)} initialSort="ca"
                rowClass={(r) => (r.injury ? "row-injured" : "")} />
              <p className="muted small">Click a name for the player card. Potential is your scouts' estimate with a range; past 29 it reads "peaked".</p>
            </Panel>
          </>
        )}
      </Fetched>
      {teamId === state?.team?.id && (
        <Fetched f={fin} what="Loading finances">
          {(d) => (
            <Panel title="Finances">
              <div className="stat-strip">
                <Stat label="Payroll" value={money(d.payroll)} />
                <Stat label="Salary cap" value={money(d.cap)} sub={`room ${money(Math.max(0, d.cap_room))}`} />
                <Stat label="Tax line" value={money(d.tax_line)} sub={d.payroll > d.tax_line ? "over the tax!" : `${money(d.tax_line - d.payroll)} below`} />
                <Stat label="Mid-level exception" value={d.mle_available ? "available" : "used"} sub={`up to ${money(d.mle)}`} />
                <Stat label="Minimum deal" value={money(d.min_salary, 2)} sub={`max ${money(d.max_salary)}`} />
              </div>
              <div className="committed">
                {d.committed.map((c: any) => (
                  <div key={c.season} className="committed-bar">
                    <span className="muted small">{c.season}</span>
                    <span className="bar"><span className="bar-fill r-good" style={{ width: `${Math.min(100, (c.amount / d.tax_line) * 100)}%` }} /></span>
                    <span className="small">{money(c.amount)}</span>
                  </div>
                ))}
                <p className="muted small">Committed salary by season (bar: share of the tax line).</p>
              </div>
            </Panel>
          )}
        </Fetched>
      )}
    </div>
  );
}
