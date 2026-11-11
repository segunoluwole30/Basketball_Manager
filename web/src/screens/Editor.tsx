import { useEffect, useState } from "react";
import { api } from "../api";
import { useFetch, useGame } from "../context";
import { Empty, Fetched, money, Panel } from "../components/ui";
import { TeamPicker } from "./Squad";

const GROUPS: [string, string[]][] = [
  ["Scoring", ["finishing", "close_range", "mid_range", "three_point", "free_throw"]],
  ["Playmaking", ["passing", "ball_handling", "vision"]],
  ["Defence", ["perimeter_defense", "interior_defense", "stealing", "blocking"]],
  ["Rebounding", ["offensive_rebounding", "defensive_rebounding"]],
  ["Physical", ["speed", "strength", "vertical", "stamina"]],
  ["Mental", ["basketball_iq", "composure"]],
];
const label = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()).replace("Iq", "IQ");

type Row = { id: number; name: string; pos: string; age: number; ca: number; pa: number; team: string | null };

/** The in-game editor (like FM's): true ratings, any player or team, changes apply at once. */
export function Editor({ params }: { params: URLSearchParams }) {
  const { state } = useGame();
  const [team, setTeam] = useState<number | null>(state?.team?.id ?? null);
  const [fa, setFa] = useState(false);
  const [q, setQ] = useState("");
  const [pid, setPid] = useState<number | null>(params.get("player") ? +params.get("player")! : null);
  const url = q.trim().length >= 2 ? `/api/editor/players?q=${encodeURIComponent(q.trim())}`
    : fa ? "/api/editor/players?free_agents=true" : team ? `/api/editor/players?team_id=${team}` : null;
  const list = useFetch<{ players: Row[]; edited: { count: number } | null }>(url);
  return (
    <div className="screen">
      <p className="muted small editor-note">
        The editor shows true ratings (no scouting fog) and changes them for good. Saves record that the editor was used
        {list.data?.edited ? ` (${list.data.edited.count} edit${list.data.edited.count === 1 ? "" : "s"} so far)` : ""}.
      </p>
      <div className="editor-layout">
        <Panel title="Players" actions={
          <span className="row gap">
            <TeamPicker value={fa ? null : team} onChange={(t) => { setFa(false); setTeam(t); setQ(""); }} />
            <button className={`btn btn-small ${fa ? "btn-primary" : ""}`} onClick={() => { setFa(true); setQ(""); }}>Free agents</button>
          </span>}>
          <input className="editor-search" placeholder="Search any player by name…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search players" />
          {team && !fa && !q && <TeamEditor id={team} />}
          <Fetched f={list} what="Loading players">
            {(d) => d.players.length ? (
              <ul className="editor-list">
                {d.players.map((p) => (
                  <li key={p.id}>
                    <button className={`editor-row ${pid === p.id ? "sel" : ""}`} onClick={() => setPid(p.id)}>
                      <span className="grow">{p.name}</span>
                      <span className="muted small">{p.pos} · {p.age}y{q && p.team ? ` · ${p.team}` : ""}</span>
                      <b>{p.ca}</b><span className="muted small">/{p.pa}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : <Empty>No players.</Empty>}
          </Fetched>
        </Panel>
        {pid !== null ? <PlayerEditor key={pid} id={pid} onSaved={list.reload} /> : <Panel title="Edit a player"><Empty>Pick a player to edit.</Empty></Panel>}
      </div>
    </div>
  );
}

function TeamEditor({ id }: { id: number }) {
  const { state, act } = useGame();
  const t = state?.teams?.[String(id)];
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({});
  useEffect(() => { setOpen(false); setForm({}); }, [id]);
  if (!t) return null;
  if (!open) return <button className="link small" onClick={() => setOpen(true)}>Edit team name and details…</button>;
  const save = async () => {
    const r = await act(() => api.post(`/api/editor/team/${id}`, form), (r: any) => r.message);
    if (r) setOpen(false);
  };
  return (
    <form className="form editor-team" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <div className="row gap">
        <label>City <input defaultValue={t.city ?? ""} onChange={(e) => setForm({ ...form, city: e.target.value })} /></label>
        <label>Name <input defaultValue={t.nickname ?? ""} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Abbr <input size={4} defaultValue={t.abbr ?? ""} onChange={(e) => setForm({ ...form, abbreviation: e.target.value })} /></label>
      </div>
      <label>Reputation (1-100) <input type="number" min={1} max={100} defaultValue={t.reputation ?? ""} onChange={(e) => setForm({ ...form, reputation: +e.target.value })} /></label>
      <div className="row gap">
        <button className="btn btn-primary btn-small" type="submit">Save team</button>
        <button className="btn btn-small" type="button" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

function PlayerEditor({ id, onSaved }: { id: number; onSaved?: () => void }) {
  const { act, openPlayer } = useGame();
  const f = useFetch<any>(`/api/editor/player/${id}`);
  const [sheet, setSheet] = useState<any>(null);
  useEffect(() => { if (f.data) setSheet(structuredClone(f.data)); }, [f.data]);
  return (
    <Fetched f={f} what="Loading player">
      {(orig) => {
        const s = sheet ?? orig;
        const set = (k: string, v: any) => setSheet({ ...s, [k]: v });
        const setIn = (block: string, k: string, v: any) => setSheet({ ...s, [block]: { ...s[block], [k]: v } });
        const diff: any = {};
        for (const k of ["first_name", "last_name", "nationality", "birth_date", "position", "height_cm", "wingspan_cm", "weight_kg", "jersey_number", "potential_ability", "morale", "team_id"])
          if (s[k] !== orig[k]) diff[k] = s[k];
        if (JSON.stringify(s.secondary_positions) !== JSON.stringify(orig.secondary_positions)) diff.secondary_positions = s.secondary_positions;
        for (const b of ["attributes", "hidden", "tendencies"]) {
          const ch = Object.fromEntries(Object.entries(s[b]).filter(([k, v]) => orig[b][k] !== v));
          if (Object.keys(ch).length) diff[b] = ch;
        }
        if (s.contract && orig.contract && JSON.stringify(s.contract) !== JSON.stringify(orig.contract)) diff.contract = s.contract;
        if (s.heal) diff.heal = true;
        const dirty = Object.keys(diff).length > 0;
        const save = async () => {
          const r: any = await act(() => api.post(`/api/editor/player/${id}`, diff), (r: any) => r.message);
          if (r) { setSheet(structuredClone(r.player)); onSaved?.(); }
        };
        const L = s.limits;
        const num = (k: string, lo: number, hi: number, block?: string) => (
          <input type="number" min={lo} max={hi} value={block ? s[block][k] : s[k] ?? ""} aria-label={label(k)}
            onChange={(e) => { const v = e.target.value === "" ? null : +e.target.value; block ? setIn(block, k, v) : set(k, v); }} />
        );
        return (
          <Panel title={`${orig.first_name} ${orig.last_name}`} actions={
            <span className="row gap">
              <button className="btn btn-small" onClick={() => openPlayer(id)}>Card</button>
              <button className="btn btn-small" disabled={!dirty} onClick={() => setSheet(structuredClone(orig))}>Undo</button>
              <button className="btn btn-small btn-primary" disabled={!dirty} onClick={save}>Save changes</button>
            </span>}>
            <div className="editor-summary">
              <span>Current ability <b>{orig.current_ability}</b></span>
              <label>Potential <span className="muted small">({orig.current_ability}-{L.ability[1]})</span> {num("potential_ability", L.ability[0], L.ability[1])}</label>
              <label>Morale {num("morale", 0, 100)}</label>
              {orig.injury && <label className="row gap"><input type="checkbox" checked={!!s.heal} onChange={(e) => set("heal", e.target.checked)} /> Heal ({orig.injury.description})</label>}
            </div>
            <h3>Details</h3>
            <div className="editor-fields">
              <label>First name <input value={s.first_name} onChange={(e) => set("first_name", e.target.value)} /></label>
              <label>Last name <input value={s.last_name} onChange={(e) => set("last_name", e.target.value)} /></label>
              <label>Born <input type="date" value={s.birth_date} onChange={(e) => set("birth_date", e.target.value)} /></label>
              <label>Nationality <input value={s.nationality} onChange={(e) => set("nationality", e.target.value)} /></label>
              <label>Position
                <select value={s.position} onChange={(e) => set("position", e.target.value)}>
                  {L.positions.map((p: string) => <option key={p}>{p}</option>)}
                </select>
              </label>
              <label>Also plays
                <span className="row gap">
                  {L.positions.filter((p: string) => p !== s.position).map((p: string) => (
                    <label key={p} className="chk"><input type="checkbox" checked={s.secondary_positions.includes(p)}
                      onChange={(e) => set("secondary_positions", e.target.checked ? [...s.secondary_positions, p] : s.secondary_positions.filter((x: string) => x !== p))} />{p}</label>
                  ))}
                </span>
              </label>
              <label>Height (cm) {num("height_cm", 160, 235)}</label>
              <label>Wingspan (cm) {num("wingspan_cm", 150, 250)}</label>
              <label>Weight (kg) {num("weight_kg", 60, 160)}</label>
              <label>Jersey {num("jersey_number", 0, 99)}</label>
              <label>Team <TeamOrFree value={s.team_id} onChange={(v) => set("team_id", v)} /></label>
            </div>
            <h3>Ratings <span className="muted small">(1-20)</span></h3>
            <div className="editor-attrs">
              {GROUPS.map(([g, keys]) => (
                <div key={g} className="attr-group">
                  <h4>{g}</h4>
                  {keys.map((k) => (
                    <div key={k} className={`editor-attr ${s.attributes[k] !== orig.attributes[k] ? "changed" : ""}`}>
                      <span className="attr-name">{label(k)}</span>
                      <input type="range" min={1} max={20} value={s.attributes[k]} aria-label={label(k)}
                        onChange={(e) => setIn("attributes", k, +e.target.value)} />
                      <b>{s.attributes[k]}</b>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="editor-attrs">
              {(["hidden", "tendencies"] as const).map((b) => (
                <div key={b} className="attr-group">
                  <h4>{b === "hidden" ? "Hidden" : "Tendencies"}</h4>
                  {Object.keys(s[b]).map((k) => (
                    <div key={k} className={`editor-attr ${s[b][k] !== orig[b][k] ? "changed" : ""}`}>
                      <span className="attr-name">{label(k)}</span>
                      <input type="range" min={1} max={20} value={s[b][k]} aria-label={`${b} ${label(k)}`}
                        onChange={(e) => setIn(b, k, +e.target.value)} />
                      <b>{s[b][k]}</b>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            {s.contract && (
              <>
                <h3>Contract</h3>
                <div className="editor-fields">
                  <label>Salary <span className="muted small">({money(s.contract.salary, 2)})</span>
                    <input type="number" min={0} max={L.salary[1]} step={10000} value={s.contract.salary}
                      onChange={(e) => setIn("contract", "salary", +e.target.value)} /></label>
                  <label>Through season
                    <input type="number" min={L.expires_season[0]} max={L.expires_season[1]} value={s.contract.expires_season}
                      onChange={(e) => setIn("contract", "expires_season", +e.target.value)} /></label>
                  <label>Option
                    <select value={s.contract.option ?? ""} onChange={(e) => setIn("contract", "option", e.target.value || null)}>
                      <option value="">None</option><option value="player">Player option</option><option value="team">Team option</option>
                    </select>
                  </label>
                </div>
              </>
            )}
          </Panel>
        );
      }}
    </Fetched>
  );
}

function TeamOrFree({ value, onChange }: { value: number | null; onChange: (v: number | null) => void }) {
  const { state } = useGame();
  const teams = Object.values(state?.teams ?? {}).sort((a: any, b: any) => a.name.localeCompare(b.name));
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? +e.target.value : null)}>
      <option value="">Free agent</option>
      {teams.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}
    </select>
  );
}
