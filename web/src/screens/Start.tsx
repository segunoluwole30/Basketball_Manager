import { useState } from "react";
import { api } from "../api";
import { useFetch, useGame } from "../context";
import { Empty, Fetched, money, Panel, Table } from "../components/ui";

export function SaveLoad({ mode, onDone }: { mode: "save" | "load"; onDone: () => void }) {
  const { state, act } = useGame();
  const saves = useFetch<any>("/api/saves");
  const current = state?.save_path?.split(/[\\/]/).pop() ?? "hoopsim_save.db";
  const [name, setName] = useState(mode === "save" ? current : "");
  const submit = async (path: string) => {
    const r = await act(() => api.post(`/api/${mode}`, { path }), mode === "save" ? (r: any) => r.message : "Game loaded");
    if (r) onDone();
  };
  return (
    <div className="saveload">
      {mode === "save" && (
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); submit(name); }}>
          <label className="grow">File name <input value={name} onChange={(e) => setName(e.target.value)} placeholder="my_league.db" /></label>
          <button className="btn btn-primary" type="submit" disabled={!name.trim()}>Save</button>
        </form>
      )}
      <Fetched f={saves} what="Looking for saves">
        {(d) => (
          <>
            <p className="muted small">Save files in {d.dir}</p>
            {d.saves.length ? (
              <ul className="file-list">
                {d.saves.map((s: any) => (
                  <li key={s.path}>
                    <button className="link" onClick={() => (mode === "load" ? submit(s.path) : setName(s.name))}>{s.name}</button>
                    <span className="muted small">{new Date(s.modified * 1000).toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty>No save files yet.</Empty>}
          </>
        )}
      </Fetched>
    </div>
  );
}

export function Start({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const { act } = useGame();
  const [teams, setTeams] = useState<any[] | null>(null);
  const [seed, setSeed] = useState("");
  const [count, setCount] = useState(12);
  const [engine, setEngine] = useState("statistical");
  const [loading, setLoading] = useState(false);
  const create = async () => {
    setLoading(true);
    const r = await act(() => api.post("/api/new", { seed: seed.trim() ? parseInt(seed, 10) : null, num_teams: count, match_engine: engine }));
    setLoading(false);
    if (r) setTeams(r.teams);
  };
  const pick = async (id: number) => {
    const r = await act(() => api.post("/api/new/team", { team_id: id }), "Welcome aboard!");
    if (r) onDone();
  };
  return (
    <div className="start">
      <div className="start-hero">
        <h1><span className="ball" aria-hidden>●</span> hoopsim <span className="muted">General Manager</span></h1>
        <p className="muted">Build a roster, set the tactics, survive the draft and free agency. Your scouts never tell you everything.</p>
      </div>
      {!teams ? (
        <div className="start-grid">
          <Panel title="New game">
            <form className="form" onSubmit={(e) => { e.preventDefault(); create(); }}>
              <label>Teams
                <select value={count} onChange={(e) => setCount(+e.target.value)}>
                  {[8, 10, 12, 14, 16, 20, 24, 30].map((n) => <option key={n} value={n}>{n} teams</option>)}
                </select>
              </label>
              <label>Match engine
                <select value={engine} onChange={(e) => setEngine(e.target.value)}>
                  <option value="statistical">Statistical (fast)</option>
                  <option value="spatial">Spatial: players on a court (~0.7 s a game)</option>
                </select>
              </label>
              <label>Seed <span className="muted small">(optional, for a repeatable league)</span>
                <input inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))} placeholder="random" />
              </label>
              <div className="row gap">
                <button className="btn btn-primary" type="submit" disabled={loading}>{loading ? "Generating…" : "Create league"}</button>
                {onCancel && <button className="btn" type="button" onClick={onCancel}>Back to my game</button>}
              </div>
            </form>
          </Panel>
          <Panel title="Load a saved game">
            <SaveLoad mode="load" onDone={onDone} />
          </Panel>
        </div>
      ) : (
        <Panel title="Choose your team" actions={<button className="btn btn-small" onClick={() => setTeams(null)}>Back</button>}>
          <p className="muted small">Strength is the average current ability of each team's top eight. Weaker teams have to outbid stronger ones for free agents.</p>
          <Table rows={teams} rowKey={(t) => t.id} initialSort="strength" onRowClick={(t) => pick(t.id)}
            columns={[
              { key: "abbr", label: "", render: (t) => <b>{t.abbr}</b> },
              { key: "name", label: "Team", sort: (t) => t.name, desc: false },
              { key: "conference", label: "Conf", desc: false },
              { key: "strength", label: "Strength", num: true },
              { key: "star", label: "Best player", sort: (t) => t.star, desc: false },
              { key: "payroll", label: "Payroll", num: true, render: (t) => money(t.payroll) },
              { key: "cap_room", label: "Cap room", num: true, render: (t) => money(t.cap_room) },
              { key: "go", label: "", render: (t) => <button className="btn btn-small btn-primary" onClick={(e) => { e.stopPropagation(); pick(t.id); }}>Manage</button> },
            ]} />
        </Panel>
      )}
    </div>
  );
}
