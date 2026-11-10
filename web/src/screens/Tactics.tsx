import { useEffect, useMemo, useState } from "react";
import { api, PlayerRow } from "../api";
import { Instructions } from "../components/PlayerCard";
import { CA, Fetched, Panel, PlayerName } from "../components/ui";
import { useFetch, useGame } from "../context";

const SLOTS = ["PG", "SG", "SF", "PF", "C"];
const TOTAL = 240;

/** Scale the unlocked players so the total is exactly 240 (each 0-48). */
function balance(minutes: Record<number, number>, locked: Set<number>): Record<number, number> {
  const out = { ...minutes };
  const fixed = Object.entries(out).filter(([id]) => locked.has(+id)).reduce((s, [, m]) => s + m, 0);
  const flex = Object.keys(out).map(Number).filter((id) => !locked.has(id) && out[id] > 0);
  const flexSum = flex.reduce((s, id) => s + out[id], 0);
  const room = Math.max(0, TOTAL - fixed);
  flex.forEach((id) => (out[id] = Math.max(1, Math.min(48, Math.round((out[id] * room) / Math.max(1, flexSum))))));
  const ids = flex.length ? flex : Object.keys(out).map(Number).filter((id) => out[id] > 0);
  let i = 0, guard = 0;
  const sum = () => Object.values(out).reduce((s, m) => s + m, 0);
  while (sum() !== TOTAL && ids.length && guard++ < 5000) {
    const id = ids[i++ % ids.length];
    const step = sum() < TOTAL ? 1 : -1;
    if (out[id] + step >= 1 && out[id] + step <= 48) out[id] += step;
  }
  return out;
}

function Lineup({ d }: { d: any }) {
  const { act, busy } = useGame();
  const players: PlayerRow[] = d.players;
  const byId = useMemo(() => Object.fromEntries(players.map((p) => [p.id, p])), [players]);
  const [starters, setStarters] = useState<number[]>(d.starters.map((s: any) => s.player_id));
  const [minutes, setMinutes] = useState<Record<number, number>>({});
  const [touched, setTouched] = useState<Set<number>>(new Set());
  useEffect(() => {
    setStarters(d.starters.map((s: any) => s.player_id));
    setMinutes(Object.fromEntries(players.map((p) => [p.id, p.minutes_target ?? 0])));
    setTouched(new Set());
  }, [d, players]);
  const total = Object.values(minutes).reduce((s, m) => s + m, 0);
  const startersChanged = starters.join() !== d.starters.map((s: any) => s.player_id).join();
  const minutesChanged = players.some((p) => (minutes[p.id] ?? 0) !== (p.minutes_target ?? 0));
  const dupes = new Set(starters.filter((id, i) => starters.indexOf(id) !== i));
  const order = [...players].sort((a, b) => (b.minutes_target ?? 0) - (a.minutes_target ?? 0) || b.ca - a.ca);

  return (
    <div className="tactics-grid">
      <Panel title="Starting five" actions={
        <label className="toggle">
          <input type="checkbox" checked={d.auto} disabled={busy}
            onChange={(e) => act(() => api.post("/api/lineup/auto", { on: e.target.checked }), (r: any) => r.message)} />
          Assistant picks lineup &amp; minutes
        </label>}>
        <div className="court">
          {SLOTS.map((slot, i) => {
            const p = byId[starters[i]];
            return (
              <div key={slot} className={`slot slot-${slot} ${dupes.has(starters[i]) ? "slot-bad" : ""}`}>
                <div className="slot-pos">{slot}</div>
                <select value={starters[i] ?? ""} aria-label={`${slot} starter`}
                  onChange={(e) => { const s = [...starters]; s[i] = +e.target.value; setStarters(s); }}>
                  {players.map((q) => (
                    <option key={q.id} value={q.id} disabled={!!q.injury}>
                      {q.at_positions[slot]} · {q.name} ({q.pos}){q.injury ? " – injured" : ""}
                    </option>
                  ))}
                </select>
                {p && <div className="slot-info"><CA v={p.at_positions[slot]} /> <span className="muted small">at {slot} · natural {p.pos}{p.injury ? " · INJURED" : ""}</span></div>}
              </div>
            );
          })}
        </div>
        <div className="row gap">
          <button className="btn btn-primary btn-small" disabled={!startersChanged || dupes.size > 0 || busy}
            onClick={() => act(() => api.post("/api/lineup/starters", { ids: starters }), (r: any) => r.message)}>
            Set starters
          </button>
          {dupes.size > 0 && <span className="bad small">A player can only start once.</span>}
          <span className="muted small">Numbers are ability at that slot (out of position costs).</span>
        </div>
      </Panel>

      <Panel title="Minutes" actions={<span className={`minutes-total ${total === TOTAL ? "good" : "bad"}`}>{total} / {TOTAL}</span>}>
        <div className="minutes">
          {order.map((p) => (
            <div key={p.id} className={`minutes-row ${p.injury ? "row-injured" : ""}`}>
              <span className="minutes-name"><PlayerName p={p} />{starters.includes(p.id) && <span className="badge badge-info">S</span>}</span>
              <input type="range" min={0} max={48} value={minutes[p.id] ?? 0} aria-label={`${p.name} minutes`}
                onChange={(e) => { setMinutes({ ...minutes, [p.id]: +e.target.value }); setTouched(new Set(touched).add(p.id)); }} />
              <input type="number" min={0} max={48} className="minutes-num" value={minutes[p.id] ?? 0} aria-label={`${p.name} minutes number`}
                onChange={(e) => { setMinutes({ ...minutes, [p.id]: Math.max(0, Math.min(48, +e.target.value || 0)) }); setTouched(new Set(touched).add(p.id)); }} />
            </div>
          ))}
        </div>
        <div className="row gap wrap">
          <button className="btn btn-small" onClick={() => setMinutes(balance(minutes, touched))} disabled={total === TOTAL}>
            Balance to 240
          </button>
          <button className="btn btn-primary btn-small" disabled={total !== TOTAL || !minutesChanged || busy}
            onClick={() => act(() => api.post("/api/lineup/minutes", { minutes: Object.fromEntries(Object.entries(minutes)) }), "Minutes saved")}>
            Save minutes
          </button>
          <button className="btn btn-small btn-ghost" disabled={!minutesChanged} onClick={() => setMinutes(Object.fromEntries(players.map((p) => [p.id, p.minutes_target ?? 0])))}>Reset</button>
          <span className="muted small">{total === TOTAL ? "Totals 240: five players on court for 48 minutes." : "Minutes must total exactly 240. Balance keeps the ones you moved and scales the rest."}</span>
        </div>
      </Panel>
    </div>
  );
}

const SLIDERS: { key: string; label: string; min: number; max: number; help: string }[] = [
  { key: "pace", label: "Pace", min: 1, max: 20, help: "Shorter setup and earlier shots." },
  { key: "three", label: "Three-point rate", min: 1, max: 20, help: "Team-wide willingness to shoot threes." },
  { key: "crash", label: "Crash the glass", min: 1, max: 20, help: "How many players attack the offensive glass." },
  { key: "depth", label: "Rotation depth", min: 5, max: 15, help: "How many players get regular minutes (assistant's plan)." },
];
const FOCUS_HELP: Record<string, string> = {
  inside: "Both bigs on the blocks; favours post entries.",
  balanced: "No preference.",
  perimeter: "Spaces five-out and hunts threes.",
};
const DEFENSE_HELP: Record<string, string> = {
  man: "Man-to-man with drop coverage.",
  switch: "Swap assignments on screens.",
  zone: "Packs the paint, concedes perimeter looks.",
  press: "Forces turnovers but gets beaten off the dribble more, and tires players.",
};

function TeamTactics({ t }: { t: any }) {
  const { act, busy } = useGame();
  const [v, setV] = useState<any>(t);
  useEffect(() => setV(t), [t]);
  const changed = ["pace", "three", "crash", "depth", "focus", "defense"].some((k) => v[k] !== t[k]);
  return (
    <Panel title="Team tactics">
      <div className="form">
        {SLIDERS.map((s) => (
          <label key={s.key} className="slider-row">
            <span className="slider-label">{s.label}</span>
            <input type="range" min={s.min} max={s.max} value={v[s.key]} onChange={(e) => setV({ ...v, [s.key]: +e.target.value })} aria-label={s.label} />
            <span className="slider-val">{v[s.key]}</span>
            <span className="muted small slider-word">{s.help}</span>
          </label>
        ))}
        <div className="choice-group">
          <span className="slider-label">Offensive focus</span>
          {t.choices.focus.map((f: string) => (
            <label key={f} className={`choice ${v.focus === f ? "choice-on" : ""}`} title={FOCUS_HELP[f]}>
              <input type="radio" name="focus" checked={v.focus === f} onChange={() => setV({ ...v, focus: f })} /> {f}
            </label>
          ))}
          <span className="muted small">{FOCUS_HELP[v.focus]}</span>
        </div>
        <div className="choice-group">
          <span className="slider-label">Defence</span>
          {t.choices.defense.map((f: string) => (
            <label key={f} className={`choice ${v.defense === f ? "choice-on" : ""}`} title={DEFENSE_HELP[f]}>
              <input type="radio" name="defense" checked={v.defense === f} onChange={() => setV({ ...v, defense: f })} /> {f}
            </label>
          ))}
          <span className="muted small">{DEFENSE_HELP[v.defense]}</span>
        </div>
        <div className="row gap">
          <button className="btn btn-primary btn-small" disabled={!changed || busy}
            onClick={() => act(() => api.post("/api/tactics", { pace: v.pace, three: v.three, crash: v.crash, depth: v.depth, focus: v.focus, defense: v.defense }), "Tactics saved")}>
            Save tactics
          </button>
          <button className="btn btn-small btn-ghost" disabled={!changed} onClick={() => setV(t)}>Reset</button>
        </div>
      </div>
    </Panel>
  );
}

function PlayerInstructions({ players }: { players: PlayerRow[] }) {
  const [pid, setPid] = useState<number>(players[0]?.id);
  const card = useFetch<any>(pid ? `/api/player/${pid}` : null);
  return (
    <Panel title="Player instructions" actions={
      <select value={pid} onChange={(e) => setPid(+e.target.value)} aria-label="Player">
        {players.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.pos})</option>)}
      </select>}>
      <Fetched f={{ ...card, data: card.data?.id === pid ? card.data : null }} what="Loading player">
        {(c) => (
          <>
            <p className="muted small">{c.name}: shooting 3PT {c.three}, mid {c.mid}, finishing {c.fin}, passing {c.pas}. Tendencies start from his skills, with some personality.</p>
            <Instructions card={c} />
          </>
        )}
      </Fetched>
    </Panel>
  );
}

export function Tactics() {
  const f = useFetch<any>("/api/lineup");
  return (
    <div>
      <div className="screen-head"><h1>Tactics</h1></div>
      <Fetched f={f} what="Loading lineup">
        {(d) => (
          <>
            {d.auto && <div className="callout callout-info">Your assistant is picking the lineup and minutes. Changing either takes over.</div>}
            <Lineup d={d} />
            <div className="tactics-grid">
              <TeamTactics t={d.tactics} />
              <PlayerInstructions players={d.players} />
            </div>
          </>
        )}
      </Fetched>
    </div>
  );
}
