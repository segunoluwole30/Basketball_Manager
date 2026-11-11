import { useEffect, useMemo, useState } from "react";
import { api, GameView } from "../api";
import { useFetch, useGame } from "../context";
import { Empty, Fetched, Panel, Stat } from "../components/ui";
import { BoxScoreModal } from "./League";
import { TeamPicker } from "./Squad";

interface CalDay {
  day: number;
  date: string;
  games: number;
  played: boolean;
  playoff: boolean;
  game: GameView | null;
  b2b: boolean;
  marks: string[];
}

interface CalData {
  team_id: number;
  today: number | null;
  today_date?: string;
  phase: string;
  days: CalDay[];
  summary?: { games: number; home: number; back_to_backs: number; longest_road_trip: number };
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const monthKey = (iso: string) => iso.slice(0, 7);
const monthName = (key: string) =>
  new Date(key + "-01T12:00:00").toLocaleDateString(undefined, { month: "long", year: "numeric" });

/** The season, a month at a time: your games, back-to-backs and the NBA's key dates. */
export function Calendar() {
  const { state } = useGame();
  const [team, setTeam] = useState<number | null>(null);
  const f = useFetch<CalData>(`/api/calendar${team ? `?team_id=${team}` : ""}`);
  return (
    <div className="screen">
      <Fetched f={f} what="Loading the calendar">
        {(d) => <CalendarBody data={d} team={team ?? d.team_id} setTeam={setTeam} me={state?.user_team_id ?? null} />}
      </Fetched>
    </div>
  );
}

function CalendarBody({ data, team, setTeam, me }: { data: CalData; team: number; setTeam: (t: number) => void; me: number | null }) {
  const months = useMemo(() => [...new Set(data.days.map((d) => monthKey(d.date)))], [data.days]);
  const todayMonth = data.today_date && months.includes(monthKey(data.today_date)) ? monthKey(data.today_date) : months[0];
  const [month, setMonth] = useState<string | undefined>(todayMonth);
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => { if (!month || !months.includes(month)) setMonth(todayMonth); }, [months, todayMonth, month]);
  if (!data.days.length || !month) return <Empty>No schedule yet.</Empty>;
  const mi = months.indexOf(month);
  const byDate = new Map(data.days.map((d) => [d.date, d]));
  // Weeks of the month, Sunday first.
  const first = new Date(month + "-01T12:00:00");
  const start = new Date(first);
  start.setDate(1 - first.getDay());
  const cells: { iso: string; inMonth: boolean }[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    cells.push({ iso, inMonth: monthKey(iso) === month });
    if (i % 7 === 6 && d.getMonth() !== first.getMonth() && i >= 28) break;
  }
  const s = data.summary;
  return (
    <>
      <div className="stat-strip">
        {s && <Stat label="Games" value={s.games} sub={`${s.home} home · ${s.games - s.home} away`} />}
        {s && <Stat label="Back-to-backs" value={s.back_to_backs} sub="NBA teams: about 14-16" />}
        {s && <Stat label="Longest road trip" value={`${s.longest_road_trip} games`} />}
        <Stat label="Today" value={data.today_date ? new Date(data.today_date + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—"} />
      </div>
      <div className="cal-layout">
        <Panel
          title={monthName(month)}
          actions={
            <span className="row gap">
              <TeamPicker value={team} onChange={setTeam} />
              <button className="btn btn-small" disabled={mi <= 0} onClick={() => setMonth(months[mi - 1])} aria-label="Previous month">‹</button>
              <button className="btn btn-small" onClick={() => setMonth(todayMonth)}>Today</button>
              <button className="btn btn-small" disabled={mi >= months.length - 1} onClick={() => setMonth(months[mi + 1])} aria-label="Next month">›</button>
            </span>
          }
        >
          <div className="cal-grid" role="grid" aria-label={`${monthName(month)} schedule`}>
            {WEEKDAYS.map((w) => <div key={w} className="cal-head" role="columnheader">{w}</div>)}
            {cells.map(({ iso, inMonth }) => {
              const d = byDate.get(iso);
              const g = d?.game;
              const isToday = d && data.today === d.day;
              const cls = ["cal-cell", inMonth ? "" : "cal-out", d && data.today !== null && d.day <= data.today ? "cal-past" : "",
                isToday ? "cal-today" : "", d && selected === d.day ? "cal-sel" : "", d?.marks.some((m) => m.includes("no games") || m.includes("break")) ? "cal-off" : ""].join(" ");
              return (
                <button key={iso} className={cls} role="gridcell" disabled={!d}
                  onClick={() => d && setSelected(d.day)} aria-label={`${iso}${g ? `, ${g.at_home ? "vs" : "at"} ${g.opponent}` : ""}`}>
                  <span className="cal-num">{+iso.slice(8)}</span>
                  {d?.marks.map((m) => <span key={m} className="cal-mark">{m}</span>)}
                  {g && (
                    <span className={`cal-game ${g.at_home ? "cal-home" : "cal-away"} ${g.played ? (g.won ? "cal-win" : "cal-loss") : ""}`}>
                      {g.at_home ? "vs" : "@"} {g.opponent}
                      {g.played && <b> {g.won ? "W" : "L"} {g.score}</b>}
                      {d?.b2b && <span className="cal-b2b" title="Second night of a back-to-back">B2B</span>}
                      {g.playoff && <span className="cal-b2b" title="Playoff game">PO</span>}
                    </span>
                  )}
                  {d && d.games > 0 && <span className="cal-count">{d.games} game{d.games > 1 ? "s" : ""}</span>}
                </button>
              );
            })}
          </div>
          <p className="muted small">
            Your games: <span className="cal-key cal-home">home</span> <span className="cal-key cal-away">away</span>{" "}
            <span className="cal-key cal-b2b">B2B</span> second night of a back-to-back. Click a day for every game on it.
          </p>
        </Panel>
        {selected !== null && <DayPanel day={selected} data={data} me={me} onClose={() => setSelected(null)} />}
      </div>
    </>
  );
}

function DayPanel({ day, data, me, onClose }: { day: number; data: CalData; me: number | null; onClose: () => void }) {
  const { act, busy } = useGame();
  const f = useFetch<{ day: number; date: string; marks: string[]; games: GameView[] }>(`/api/calendar/day/${day}`);
  const [box, setBox] = useState<number | null>(null);
  const future = data.today === null || day > data.today;
  const canSim = future && (data.phase === "regular_season" || data.phase === "playoffs") && data.days.some((d) => d.day === day && d.games > 0);
  return (
    <Panel title={new Date(data.days.find((d) => d.day === day)!.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
      actions={<button className="btn btn-small btn-ghost" onClick={onClose} aria-label="Close">×</button>}>
      <Fetched f={f} what="Loading the day">
        {(d) => (
          <>
            {d.marks.map((m) => <p key={m} className="cal-mark-big">{m}</p>)}
            {!d.games.length && <Empty>No games.</Empty>}
            <ul className="games">
              {d.games.map((g) => {
                const mine = me !== null && (g.home_id === me || g.away_id === me);
                return (
                  <li key={g.id} className={`game-row ${mine ? "game-mine" : ""}`}>
                    <span>{g.away} {g.played ? g.away_score : ""} @ {g.home} {g.played ? g.home_score : ""}{g.ot ? " (OT)" : ""}</span>
                    <span className="row gap">
                      {g.has_box && <button className="link small" onClick={() => setBox(g.id)}>box</button>}
                      {g.has_replay && <a className="link small" href={`/api/replay/${g.id}`} target="_blank" rel="noopener">watch</a>}
                    </span>
                  </li>
                );
              })}
            </ul>
            {canSim && (
              <button className="btn btn-primary" disabled={busy}
                onClick={() => act(() => api.post("/api/sim/to", { day }), (r: any) =>
                  r.stopped_for === "offers" ? "A trade offer arrived first: check your inbox." : "Played to that day")}>
                Sim to this day
              </button>
            )}
          </>
        )}
      </Fetched>
      {box !== null && <BoxScoreModal id={box} onClose={() => setBox(null)} />}
    </Panel>
  );
}
