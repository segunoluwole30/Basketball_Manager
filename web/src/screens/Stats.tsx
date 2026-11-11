import { useState } from "react";
import { useFetch, useGame } from "../context";
import { Column, Empty, Fetched, Panel, PlayerName, pct, shortDate, Stat, Table, Tabs } from "../components/ui";
import { BoxScoreModal } from "./League";
import { TeamPicker } from "./Squad";

type Tab = "leaders" | "teams" | "gamelog";

/** Format a leader / table value by kind: percentages as .xxx, shares as xx.x%. */
export function fmtStat(v: number | null | undefined, kind: "pct" | "share" | "num" = "num") {
  if (v == null) return "–";
  if (kind === "share") return `${(v * 100).toFixed(1)}%`;
  if (kind === "pct") return pct(v);
  return v.toFixed(1);
}

const SHARE_KEYS = new Set(["usg", "fga_share"]);

function Seg<T extends string | boolean>({ value, options, onChange, label }: {
  value: T; options: { key: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.key)} className={`seg-btn ${o.key === value ? "seg-on" : ""}`} aria-pressed={o.key === value}
          onClick={() => onChange(o.key)}>{o.label}</button>
      ))}
    </span>
  );
}

function SeasonPicker({ seasons, value, onChange }: { seasons: number[]; value: number | null; onChange: (s: number) => void }) {
  if (!seasons || seasons.length < 2) return null;
  return (
    <select value={value ?? seasons[0]} onChange={(e) => onChange(+e.target.value)} aria-label="Season">
      {seasons.map((s) => <option key={s} value={s}>{s}-{String((s + 1) % 100).padStart(2, "0")}</option>)}
    </select>
  );
}

function Leaders() {
  const [playoffs, setPlayoffs] = useState(false);
  const [pos, setPos] = useState("");
  const [season, setSeason] = useState<number | null>(null);
  const q = new URLSearchParams({ playoffs: String(playoffs), limit: "10" });
  if (pos) q.set("pos", pos);
  if (season != null) q.set("season", String(season));
  const f = useFetch<any>(`/api/stats/leaders?${q}`);
  return (
    <>
      <div className="filters" aria-label="Filters">
        <SeasonPicker seasons={f.data?.seasons} value={season} onChange={setSeason} />
        <Seg label="Season type" value={playoffs} onChange={setPlayoffs}
          options={[{ key: false, label: "Regular season" }, { key: true, label: "Playoffs" }]} />
        <select value={pos} onChange={(e) => setPos(e.target.value)} aria-label="Position">
          <option value="">All positions</option>
          {(f.data?.positions ?? ["PG", "SG", "SF", "PF", "C"]).map((p: string) => <option key={p} value={p}>{p}</option>)}
        </select>
        <span className="muted small"><span className="key-me" aria-hidden /> your players</span>
      </div>
      <Fetched f={f} what="Loading leaders">
        {(d) => {
          const any = d.categories.some((c: any) => c.rows.length);
          if (!any) return <Empty>{playoffs ? "No playoff games with box scores yet." : "No qualified players yet: sim a few days."}</Empty>;
          const group = (adv: boolean) => (
            <div className="leader-grid">
              {d.categories.filter((c: any) => c.advanced === adv).map((c: any) => (
                <Panel key={c.key} title={c.label}>
                  {c.rows.length ? (
                    <ol className="leaders">
                      {c.rows.map((r: any) => (
                        <li key={r.player_id} className={r.mine ? "leader-me" : ""}>
                          <PlayerName p={{ id: r.player_id, name: r.name }} /> <span className="muted small">{r.team} · {r.pos}</span>
                          <b>{fmtStat(r.value, c.kind === "pct" ? (SHARE_KEYS.has(c.key) ? "share" : "pct") : "num")}</b>
                        </li>
                      ))}
                    </ol>
                  ) : <Empty>{SHARE_KEYS.has(c.key) && !d.from_box_scores ? "Needs box scores (current season only)." : "Nobody qualifies yet."}</Empty>}
                  <div className="muted small leader-q">Min. {c.qualifier}</div>
                </Panel>
              ))}
            </div>
          );
          return (
            <>
              <p className="muted small">Per game. To qualify: {d.min_games}+ games{playoffs ? " (one series' worth)" : " (60% of the most any team has played)"}; shooting and rate stats need the volume shown under each list.</p>
              {group(false)}
              <h3>Advanced</h3>
              <p className="muted small">
                TS% = PTS / (2 × (FGA + 0.44 × FTA)). Usage % = share of his team's plays (FGA + 0.44 × FTA + TOV) he used while on the floor.
                Share of team FGA = his FGA over his team's FGA in the games he played. Per 36 = per 36 minutes played.
              </p>
              {group(true)}
            </>
          );
        }}
      </Fetched>
    </>
  );
}

type TeamCol = [key: string, label: string, title: string, kind: string, low?: boolean];
const TEAM_VIEWS: Record<"ratings" | "offence" | "defence", TeamCol[]> = {
  ratings: [
    ["ppg", "PTS", "Points per game", "num"], ["opp_ppg", "OPP", "Opponent points per game", "num", true],
    ["diff", "DIFF", "Point differential per game", "signed"], ["pace", "PACE", "Possessions per 48 minutes", "num"],
    ["ortg", "ORTG", "Offensive rating: points per 100 possessions", "num"],
    ["drtg", "DRTG", "Defensive rating: opponent points per 100 possessions", "num", true],
    ["net", "NET", "Net rating: ORTG − DRTG", "signed"],
  ],
  offence: [
    ["efg", "eFG%", "Effective FG%: (FGM + 0.5 × 3PM) / FGA", "pct"],
    ["tov_pct", "TOV%", "Turnovers per play: TOV / (FGA + 0.44 × FTA + TOV)", "share", true],
    ["orb_pct", "ORB%", "Offensive rebounds / (ORB + opponent DRB)", "share"],
    ["ft_rate", "FTr", "Free-throw rate: FTA / FGA", "pct"],
    ["tpa_rate", "3PAr", "Three-point attempt rate: 3PA / FGA", "pct"],
  ],
  defence: [
    ["opp_efg", "Opp eFG%", "Opponents' effective FG%", "pct", true],
    ["opp_tov_pct", "Opp TOV%", "Turnovers forced per opponent play", "share"],
    ["drb_pct", "DRB%", "Defensive rebounds / (DRB + opponent ORB)", "share"],
    ["opp_ft_rate", "Opp FTr", "Opponents' FTA / FGA", "pct", true],
  ],
};

function fmtTeam(v: number | null, kind: string) {
  if (v == null) return "–";
  if (kind === "signed") return v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1);
  return fmtStat(v, kind as any);
}

function Teams() {
  const [playoffs, setPlayoffs] = useState(false);
  const [view, setView] = useState<keyof typeof TEAM_VIEWS>("ratings");
  const f = useFetch<any>(`/api/stats/teams?playoffs=${playoffs}`);
  const columns: Column<any>[] = [
    { key: "name", label: "Team", sort: (r) => r.name, desc: false,
      render: (r) => <span title={r.name}><span className="hide-phone">{r.name}</span><span className="show-phone">{r.abbr}</span></span> },
    { key: "gp", label: "GP", num: true },
    { key: "w", label: "W-L", num: true, sort: (r) => r.w / Math.max(1, r.gp), render: (r) => `${r.w}-${r.l}` },
    ...TEAM_VIEWS[view].map(([key, label, title, kind, low]): Column<any> => ({
      key, label, title: `${title}${low ? " (lower is better)" : ""}`, num: true, desc: !low,
      render: (r) => fmtTeam(r[key], kind),
    })),
  ];
  return (
    <>
      <div className="filters">
        <Seg label="Season type" value={playoffs} onChange={setPlayoffs}
          options={[{ key: false, label: "Regular season" }, { key: true, label: "Playoffs" }]} />
        <Tabs value={view} onChange={setView} tabs={[
          { key: "ratings", label: "Ratings" }, { key: "offence", label: "Offence" }, { key: "defence", label: "Defence" }]} />
      </div>
      <Fetched f={f} what="Loading team stats">
        {(d) => !d.teams.length ? <Empty>{d.note ?? "No games yet."}</Empty> : (
          <Panel title={`Team stats · ${playoffs ? "playoffs" : "regular season"}`}>
            <Table dense rows={d.teams} rowKey={(r: any) => r.team_id} columns={columns} initialSort={view === "ratings" ? "net" : undefined}
              rowClass={(r: any) => (r.mine ? "row-me" : "")} />
            {d.league && (
              <p className="muted small totals-line">
                League average: {d.league.ppg.toFixed(1)} points, pace {d.league.pace?.toFixed(1)}, rating {d.league.ortg?.toFixed(1)},
                eFG% {pct(d.league.efg ?? 0)}, TOV% {fmtStat(d.league.tov_pct, "share")}, ORB% {fmtStat(d.league.orb_pct, "share")}.
              </p>
            )}
            <p className="muted small">
              From box scores. Possessions per team-game ≈ FGA + 0.44 × FTA − ORB + TOV, averaged over both teams so a game's teams share one count.
              Hover a column heading for its formula; click it to sort.
            </p>
          </Panel>
        )}
      </Fetched>
    </>
  );
}

function GameLog() {
  const { state } = useGame();
  const [team, setTeam] = useState<number>(state?.team?.id ?? 0);
  const [box, setBox] = useState<number | null>(null);
  const f = useFetch<any>(`/api/team/gamelog?team_id=${team}`);
  return (
    <>
      <div className="filters"><TeamPicker value={team} onChange={setTeam} /></div>
      <Fetched f={f} what="Loading game log">
        {(d) => {
          const reg = d.games.filter((g: any) => !g.playoff);
          const home = reg.filter((g: any) => g.at_home), away = reg.filter((g: any) => !g.at_home);
          const wl = (gs: any[]) => `${gs.filter((g) => g.won).length}-${gs.filter((g) => !g.won).length}`;
          return (
            <>
              <div className="stat-strip">
                <Stat label="Record" value={`${d.record[0]}-${d.record[1]}`} sub={`${d.remaining} games left`} />
                <Stat label="Home / away" value={`${wl(home)} / ${wl(away)}`} />
                <Stat label="Points" value={reg.length ? (d.points_for / reg.length).toFixed(1) : "–"}
                  sub={reg.length ? `allowed ${(d.points_against / reg.length).toFixed(1)}` : undefined} />
                <Stat label="Last 10" value={reg.length ? wl(reg.slice(-10)) : "–"} />
                {d.playoff_record[0] + d.playoff_record[1] > 0 && <Stat label="Playoffs" value={`${d.playoff_record[0]}-${d.playoff_record[1]}`} />}
              </div>
              <Panel title={`${d.name} · ${d.season}-${String((d.season + 1) % 100).padStart(2, "0")} game log`}>
                <Table dense rows={d.games} rowKey={(g: any) => g.id} empty="No games played yet this season."
                  onRowClick={(g: any) => g.has_box && setBox(g.id)}
                  columns={[
                    { key: "date", label: "Date", sort: (g: any) => (g.playoff ? 1e6 : 0) + g.day * 1000 + g.id, desc: false,
                      render: (g: any) => <>{shortDate(g.date)}{g.playoff ? <span className="muted small"> PO</span> : null}</> },
                    { key: "opponent", label: "Opp", desc: false, render: (g: any) => <span title={g.opponent_name}><span className="muted">{g.at_home ? "vs" : "@"}</span> {g.opponent}</span> },
                    { key: "won", label: "Res", sort: (g: any) => (g.won ? 1 : 0), render: (g: any) => <b className={g.won ? "good" : "bad"}>{g.won ? "W" : "L"}</b> },
                    { key: "score", label: "Score", sort: (g: any) => g.margin, num: true,
                      render: (g: any) => `${g.team_score}-${g.opp_score}${g.ot ? ` (${g.ot}OT)` : ""}` },
                    { key: "record", label: "Rec", sort: (g: any) => (g.playoff ? 1e6 : 0) + g.day * 1000 + g.id, num: true, desc: false },
                    { key: "top", label: "Top scorer", sort: (g: any) => g.top_scorer?.pts,
                      render: (g: any) => g.top_scorer ? <><PlayerName p={{ id: g.top_scorer.player_id, name: g.top_scorer.name }} /> <span className="muted small">{g.top_scorer.pts} pts, {g.top_scorer.trb} reb, {g.top_scorer.ast} ast</span></> : <span className="muted">–</span> },
                    { key: "links", label: "", render: (g: any) => (
                      <span className="row gap">
                        {g.has_box && <button className="link small" onClick={(e) => { e.stopPropagation(); setBox(g.id); }}>box</button>}
                        {g.has_replay && <a className="link small" href={`/api/replay/${g.id}`} target="_blank" rel="noopener" onClick={(e) => e.stopPropagation()}>watch</a>}
                      </span>
                    ) },
                  ]} />
                <p className="muted small">Click a game for its box score. Recordings ("watch") are kept for your team's most recent games on the spatial engine.</p>
              </Panel>
            </>
          );
        }}
      </Fetched>
      {box != null && <BoxScoreModal id={box} onClose={() => setBox(null)} />}
    </>
  );
}

export function Stats({ params }: { params: URLSearchParams }) {
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "leaders");
  return (
    <div className="stats">
      <div className="screen-head">
        <h1>Stats</h1>
        <Tabs value={tab} onChange={setTab} tabs={[
          { key: "leaders", label: "League leaders" }, { key: "teams", label: "Team stats" }, { key: "gamelog", label: "Game log" }]} />
      </div>
      {tab === "leaders" && <Leaders />}
      {tab === "teams" && <Teams />}
      {tab === "gamelog" && <GameLog />}
    </div>
  );
}
