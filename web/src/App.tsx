import { useState } from "react";
import { api } from "./api";
import { PlayerCard } from "./components/PlayerCard";
import { Loading, Modal } from "./components/ui";
import { useGame, useRoute } from "./context";
import { Home } from "./screens/Home";
import { League } from "./screens/League";
import { Offseason } from "./screens/Offseason";
import { Squad } from "./screens/Squad";
import { Start, SaveLoad } from "./screens/Start";
import { Tactics } from "./screens/Tactics";
import { Transfers } from "./screens/Transfers";

const NAV = [
  { key: "home", label: "Home", icon: "⌂" },
  { key: "squad", label: "Squad", icon: "☰" },
  { key: "tactics", label: "Tactics", icon: "◎" },
  { key: "transfers", label: "Transfers", icon: "⇄" },
  { key: "league", label: "League", icon: "▤" },
  { key: "offseason", label: "Offseason", icon: "☀" },
];

const STAGE_LABEL: Record<string, string> = {
  regular_season: "Regular season",
  season_over: "Regular season over",
  playoffs: "Playoffs",
  awards: "Season complete",
  offseason: "Offseason",
};

function SimControls({ go }: { go: (r: string) => void }) {
  const { state, act, busy } = useGame();
  if (!state?.stage) return null;
  const sim = (mode: string) =>
    act(() => api.post("/api/sim", { mode }), (r: any) =>
      r.stopped_for === "offers" ? "A trade offer arrived: check your inbox." : r.games.length ? `Played ${r.days} day${r.days > 1 ? "s" : ""}` : undefined);
  const po = (mode: string) => act(() => api.post("/api/playoffs/sim", { mode }), (r: any) => (r.finished ? "The season is over!" : undefined));
  switch (state.stage) {
    case "regular_season":
      return (
        <div className="sim-controls" aria-label="Simulation">
          <button className="btn btn-primary" disabled={busy} onClick={() => sim("day")}>Next day</button>
          <button className="btn" disabled={busy} onClick={() => sim("week")}>Week</button>
          {state.deadline_in != null && <button className="btn" disabled={busy} onClick={() => sim("deadline")} title="Play to the trade deadline">To deadline</button>}
          <button className="btn" disabled={busy} onClick={() => sim("end")} title="Play the rest of the regular season">To end</button>
        </div>
      );
    case "season_over":
      return (
        <div className="sim-controls">
          <button className="btn btn-primary" disabled={busy} onClick={async () => { if (await act(() => api.post("/api/playoffs/start"), "The playoffs are under way")) go("league?tab=playoffs"); }}>
            Start the playoffs
          </button>
        </div>
      );
    case "playoffs":
      return (
        <div className="sim-controls">
          <button className="btn btn-primary" disabled={busy} onClick={() => po("day")}>Next day</button>
          <button className="btn" disabled={busy} onClick={() => po("round")}>Round</button>
          <button className="btn" disabled={busy} onClick={() => po("all")}>All playoffs</button>
        </div>
      );
    case "awards":
      return (
        <div className="sim-controls">
          <button className="btn btn-primary" disabled={busy} onClick={() => go("offseason")}>Go to the offseason</button>
        </div>
      );
    case "offseason":
      return (
        <div className="sim-controls">
          <button className="btn btn-primary" onClick={() => go("offseason")}>Offseason: your decision</button>
        </div>
      );
  }
  return null;
}

export default function App() {
  const { state, busy } = useGame();
  const [route, go] = useRoute();
  const [menu, setMenu] = useState<null | "save" | "load" | "new">(null);
  const [navOpen, setNavOpen] = useState(false);
  const [page, query] = route.split("?");
  const params = new URLSearchParams(query ?? "");

  if (state === null) return <div className="boot"><Loading what="Connecting to the game" /></div>;
  if (!state.loaded || !state.team || menu === "new") return <Start onDone={() => { setMenu(null); go("home"); }} onCancel={state.team ? () => setMenu(null) : undefined} />;

  let screen: React.ReactNode;
  switch (page) {
    case "squad": screen = <Squad />; break;
    case "tactics": screen = <Tactics />; break;
    case "transfers": screen = <Transfers params={params} />; break;
    case "league": screen = <League params={params} go={go} />; break;
    case "offseason": screen = <Offseason go={go} />; break;
    default: screen = <Home go={go} />;
  }
  const [w, l] = state.record ?? [0, 0];
  return (
    <div className="app">
      <aside className={`sidebar ${navOpen ? "sidebar-open" : ""}`}>
        <div className="brand"><span className="ball" aria-hidden>●</span> hoopsim <span className="muted">GM</span></div>
        <nav>
          {NAV.map((n) => (
            <a key={n.key} href={`#/${n.key}`} className={`nav-item ${page === n.key || (page === "" && n.key === "home") ? "nav-on" : ""}`}
              onClick={() => setNavOpen(false)}>
              <span className="nav-icon" aria-hidden>{n.icon}</span>{n.label}
              {n.key === "home" && state.open_offers ? <span className="nav-count">{state.open_offers}</span> : null}
              {n.key === "offseason" && state.stage === "offseason" ? <span className="nav-count">!</span> : null}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button className="btn btn-small btn-ghost" onClick={() => setMenu("save")}>Save{state.unsaved ? " •" : ""}</button>
          <button className="btn btn-small btn-ghost" onClick={() => setMenu("load")}>Load</button>
          <button className="btn btn-small btn-ghost" onClick={() => setMenu("new")}>New game</button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="icon-btn nav-toggle" aria-label="Menu" onClick={() => setNavOpen(!navOpen)}>☰</button>
          <div className="team-id">
            <div className="team-name">{state.team.name}</div>
            <div className="muted small">
              {w}-{l} · {ordinal(state.conference_rank ?? 0)} in the {state.team.conference} · {state.season_label} · {STAGE_LABEL[state.stage ?? ""]}
              {state.deadline_in != null && state.stage === "regular_season" ? ` · deadline in ${state.deadline_in} days` : ""}
            </div>
          </div>
          <div className="topbar-date">{state.date && new Date(state.date + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</div>
          <SimControls go={go} />
          {busy && <span className="spinner" aria-label="Working" />}
        </header>
        <main className="content">{screen}</main>
      </div>
      <PlayerCard />
      {(menu === "save" || menu === "load") && (
        <Modal title={menu === "save" ? "Save game" : "Load game"} onClose={() => setMenu(null)}>
          <SaveLoad mode={menu} onDone={() => setMenu(null)} />
        </Modal>
      )}
    </div>
  );
}

export function ordinal(n: number) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
