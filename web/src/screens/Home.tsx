import { useState } from "react";
import { api, GameView, InboxItem } from "../api";
import { useFetch, useGame } from "../context";
import { BoxScoreModal } from "./League";
import { Badge, Empty, Fetched, longDate, Panel, PlayerName, shortDate, Stat, Table } from "../components/ui";

const KIND_BADGE: Record<string, [string, string]> = {
  results: ["neutral", "Results"], injury: ["bad", "Injury"], trade: ["good", "Trade"], offer: ["warn", "Offer"],
  league: ["neutral", "League"], season: ["info", "Season"], playoffs: ["info", "Playoffs"],
  offseason: ["info", "Offseason"], welcome: ["info", "Board"],
};

function OfferItem({ m }: { m: InboxItem }) {
  const { act, busy } = useGame();
  const side = (rows: InboxItem["give"]) => (
    <ul className="plain">
      {(rows ?? []).map((p) => (
        <li key={p.id}><PlayerName p={p} /> <span className="muted small">{p.pos}, {p.age}, CA {p.ca}, ${((p.salary ?? 0) / 1e6).toFixed(1)}M to {p.thru}</span></li>
      ))}
    </ul>
  );
  return (
    <div className="offer">
      <div className="offer-sides">
        <div><div className="muted small">You give</div>{side(m.give)}</div>
        <div><div className="muted small">You get</div>{side(m.get)}</div>
      </div>
      {m.status === "open" ? (
        <div className="row gap">
          <button className="btn btn-primary btn-small" disabled={busy} onClick={() => act(() => api.post(`/api/offers/${m.id}`, { accept: true }), (r: any) => r.message)}>Accept</button>
          <button className="btn btn-small" disabled={busy} onClick={() => act(() => api.post(`/api/offers/${m.id}`, { accept: false }), (r: any) => r.message)}>Decline</button>
          <span className="muted small">Expires when you sim again.</span>
        </div>
      ) : <Badge>{m.status}</Badge>}
    </div>
  );
}

export function Inbox({ items }: { items: InboxItem[] }) {
  const [open, setOpen] = useState<number | null>(items[0]?.id ?? null);
  const [box, setBox] = useState<number | null>(null);
  if (!items.length) return <Empty>No news yet. Sim a day to get the season going.</Empty>;
  return (
    <>
      <ul className="inbox">
        {items.map((m) => {
          const [kind, label] = KIND_BADGE[m.kind] ?? ["neutral", m.kind];
          const isOpen = open === m.id || (m.kind === "offer" && m.status === "open");
          return (
            <li key={m.id} className={`inbox-item ${isOpen ? "inbox-open" : ""} ${m.kind === "offer" && m.status === "open" ? "inbox-action" : ""}`}>
              <button className="inbox-head" onClick={() => setOpen(isOpen ? null : m.id)} aria-expanded={isOpen}>
                <Badge kind={kind}>{label}</Badge>
                <span className="inbox-title">{m.title}</span>
                <span className="muted small">{shortDate(m.date)}</span>
              </button>
              {isOpen && (
                <div className="inbox-body">
                  {m.body.map((line, i) => <p key={i}>{line}</p>)}
                  {m.kind === "offer" && <OfferItem m={m} />}
                  {m.kind === "results" && m.game_ids?.length ? (
                    <div className="row gap wrap">{m.game_ids.slice(-6).map((g) => <button key={g} className="btn btn-small btn-ghost" onClick={() => setBox(g)}>Box score #{g}</button>)}</div>
                  ) : null}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {box != null && <BoxScoreModal id={box} onClose={() => setBox(null)} />}
    </>
  );
}

export function GameList({ games, onBox }: { games: GameView[]; onBox?: (id: number) => void }) {
  if (!games.length) return <Empty>No games.</Empty>;
  return (
    <ul className="games">
      {games.map((g) => (
        <li key={g.id} className="game-row">
          <span className="muted small game-date">{shortDate(g.date)}</span>
          <span>{g.at_home ? "vs" : "@"} <b>{g.opponent}</b></span>
          {g.played ? (
            <>
              <span className={g.won ? "good" : "bad"}><b>{g.won ? "W" : "L"}</b> {g.score}{g.ot ? ` (${g.ot}OT)` : ""}</span>
              {g.has_box && onBox && <button className="link small" onClick={() => onBox(g.id)}>box</button>}
            </>
          ) : <span className="muted small">{g.playoff ? "playoff" : `day ${g.day}`}</span>}
        </li>
      ))}
    </ul>
  );
}

export function Home({ go }: { go: (r: string) => void }) {
  const f = useFetch<any>("/api/dashboard");
  const [box, setBox] = useState<number | null>(null);
  return (
    <Fetched f={f} what="Loading your office">
      {(d) => {
        const s = d.state;
        const [w, l] = s.record;
        return (
          <div className="home">
            <div className="stat-strip">
              <Stat label="Record" value={`${w}-${l}`} sub={`${s.conference_rank}${["th", "st", "nd", "rd"][s.conference_rank] ?? "th"} in the ${s.team.conference}`} />
              <Stat label="Date" value={longDate(s.date)} sub={s.stage === "regular_season" ? `day ${s.day}` : s.stage.replace("_", " ")} />
              <Stat label="Trade deadline" value={s.deadline_in != null ? `${s.deadline_in} days` : "passed"} />
              <Stat label="Payroll" value={`$${(d.finances.payroll / 1e6).toFixed(1)}M`} sub={`cap room $${(Math.max(0, d.finances.cap_room) / 1e6).toFixed(1)}M`} />
              <Stat label="Roster" value={d.finances.roster_size} sub={`${d.injured.length} injured`} />
            </div>
            {s.stage === "offseason" && (
              <div className="callout">
                The offseason is waiting on you. <button className="btn btn-primary btn-small" onClick={() => go("offseason")}>Make your decision</button>
              </div>
            )}
            {s.stage === "awards" && (
              <div className="callout">The season is over. <button className="btn btn-primary btn-small" onClick={() => go("offseason")}>Begin the offseason</button></div>
            )}
            {s.stage === "season_over" && <div className="callout">The regular season is over: start the playoffs from the top bar.</div>}
            <div className="home-grid">
              <Panel title="Inbox" className="span-2">
                <Inbox items={d.inbox} />
              </Panel>
              <div className="stack">
                <Panel title={`${s.team.conference} standings`} actions={<a className="link small" href="#/league">Full table</a>}>
                  <Table dense rows={d.standings} rowKey={(r: any) => r.team_id} rowClass={(r: any) => (r.team_id === s.team.id ? "row-me" : "")}
                    columns={[
                      { key: "rank", label: "#", num: true, desc: false },
                      { key: "abbr", label: "Team", desc: false },
                      { key: "w", label: "W", num: true },
                      { key: "l", label: "L", num: true },
                      { key: "gb", label: "GB", num: true, desc: false, render: (r: any) => (r.gb ? r.gb.toFixed(1) : "–") },
                    ]} />
                </Panel>
                <Panel title="Next games"><GameList games={d.upcoming} /></Panel>
                <Panel title="Recent results"><GameList games={d.recent} onBox={setBox} /></Panel>
                <Panel title="Team leaders">
                  {d.team_leaders.length ? (
                    <ul className="plain">
                      {d.team_leaders.map((t: any) => <li key={t.stat}><span className="muted">{t.stat}</span> <PlayerName p={{ id: t.player_id, name: t.name }} /> <b>{t.value}</b></li>)}
                    </ul>
                  ) : <Empty>No games played yet.</Empty>}
                </Panel>
                {d.injured.length > 0 && (
                  <Panel title="Injuries">
                    <ul className="plain">
                      {d.injured.map((p: any) => <li key={p.id}><PlayerName p={p} /> <span className="muted small">{p.description}, out {p.games} g</span></li>)}
                    </ul>
                  </Panel>
                )}
              </div>
            </div>
            {box != null && <BoxScoreModal id={box} onClose={() => setBox(null)} />}
          </div>
        );
      }}
    </Fetched>
  );
}

