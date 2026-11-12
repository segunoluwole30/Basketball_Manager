import { useEffect, useState } from "react";
import { api } from "../api";
import { useFetch, useGame } from "../context";
import { CA, Fetched, money, MoneyInput, potText, Rating } from "./ui";
import { PlayerHistory } from "./PlayerHistory";

const TENDENCIES: { key: string; field: string; label: string }[] = [
  { key: "three", field: "three_point", label: "Threes" },
  { key: "mid", field: "mid_range", label: "Mid-range" },
  { key: "drive", field: "drive", label: "Drives" },
  { key: "post", field: "post_up", label: "Post-ups" },
  { key: "pass", field: "passing", label: "Passing" },
  { key: "cut", field: "cutting", label: "Cuts" },
];

const tendencyWord = (v: number) =>
  v <= 5 ? "avoids" : v <= 8 ? "rarely" : v >= 15 ? "often" : v >= 12 ? "sometimes" : "normal";

export function Instructions({ card }: { card: any }) {
  const { act } = useGame();
  const [values, setValues] = useState<Record<string, number>>({});
  useEffect(() => {
    setValues(Object.fromEntries(TENDENCIES.map((t) => [t.key, card.tendencies[t.field]])));
  }, [card]);
  const changed = TENDENCIES.some((t) => values[t.key] !== card.tendencies[t.field]);
  return (
    <div className="instructions">
      {TENDENCIES.map((t) => (
        <label key={t.key} className="slider-row">
          <span className="slider-label">{t.label}</span>
          <input type="range" min={1} max={20} value={values[t.key] ?? 10} disabled={!card.is_mine}
            onChange={(e) => setValues({ ...values, [t.key]: +e.target.value })} aria-label={t.label} />
          <span className="slider-val">{values[t.key]}</span>
          <span className="muted small slider-word">{tendencyWord(values[t.key] ?? 10)}</span>
        </label>
      ))}
      {card.is_mine && (
        <div className="row gap">
          <button className="btn btn-primary btn-small" disabled={!changed}
            onClick={() => act(() => api.post(`/api/player/${card.id}/instructions`, values), (r: any) => r.message)}>
            Save instructions
          </button>
          <button className="btn btn-small" disabled={!changed}
            onClick={() => setValues(Object.fromEntries(TENDENCIES.map((t) => [t.key, card.tendencies[t.field]])))}>
            Reset
          </button>
          <span className="muted small">10 = no preference. Changes what he chooses, not how well it goes.</span>
        </div>
      )}
    </div>
  );
}

function SignForm({ card }: { card: any }) {
  const { act, closePlayer } = useGame();
  const [salary, setSalary] = useState<number>(card.ask?.salary ?? 1_100_000);
  const [years, setYears] = useState<number>(card.ask?.years ?? 1);
  return (
    <div className="sign-form">
      <span>He wants <b>{money(card.ask?.salary, 2)}</b> × {card.ask?.years}y.</span>
      <MoneyInput key={card.id} value={salary} onChange={setSalary} />
      <select value={years} onChange={(e) => setYears(+e.target.value)} aria-label="Years">
        {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{y} yr{y > 1 ? "s" : ""}</option>)}
      </select>
      <button className="btn btn-primary btn-small"
        onClick={async () => {
          const r = await act(() => api.post("/api/sign", { player_id: card.id, salary, years }), (r: any) => r.message);
          if (r) closePlayer();
        }}>
        Offer contract
      </button>
    </div>
  );
}

export function PlayerCard() {
  const { playerId, closePlayer, state, act } = useGame();
  const f = useFetch<any>(playerId != null ? `/api/player/${playerId}` : null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closePlayer();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closePlayer]);
  if (playerId == null) return null;
  const data = f.data && f.data.id === playerId ? f.data : null;
  return (
    <div className="drawer-back" onClick={closePlayer}>
      <aside className="drawer" role="dialog" aria-label="Player card" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn drawer-close" aria-label="Close" onClick={closePlayer}>×</button>
        <button className="btn btn-small drawer-edit" title="Open in the editor (true ratings)"
          onClick={() => { window.location.hash = `/editor?player=${playerId}`; closePlayer(); }}>Edit</button>
        <Fetched f={{ ...f, data }} what="Loading player">
          {(p) => (
            <div className="card">
              <header className="card-head">
                <div className="card-jersey">{p.jersey ?? "–"}</div>
                <div>
                  <h2>{p.name}</h2>
                  <div className="muted">
                    {p.pos}{p.secondary.length ? `/${p.secondary.join("/")}` : ""} ·{" "}
                    {p.is_prospect ? "Draft prospect" : p.team ? (state?.teams?.[String(p.team_id)]?.name ?? p.team) : p.retired ? "Retired" : "Free agent"}
                    {" · "}Age {p.age} · {p.height} ({p.height_cm} cm) · {p.weight_kg} kg · {p.nationality}
                  </div>
                </div>
              </header>

              <div className="card-stats">
                <div className="stat"><div className="stat-label">Current ability</div><div className="stat-value"><CA v={p.ca} /></div></div>
                <div className="stat" title="Your scouts' estimate. The range narrows with age and track record, and is tighter for your own players.">
                  <div className="stat-label">Potential (scouted)</div>
                  <div className="stat-value">{potText(p.potential)}</div>
                  <div className="stat-sub">certainty: {p.potential.peaked ? "n/a" : p.scouting_certainty}</div>
                </div>
                <div className="stat"><div className="stat-label">Contract</div>
                  <div className="stat-value">{p.salary ? money(p.salary, 2) : p.ask ? `asks ${money(p.ask.salary, 2)}` : "–"}</div>
                  <div className="stat-sub">{p.thru ? `through ${p.thru}${p.option ? `, ${p.option} option` : ""}` : p.ask ? `${p.ask.years}-year deal` : ""}</div>
                </div>
                <div className="stat"><div className="stat-label">Morale</div><div className="stat-value"><span className={`morale m-${p.morale_word}`}>{p.morale_word}</span></div>
                  <div className="stat-sub">{p.injury ? <span className="bad">{p.injury.description}, out {p.injury.games} g</span> : "healthy"}</div></div>
              </div>

              {(p.all_star_now || Object.values(p.honours ?? {}).some((v: any) => v > 0)) && (
                <div className="notes as-honours">
                  {p.all_star_now && <span className="chip chip-star">★ All-Star this season</span>}
                  {p.honours?.all_star > 0 && <span className="chip chip-star">{p.honours.all_star}× All-Star</span>}
                  {p.honours?.asg_mvp > 0 && <span className="chip">{p.honours.asg_mvp}× All-Star MVP</span>}
                  {p.honours?.three_point_champion > 0 && <span className="chip">{p.honours.three_point_champion}× 3-Point champion</span>}
                  {p.honours?.rising_stars_mvp > 0 && <span className="chip">Rising Stars MVP</span>}
                  {p.honours?.mvp > 0 && <span className="chip chip-star">{p.honours.mvp}× MVP</span>}
                  {p.honours?.finals_mvp > 0 && <span className="chip chip-star">{p.honours.finals_mvp}× Finals MVP</span>}
                  {p.honours?.dpoy > 0 && <span className="chip">{p.honours.dpoy}× Defensive POY</span>}
                  {p.honours?.roy > 0 && <span className="chip">Rookie of the Year</span>}
                  {p.honours?.sixth_man > 0 && <span className="chip">{p.honours.sixth_man}× Sixth Man</span>}
                  {p.honours?.mip > 0 && <span className="chip">Most Improved</span>}
                  {p.honours?.all_nba > 0 && <span className="chip">{p.honours.all_nba}× All-NBA</span>}
                  {p.honours?.all_defense > 0 && <span className="chip">{p.honours.all_defense}× All-Defensive</span>}
                </div>
              )}
              {p.draft && <p className="muted small">{p.draft.pick ? `Pick #${p.draft.pick} in the ${p.draft.year} draft.` : `Undrafted in ${p.draft.year}.`}</p>}

              <h3>Scouting notes</h3>
              <div className="notes">
                {p.scouting_notes.length ? p.scouting_notes.map((n: string) => <span key={n} className="chip">{n}</span>)
                  : <span className="muted">Nothing stands out.</span>}
              </div>

              <h3>Attributes</h3>
              <div className="attr-grid">
                {p.attributes.map((g: any) => (
                  <div key={g.group} className="attr-group">
                    <h4>{g.group}</h4>
                    {g.ratings.map((r: any) => (
                      <div key={r.key} className="attr-row">
                        <span className="attr-name">{r.label}</span>
                        <span className="attr-bar"><span style={{ width: `${(r.value / 20) * 100}%` }} className={`bar-fill ${r.value >= 16 ? "r-elite" : r.value >= 13 ? "r-good" : r.value >= 9 ? "r-avg" : "r-poor"}`} /></span>
                        <Rating v={r.value} />
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div className="positions">
                <span className="muted small">Ability by position:</span>
                {Object.entries(p.at_positions).map(([pos, v]) => (
                  <span key={pos} className={`chip ${pos === p.pos ? "chip-on" : ""}`}>{pos} {v as number}</span>
                ))}
              </div>

              <h3>Player instructions {p.is_mine ? "" : <span className="muted small">(his tendencies)</span>}</h3>
              <Instructions card={p} />

              {p.season && (
                <>
                  <h3>This season</h3>
                  <div className="season-line">
                    {[["GP", p.season.gp], ["MPG", p.season.mpg], ["PPG", p.season.ppg], ["RPG", p.season.rpg], ["APG", p.season.apg],
                      ["SPG", p.season.spg], ["BPG", p.season.bpg], ["FG%", p.season.fg_pct.toFixed(3)], ["3P%", p.season.three_pct.toFixed(3)],
                      ["FT%", p.season.ft_pct.toFixed(3)]].map(([k, v]) => (
                      <div key={k as string}><div className="muted small">{k}</div><b>{v}</b></div>
                    ))}
                  </div>
                </>
              )}

              {!p.is_prospect && <PlayerHistory id={p.id} />}

              <div className="card-actions">
                {p.ask && state?.phase !== "offseason" && <SignForm card={p} />}
                {p.ask && state?.phase === "offseason" && <span className="muted small">Free agents sign in the offseason's free-agency rounds.</span>}
                {p.is_mine && (
                  <button className="btn btn-danger btn-small" onClick={async () => {
                    if (!window.confirm(`Release ${p.name}? His salary comes off your books.`)) return;
                    const r = await act(() => api.post("/api/release", { player_id: p.id }), (r: any) => r.message);
                    if (r) closePlayer();
                  }}>Release</button>
                )}
                {!p.is_mine && p.team_id != null && !p.is_prospect && (
                  <a className="btn btn-small" href={`#/transfers?team=${p.team_id}&get=${p.id}`} onClick={closePlayer}>Trade for him</a>
                )}
              </div>
            </div>
          )}
        </Fetched>
      </aside>
    </div>
  );
}
