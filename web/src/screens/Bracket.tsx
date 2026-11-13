import { useEffect, useState } from "react";
import { GameView } from "../api";
import { useFetch, useGame } from "../context";
import { Empty, Fetched, Modal, Panel, shortDate } from "../components/ui";

/** One side of a bracket slot: a team, or "TBD" until the feeder series is over. */
interface SlotTeam { id: number | null; abbr: string; name: string; seed: number | null }

/** One series in the bracket (drawn or still to be drawn). Mirrors hoopsim/web/bracket.py. */
export interface Slot {
  id: number | null;
  round: number;
  conference: string | null;
  label: string;
  play_in: string;
  matchup?: string;
  teams: [SlotTeam, SlotTeam];
  wins: [number, number];
  over: boolean;
  winner_id: number | null;
  best_of: number;
  games: GameView[];
  status: string;
  projected: boolean;
}

interface ConfBracket { conference: string; play_in: Slot[]; rounds: Slot[][]; round_labels: string[] }
interface BracketData { projected: boolean; conferences: ConfBracket[]; finals: Slot | null }

function useNarrow(query = "(max-width: 760px)") {
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, [query]);
  return narrow;
}

function SeriesBox({ s, me, onOpen }: { s: Slot; me?: number; onOpen: (s: Slot) => void }) {
  const mine = s.teams.some((t) => t.id != null && t.id === me);
  const tbd = s.teams.every((t) => t.id == null);
  const cls = ["bk-box", mine ? "bk-mine" : "", s.over ? "bk-over" : "", tbd ? "bk-tbd" : "", s.projected ? "bk-projected" : ""].join(" ");
  const body = (
    <>
      {s.teams.map((t, i) => {
        const won = s.over && t.id === s.winner_id;
        const lost = s.over && t.id !== s.winner_id;
        return (
          <div key={i} className={`bk-team ${won ? "bk-win" : ""} ${lost ? "bk-lose" : ""} ${t.id != null && t.id === me ? "bk-me" : ""}`}>
            <span className="bk-seed">{t.seed ?? ""}</span>
            <span className="bk-abbr" title={t.name}>{t.id == null ? <span className="muted">{t.abbr}</span> : t.abbr}</span>
            <span className="bk-name">{t.id == null ? (t.name !== "TBD" ? t.name : "") : t.name}</span>
            <b className="bk-wins">{s.id != null && (s.games.length > 0) ? s.wins[i] : ""}</b>
          </div>
        );
      })}
      <div className="bk-status">{s.status || (s.projected ? "Projected" : " ")}</div>
    </>
  );
  if (s.id == null) return <div className={cls}>{body}</div>;
  return (
    <button type="button" className={cls} onClick={() => onOpen(s)}
      aria-label={`${s.label}: ${s.teams[0].abbr} v ${s.teams[1].abbr}, ${s.status}. Show the games`}>
      {body}
    </button>
  );
}

function SeriesModal({ s, onClose, onBox }: { s: Slot; onClose: () => void; onBox: (id: number) => void }) {
  const title = `${s.conference ? `${s.conference} ` : ""}${s.label}${s.matchup ? ` (${s.matchup})` : ""}: ${s.teams[0].abbr} v ${s.teams[1].abbr}`;
  return (
    <Modal title={title} onClose={onClose}>
      <p><b>{s.status}</b>{s.play_in ? "" : <span className="muted small"> · best of {s.best_of}</span>}</p>
      {!s.games.length ? <Empty>No games played yet.</Empty> : (
        <table className="table table-dense">
          <thead><tr><th>Game</th><th>Date</th><th>Score</th><th></th></tr></thead>
          <tbody>
            {s.games.map((g, i) => (
              <tr key={g.id}>
                <td>{s.play_in ? "–" : `G${i + 1}`}</td>
                <td>{shortDate(g.date)}</td>
                <td>{g.played
                  ? <>{g.away} {g.away_score} @ {g.home} {g.home_score}{g.ot ? ` (${g.ot}OT)` : ""}</>
                  : <span className="muted">{g.away} @ {g.home}</span>}</td>
                <td>
                  <span className="row gap">
                    {g.has_box && <button className="link small" onClick={() => { onClose(); onBox(g.id); }}>box</button>}
                    {g.has_replay && <a className="link small" href={`/api/replay/${g.id}`} target="_blank" rel="noopener">watch</a>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}

function Column({ conf, label, slots, me, onOpen, finals }: { conf?: string; label: string; slots: Slot[]; me?: number; onOpen: (s: Slot) => void; finals?: boolean }) {
  return (
    <div className={`bk-col ${finals ? "bk-col-finals" : ""}`}>
      <div className="bk-col-head"><span className="bk-col-conf">{conf ?? "\u00a0"}</span>{label.replace("Conference ", "Conf. ")}</div>
      <div className="bk-col-body">
        {slots.map((s, i) => <SeriesBox key={s.id ?? `t${i}`} s={s} me={me} onOpen={onOpen} />)}
      </div>
    </div>
  );
}

function Tree({ b, me, onOpen }: { b: BracketData; me?: number; onOpen: (s: Slot) => void }) {
  const [east, west] = b.conferences;
  return (
    <div className="bk-scroll" role="region" aria-label="Playoff bracket" tabIndex={0}>
      <div className="bk-tree">
        {east.rounds.map((col, i) => <Column key={`e${i}`} conf={east.conference} label={east.round_labels[i]} slots={col} me={me} onOpen={onOpen} />)}
        {b.finals && <Column label="Finals" slots={[b.finals]} me={me} onOpen={onOpen} finals />}
        {west.rounds.map((col, i) => ({ col, i })).reverse().map(({ col, i }) => (
          <Column key={`w${i}`} conf={west.conference} label={west.round_labels[i]} slots={col} me={me} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}

function Stacked({ b, me, onOpen }: { b: BracketData; me?: number; onOpen: (s: Slot) => void }) {
  const rounds = Math.max(...b.conferences.map((c) => c.rounds.length));
  return (
    <div className="bk-stack">
      {Array.from({ length: rounds }, (_, i) => (
        <section key={i} className="bk-stage">
          <h3>{b.conferences[0].round_labels[i]}</h3>
          <div className="bk-stage-confs">
            {b.conferences.filter((c) => c.rounds[i]).map((c) => (
              <div key={c.conference} className="bk-stage-conf">
                <div className="bk-col-head">{c.conference}</div>
                {c.rounds[i].map((s, j) => <SeriesBox key={s.id ?? `t${j}`} s={s} me={me} onOpen={onOpen} />)}
              </div>
            ))}
          </div>
        </section>
      ))}
      {b.finals && (
        <section className="bk-stage">
          <h3>Finals</h3>
          <div className="bk-finals-one"><SeriesBox s={b.finals} me={me} onOpen={onOpen} /></div>
        </section>
      )}
    </div>
  );
}

export function Bracket({ onBox }: { onBox: (id: number) => void }) {
  const { state } = useGame();
  const f = useFetch<any>("/api/playoffs");
  const narrow = useNarrow();
  const [open, setOpen] = useState<Slot | null>(null);
  return (
    <Fetched f={f} what="Loading the bracket">
      {(d) => {
        const b: BracketData | null = d.bracket;
        const me: number | undefined = d.user_team_id ?? state?.team?.id;
        if (!b) {
          return <Empty>{d.stage === "regular_season" ? "The playoff picture appears once games have been played." : "No playoff bracket this season yet. Past champions are under History."}</Empty>;
        }
        const playIn = b.conferences.filter((c) => c.play_in.length);
        const tree = !narrow && b.conferences.length === 2;
        return (
          <div className="bracket">
            {d.champion != null && <div className="callout">🏆 {state?.teams?.[String(d.champion)]?.name} are the champions!</div>}
            {b.projected && (
              <div className="callout callout-info">
                {d.stage === "season_over"
                  ? "The regular season is over and the seeds are set: start the playoffs from the top bar."
                  : "Projected bracket: if the season ended today. Seeds follow the current standings and tiebreakers."}
              </div>
            )}
            {playIn.length > 0 && (
              <Panel title="Play-in" className="bk-playin">
                <p className="muted small">Seeds 7–10 of each conference. The 7 v 8 winner is the 7 seed; its loser meets the 9 v 10 winner for the 8 seed.</p>
                <div className="bk-playin-grid">
                  {playIn.map((c) => (
                    <div key={c.conference} className="bk-stage-conf">
                      <div className="bk-col-head">{c.conference}</div>
                      {c.play_in.map((s, i) => (
                        <div key={s.id ?? `p${i}`} className="bk-playin-item">
                          <div className="muted small">{s.matchup}</div>
                          <SeriesBox s={s} me={me} onOpen={setOpen} />
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </Panel>
            )}
            <Panel title={b.projected ? "Playoffs (projected)" : "Playoffs"}>
              {tree ? <Tree b={b} me={me} onOpen={setOpen} /> : <Stacked b={b} me={me} onOpen={setOpen} />}
            </Panel>
            {open && <SeriesModal s={open} onClose={() => setOpen(null)} onBox={onBox} />}
          </div>
        );
      }}
    </Fetched>
  );
}
