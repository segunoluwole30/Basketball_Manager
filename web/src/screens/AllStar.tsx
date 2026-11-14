import { useState } from "react";
import { useFetch } from "../context";
import { Badge, Empty, Fetched, Panel, PlayerName } from "../components/ui";

type Row = { id: number; name: string; pos: string; team: string; mine: boolean; injured: boolean;
  ppg: number; rpg: number; apg: number; tpm: number; tpa: number; votes?: number };
type BoxRow = { id: number; name: string; team: string; min: number; pts: number; reb: number; ast: number;
  stl: number; blk: number; fg: string; tp: string };
type Exhibition = { home: string; away: string; home_abbr: string; away_abbr: string; score: [number, number];
  ot: number; box: { home: BoxRow[]; away: BoxRow[] }; mvp: number; replay_id: number | null };
type Round = { racks: number[][]; deep: number[]; money_rack: number; points: number };

const fmtDate = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

/** All-Star Weekend: voting, the rosters, Rising Stars, the Three-Point Contest and the game. */
export function AllStar() {
  const f = useFetch<any>("/api/allstar");
  return (
    <Fetched f={f} what="Loading All-Star Weekend">
      {(d) => !d.available ? (
        <>
          <Empty>No All-Star Weekend this season (the season is too short for an All-Star break).</Empty>
          <Past past={d.past} />
        </>
      ) : (
        <div className="allstar">
          <div className="as-timeline" aria-label="All-Star calendar">
            {d.schedule.map((e: any) => (
              <span key={e.key} className={`as-step ${e.done ? "done" : ""}`}>
                <span className="as-dot" aria-hidden>{e.done ? "✓" : "•"}</span>
                <b>{fmtDate(e.date)}</b> {e.label}
              </span>
            ))}
          </div>
          {d.game && <GameCard title="All-Star Game" g={d.game} />}
          {d.three?.final && <ThreePoint c={d.three} />}
          {d.rising?.result && <GameCard title="Rising Stars" g={d.rising.result} />}
          {(d.east || d.west) && (
            <div className="as-cols">
              {(["east", "west"] as const).map((c) => d[c] && (
                <Panel key={c} title={`${c === "east" ? "East" : "West"} All-Stars`}>
                  <Roster rows={d[c].starters} label="Starters" more={{ label: "Reserves", rows: d[c].reserves }} />
                  {d[c].replaced.length > 0 && (
                    <p className="muted small">Injury replacements: {d[c].replaced.map((r: any) => `${r.in} for ${r.out}`).join("; ")}</p>
                  )}
                </Panel>
              ))}
            </div>
          )}
          {d.votes && !d.east && <Votes votes={d.votes} />}
          {!d.votes && <Empty>Fan voting opens about six weeks before the All-Star Game ({fmtDate(d.game_date)}).</Empty>}
          {d.rising && !d.rising.result && (
            <Panel title="Rising Stars">
              <div className="as-cols">
                {d.rising.teams.map((t: any) => <Roster key={t.name} rows={t.players} label={t.name} />)}
              </div>
            </Panel>
          )}
          {d.three && !d.three.final && (
            <Panel title="Three-Point Contest field"><Roster rows={d.three.field} label="Shooters" threes /></Panel>
          )}
          {d.votes && d.east && (
            <details className="as-details"><summary>Final fan vote</summary><Votes votes={d.votes} /></details>
          )}
          <Past past={d.past} />
        </div>
      )}
    </Fetched>
  );
}

function Roster({ rows, label, threes, more }: { rows: Row[]; label: string; threes?: boolean; more?: { label: string; rows: Row[] } }) {
  const line = (r: Row) => (
    <tr key={r.id} className={r.mine ? "row-mine" : ""}>
      <td><PlayerName p={r} /> {r.injured && <Badge kind="bad">inj</Badge>}</td>
      <td>{r.team}</td><td>{r.pos}</td>
      {threes ? <><td className="num">{r.tpm}</td><td className="num">{r.tpa ? (r.tpm / r.tpa).toFixed(3).replace(/^0/, "") : "–"}</td></>
        : <><td className="num">{r.ppg}</td><td className="num">{r.rpg}</td><td className="num">{r.apg}</td></>}
    </tr>
  );
  return (
    <div className="table-wrap">
      <table className="table table-dense">
        <thead><tr><th>{label}</th><th>Team</th><th>Pos</th>
          {threes ? <><th className="num">3PM</th><th className="num">3P%</th></> : <><th className="num">PPG</th><th className="num">RPG</th><th className="num">APG</th></>}
        </tr></thead>
        <tbody>
          {rows.map(line)}
          {more && <tr className="as-sub"><th colSpan={6}>{more.label}</th></tr>}
          {more?.rows.map(line)}
        </tbody>
      </table>
    </div>
  );
}

function Votes({ votes }: { votes: Record<string, Row[]> }) {
  return (
    <Panel title="Fan voting">
      <p className="muted small">Starters are picked half on the fan vote and half on the players' and media vote: two guards and three frontcourt players per conference.</p>
      <div className="as-cols as-cols-4">
        {Object.entries(votes).map(([group, rows]) => (
          <div key={group}>
            <h4>{group}</h4>
            <ol className="as-votes">
              {rows.map((r) => (
                <li key={r.id} className={r.mine ? "row-mine" : ""}><PlayerName p={r} /> <span className="muted small">{r.team}</span>
                  <span className="as-num">{r.votes?.toLocaleString()}</span></li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function GameCard({ title, g }: { title: string; g: Exhibition }) {
  const [open, setOpen] = useState(false);
  const mvp = [...g.box.home, ...g.box.away].find((r) => r.id === g.mvp);
  const homeWon = g.score[0] > g.score[1];
  return (
    <Panel title={title} actions={g.replay_id ? <a className="btn btn-small" href={`/api/replay/${g.replay_id}`} target="_blank" rel="noopener">Watch</a> : undefined}>
      <div className="as-score">
        <span className={homeWon ? "" : "win"}>{g.away} <b>{g.score[1]}</b></span>
        <span className="muted">@</span>
        <span className={homeWon ? "win" : ""}>{g.home} <b>{g.score[0]}</b>{g.ot ? " (OT)" : ""}</span>
      </div>
      {mvp && <p>MVP: <PlayerName p={mvp} /> ({mvp.team}): {mvp.pts} pts, {mvp.reb} reb, {mvp.ast} ast, {mvp.tp} from three.</p>}
      <button className="link small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} box score</button>
      {open && (
        <div className="as-cols">
          {(["away", "home"] as const).map((side) => (
            <div key={side} className="table-wrap">
              <table className="table table-dense">
                <thead><tr><th>{side === "home" ? g.home : g.away}</th><th className="num">MIN</th><th className="num">PTS</th>
                  <th className="num">REB</th><th className="num">AST</th><th className="num">STL</th><th className="num">BLK</th>
                  <th className="num">FG</th><th className="num">3P</th></tr></thead>
                <tbody>
                  {g.box[side].map((r) => (
                    <tr key={r.id}><td><PlayerName p={r} /> <span className="muted small">{r.team}</span></td>
                      <td className="num">{r.min}</td><td className="num">{r.pts}</td><td className="num">{r.reb}</td>
                      <td className="num">{r.ast}</td><td className="num">{r.stl}</td><td className="num">{r.blk}</td>
                      <td className="num">{r.fg}</td><td className="num">{r.tp}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Racks({ r }: { r: Round }) {
  // Racks 1-2, deep ball, rack 3, deep ball, racks 4-5 (as shot).
  const parts: React.ReactNode[] = [];
  r.racks.forEach((rack, i) => {
    parts.push(
      <span key={`r${i}`} className={`as-rack ${i === r.money_rack ? "money" : ""}`} title={i === r.money_rack ? "Money-ball rack: every ball worth 2" : `Rack ${i + 1}`}>
        {rack.map((v, j) => <i key={j} className={`${v ? "make" : "miss"} ${(i === r.money_rack || j === 4) ? "mb" : ""}`} />)}
      </span>);
    if (i === 1 || i === 2) {
      const v = r.deep[i - 1];
      parts.push(<span key={`d${i}`} className={`as-deep ${v ? "make" : "miss"}`} title="Deep shot: 3 points">★</span>);
    }
  });
  return <span className="as-racks">{parts}</span>;
}

function ThreePoint({ c }: { c: any }) {
  const names: Record<string, Row> = Object.fromEntries(c.field.map((r: Row) => [String(r.id), r]));
  const table = (title: string, rounds: Record<string, Round>) => {
    const ids = Object.keys(rounds).sort((a, b) => rounds[b].points - rounds[a].points);
    return (
      <>
        <h4>{title}</h4>
        <div className="table-wrap">
          <table className="table table-dense">
            <tbody>
              {ids.map((id) => (
                <tr key={id} className={names[id]?.mine ? "row-mine" : ""}>
                  <td>{names[id] ? <PlayerName p={names[id]} /> : id} <span className="muted small">{names[id]?.team}</span></td>
                  <td><Racks r={rounds[id]} /></td>
                  <td className="num"><b>{rounds[id].points}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  };
  const winner = names[String(c.winner)];
  return (
    <Panel title="Three-Point Contest" actions={c.watch_url ? <a className="btn btn-small" href={c.watch_url} target="_blank" rel="noopener">Watch</a> : undefined}>
      {winner && <p className="as-champ">🏆 <PlayerName p={winner} /> ({winner.team}) wins with {c.final[String(c.winner)].points} in the final.</p>}
      {table("Final", c.final)}
      {table("First round (top three advance)", c.first)}
      {c.shootoffs.length > 0 && <p className="muted small">Ties settled by shoot-off ({c.shootoffs.map((s: any) => s.stage).join(", ")}).</p>}
      <p className="muted small">Five racks round the arc, corner to corner: each rack's last ball is a 2-point money ball, one rack (the shooter's pick) is all money balls, and the two ★ deep shots between the wings and the top are worth 3. 70 seconds; 40 points is perfect.</p>
    </Panel>
  );
}

function Past({ past }: { past: any[] }) {
  if (!past?.length) return null;
  return (
    <Panel title="Past All-Star Weekends">
      <table className="table table-dense">
        <thead><tr><th>Season</th><th>Game</th><th>MVP</th><th>3-Point champion</th><th>Rising Stars MVP</th></tr></thead>
        <tbody>
          {past.map((p) => (
            <tr key={p.season}>
              <td>{p.season}-{String((p.season + 1) % 100).padStart(2, "0")}</td>
              <td>{Object.entries(p.score).map(([k, v]) => `${k.replace(" All-Stars", "")} ${v}`).join(", ")}</td>
              <td><PlayerName p={{ id: p.mvp, name: p.mvp_name }} /></td>
              <td>{p.three_point_champion != null ? <PlayerName p={{ id: p.three_point_champion, name: p.three_point_champion_name }} /> : "–"}
                {p.three_point_watch_url && <> <a className="link small" href={p.three_point_watch_url} target="_blank" rel="noopener">watch</a></>}</td>
              <td>{p.rising_stars_mvp != null ? <PlayerName p={{ id: p.rising_stars_mvp, name: p.rising_stars_mvp_name }} /> : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
