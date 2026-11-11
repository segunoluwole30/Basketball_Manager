import { useState } from "react";
import { GameView } from "../api";
import { useFetch, useGame } from "../context";
import { Empty, ErrorBox, Fetched, Loading, Modal, Panel, PlayerName, pct, shortDate, Table, Tabs } from "../components/ui";
import { TeamPicker } from "./Squad";

type Tab = "standings" | "leaders" | "schedule" | "results" | "playoffs" | "history";

export function BoxScoreModal({ id, onClose }: { id: number; onClose: () => void }) {
  const f = useFetch<any>(`/api/game/${id}`);
  const g = f.data && f.data.id === id ? f.data : null;
  return (
    <Modal wide title={g ? `${g.away} ${g.away_score ?? ""} @ ${g.home} ${g.home_score ?? ""}${g.ot ? ` (${g.ot}OT)` : ""}` : "Box score"} onClose={onClose}>
      {f.error ? <ErrorBox error={f.error} /> : !g ? <Loading what="Loading box score" /> : !g.box ? (
        <Empty>{g.played ? "Box scores are kept for the current season only." : "Not played yet."}</Empty>
      ) : (
        <div className="box">
          <div className="muted small">{shortDate(g.date)}{g.playoff ? " · playoffs" : ""}</div>
          <table className="table table-dense linescore">
            <thead><tr><th>Team</th>{g.box.away.periods.map((_: number, i: number) => <th key={i} className="num">{i < 4 ? `Q${i + 1}` : `OT${i - 3}`}</th>)}<th className="num">T</th></tr></thead>
            <tbody>
              {[g.box.away, g.box.home].map((t: any) => (
                <tr key={t.team_id}><td><b>{t.abbr}</b></td>{t.periods.map((p: number, i: number) => <td key={i} className="num">{p}</td>)}<td className="num"><b>{t.totals.pts}</b></td></tr>
              ))}
            </tbody>
          </table>
          {[g.box.away, g.box.home].map((t: any) => (
            <div key={t.team_id}>
              <h3>{t.name}</h3>
              <Table dense rows={t.lines} rowKey={(l: any) => l.player_id} initialSort="min"
                rowClass={(l: any) => (l.starter ? "row-starter" : "")}
                columns={[
                  { key: "name", label: "Player", sort: (l: any) => l.name, desc: false, render: (l: any) => <><PlayerName p={{ id: l.player_id, name: l.name }} /> <span className="muted small">{l.pos}</span></> },
                  { key: "min", label: "MIN", num: true, render: (l: any) => l.min.toFixed(0) },
                  { key: "pts", label: "PTS", num: true },
                  { key: "fg", label: "FG", num: true, sort: (l: any) => l.fgm, render: (l: any) => `${l.fgm}-${l.fga}` },
                  { key: "tp", label: "3PT", num: true, sort: (l: any) => l.tpm, render: (l: any) => `${l.tpm}-${l.tpa}` },
                  { key: "ft", label: "FT", num: true, sort: (l: any) => l.ftm, render: (l: any) => `${l.ftm}-${l.fta}` },
                  { key: "trb", label: "REB", num: true },
                  { key: "ast", label: "AST", num: true },
                  { key: "stl", label: "STL", num: true },
                  { key: "blk", label: "BLK", num: true },
                  { key: "tov", label: "TO", num: true },
                  { key: "pf", label: "PF", num: true },
                  { key: "pm", label: "+/-", num: true, render: (l: any) => (l.pm > 0 ? `+${l.pm}` : l.pm) },
                ]} />
              <div className="muted small totals-line">
                Team: {t.totals.pts} pts, {t.totals.fgm}-{t.totals.fga} FG ({pct(t.totals.fgm / Math.max(1, t.totals.fga))}), {t.totals.tpm}-{t.totals.tpa} 3PT, {t.totals.ftm}-{t.totals.fta} FT, {t.totals.trb} reb ({t.totals.orb} off), {t.totals.ast} ast, {t.totals.tov} TO
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function Standings() {
  const f = useFetch<any>("/api/standings");
  return (
    <Fetched f={f} what="Loading standings">
      {(d) => (
        <div className="two-col">
          {d.conferences.map((c: any) => (
            <Panel key={c.conference} title={`${c.conference}ern Conference`.replace("ernern", "ern")}>
              <Table dense rows={c.teams} rowKey={(r: any) => r.team_id} rowClass={(r: any) => (r.team_id === d.user_team_id ? "row-me" : "")}
                columns={[
                  { key: "rank", label: "#", num: true, desc: false },
                  { key: "name", label: "Team", sort: (r: any) => r.name, desc: false },
                  { key: "w", label: "W", num: true },
                  { key: "l", label: "L", num: true },
                  { key: "pct", label: "PCT", num: true, render: (r: any) => pct(r.pct) },
                  { key: "gb", label: "GB", num: true, desc: false, render: (r: any) => (r.gb ? r.gb.toFixed(1) : "–") },
                  { key: "net", label: "NET", title: "Point differential per game", num: true, render: (r: any) => (r.net > 0 ? `+${r.net}` : r.net) },
                  { key: "last10", label: "L10", sort: (r: any) => +r.last10.split("-")[0] },
                  { key: "streak", label: "STRK" },
                  { key: "strength", label: "STR", title: "Top-8 average current ability", num: true },
                ]} />
            </Panel>
          ))}
        </div>
      )}
    </Fetched>
  );
}

function Leaders() {
  const f = useFetch<any>("/api/leaders");
  return (
    <Fetched f={f} what="Loading leaders">
      {(d) => !d.categories.length ? <Empty>No games played yet.</Empty> : (
        <>
          <p className="muted small">Per game; at least {d.min_games} games played.</p>
          <div className="leader-grid">
            {d.categories.map((c: any) => (
              <Panel key={c.key} title={c.label}>
                {c.rows.length ? (
                  <ol className="leaders">
                    {c.rows.map((r: any) => (
                      <li key={r.player_id}><PlayerName p={{ id: r.player_id, name: r.name }} /> <span className="muted small">{r.team}</span> <b>{r.value < 1 && c.key.length === 2 ? pct(r.value) : r.value}</b></li>
                    ))}
                  </ol>
                ) : <Empty>Nobody qualifies yet.</Empty>}
              </Panel>
            ))}
          </div>
        </>
      )}
    </Fetched>
  );
}

function GamesTable({ games, onBox, me }: { games: GameView[]; onBox: (id: number) => void; me?: number }) {
  return (
    <Table dense rows={games} rowKey={(g) => g.id} empty="No games."
      onRowClick={(g) => g.has_box && onBox(g.id)}
      columns={[
        { key: "date", label: "Date", sort: (g) => g.day * 1000 + g.id, desc: false, render: (g) => shortDate(g.date) },
        { key: "away", label: "Away", desc: false, render: (g) => <span className={g.away_id === me ? "me" : ""}>{g.away}</span> },
        { key: "score", label: "Score", sort: (g) => g.day, render: (g) => (g.played ? `${g.away_score} – ${g.home_score}${g.ot ? ` (${g.ot}OT)` : ""}` : "–") },
        { key: "home", label: "Home", desc: false, render: (g) => <span className={g.home_id === me ? "me" : ""}>{g.home}</span> },
        { key: "res", label: "", render: (g) => (g.won === undefined ? (g.playoff ? <span className="muted small">PO</span> : "") : <b className={g.won ? "good" : "bad"}>{g.won ? "W" : "L"}</b>) },
        { key: "box", label: "", render: (g) => (
          <span className="row gap">
            {g.has_box && <button className="link small" onClick={(e) => { e.stopPropagation(); onBox(g.id); }}>box</button>}
            {g.has_replay && <a className="link small" href={`/api/replay/${g.id}`} target="_blank" rel="noopener" onClick={(e) => e.stopPropagation()}>watch</a>}
          </span>
        ) },
      ]} />
  );
}

function Schedule({ onBox }: { onBox: (id: number) => void }) {
  const { state } = useGame();
  const [team, setTeam] = useState<number>(state?.team?.id ?? 0);
  const f = useFetch<any>(`/api/schedule?team_id=${team}`);
  return (
    <Panel title="Schedule & results" actions={<TeamPicker value={team} onChange={setTeam} />}>
      <Fetched f={f} what="Loading schedule">
        {(d) => {
          const played = d.games.filter((g: GameView) => g.played);
          const left = d.games.filter((g: GameView) => !g.played);
          return (
            <div className="two-col">
              <div><h3>Results ({played.length})</h3><GamesTable games={[...played].reverse()} onBox={onBox} me={team} /></div>
              <div><h3>Upcoming ({left.length})</h3><GamesTable games={left} onBox={onBox} me={team} /></div>
            </div>
          );
        }}
      </Fetched>
    </Panel>
  );
}

function Results({ onBox }: { onBox: (id: number) => void }) {
  const [day, setDay] = useState<number | null>(null);
  const f = useFetch<any>(`/api/results${day != null ? `?day=${day}` : ""}`);
  return (
    <Fetched f={f} what="Loading results">
      {(d) => !d.days.length ? <Empty>No games played yet this season.</Empty> : (
        <Panel title={`Day ${d.day}`} actions={
          <div className="row gap">
            <button className="btn btn-small" disabled={d.days.indexOf(d.day) <= 0} onClick={() => setDay(d.days[d.days.indexOf(d.day) - 1])}>‹ Prev</button>
            <select value={d.day} onChange={(e) => setDay(+e.target.value)} aria-label="Day">
              {d.days.map((x: number) => <option key={x} value={x}>Day {x}</option>)}
            </select>
            <button className="btn btn-small" disabled={d.days.indexOf(d.day) >= d.days.length - 1} onClick={() => setDay(d.days[d.days.indexOf(d.day) + 1])}>Next ›</button>
          </div>}>
          <div className="scoreboard">
            {d.games.map((g: GameView) => (
              <button key={g.id} className="score-card" onClick={() => onBox(g.id)} disabled={!g.has_box}>
                <div className={g.away_score! > g.home_score! ? "winner" : ""}><span>{g.away}</span><b>{g.away_score}</b></div>
                <div className={g.home_score! > g.away_score! ? "winner" : ""}><span>{g.home}</span><b>{g.home_score}</b></div>
                <div className="muted small">{g.ot ? `${g.ot}OT` : "Final"}{g.playoff ? " · PO" : ""}</div>
              </button>
            ))}
          </div>
        </Panel>
      )}
    </Fetched>
  );
}

function Bracket({ onBox }: { onBox: (id: number) => void }) {
  const { state } = useGame();
  const f = useFetch<any>("/api/playoffs");
  const me = state?.team?.id;
  return (
    <Fetched f={f} what="Loading the bracket">
      {(d) => {
        if (!d.series.length) {
          return <Empty>{d.stage === "regular_season" ? "The playoffs start when the regular season ends." : d.stage === "season_over" ? "The bracket is set: start the playoffs from the top bar." : "No playoff bracket this season yet. Past champions are under History."}</Empty>;
        }
        const groups: Record<string, any[]> = {};
        d.series.forEach((s: any) => { const k = s.conference ?? "Finals"; (groups[k] ??= []).push(s); });
        return (
          <div className="bracket">
            {d.champion && <div className="callout">🏆 {state?.teams?.[String(d.champion)]?.name} are the champions!</div>}
            {Object.entries(groups).map(([conf, series]) => {
              const rounds = [...new Set(series.map((s) => s.round))].sort();
              return (
                <Panel key={conf} title={conf === "Finals" ? "Finals" : `${conf}`}>
                  <div className="bracket-rounds">
                    {rounds.map((r) => (
                      <div key={r} className="bracket-round">
                        <div className="muted small">{conf === "Finals" ? "Finals" : `Round ${r}`}</div>
                        {series.filter((s) => s.round === r).map((s) => (
                          <div key={s.id} className={`series ${s.high_id === me || s.low_id === me ? "series-me" : ""}`}>
                            {[["high", s.high_seed, s.high, s.high_wins, s.high_id], ["low", s.low_seed, s.low, s.low_wins, s.low_id]].map(([k, seed, abbr, wins, id]) => (
                              <div key={k as string} className={`series-team ${s.over && s.winner_id === id ? "winner" : s.over ? "loser" : ""}`}>
                                <span className="muted small">{seed}</span> <span>{abbr}</span> <b>{wins}</b>
                              </div>
                            ))}
                            <div className="series-games">
                              {s.games.map((g: GameView, i: number) => (
                                <button key={g.id} className="link small" onClick={() => onBox(g.id)} title={`${g.away} ${g.away_score} @ ${g.home} ${g.home_score}`}>G{i + 1}</button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                </Panel>
              );
            })}
          </div>
        );
      }}
    </Fetched>
  );
}

function History() {
  const f = useFetch<any>("/api/history");
  return (
    <Fetched f={f} what="Loading history">
      {(d) => !d.seasons.length ? <Empty>No completed seasons yet. Champions and awards appear here.</Empty> : (
        <div className="history">
          {d.seasons.map((s: any) => (
            <Panel key={s.season} title={`${s.season}-${String((s.season + 1) % 100).padStart(2, "0")}`}>
              <p><b>{s.champion?.name}</b> beat {s.runner_up?.name} {s.finals} in the Finals.</p>
              <div className="awards">
                {s.awards.map((a: any) => (
                  <div key={a.award}><span className="muted small">{a.award}</span><br />{a.player_id != null ? <PlayerName p={{ id: a.player_id, name: a.name }} /> : "–"}</div>
                ))}
              </div>
              <details>
                <summary className="small">Final standings</summary>
                <ol className="small">{s.standings.map((r: any) => <li key={r.team?.id}>{r.team?.name} {r.w}-{r.l}</li>)}</ol>
              </details>
              {s.deadline_trades.length > 0 && (
                <details><summary className="small">Deadline trades</summary><ul className="small">{s.deadline_trades.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></details>
              )}
            </Panel>
          ))}
        </div>
      )}
    </Fetched>
  );
}

export function League({ params }: { params: URLSearchParams; go: (r: string) => void }) {
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "standings");
  const [box, setBox] = useState<number | null>(null);
  return (
    <div>
      <div className="screen-head">
        <h1>League</h1>
        <Tabs value={tab} onChange={setTab} tabs={[
          { key: "standings", label: "Standings" }, { key: "leaders", label: "Leaders" },
          { key: "schedule", label: "Schedule" }, { key: "results", label: "Scores" },
          { key: "playoffs", label: "Playoffs" }, { key: "history", label: "History" }]} />
      </div>
      {tab === "standings" && <Standings />}
      {tab === "leaders" && <Leaders />}
      {tab === "schedule" && <Schedule onBox={setBox} />}
      {tab === "results" && <Results onBox={setBox} />}
      {tab === "playoffs" && <Bracket onBox={setBox} />}
      {tab === "history" && <History />}
      {box != null && <BoxScoreModal id={box} onClose={() => setBox(null)} />}
    </div>
  );
}
