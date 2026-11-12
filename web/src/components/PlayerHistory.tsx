import { useState } from "react";
import { useFetch } from "../context";
import { SeasonLine } from "./Charts";
import { Fetched, pct, Table, Tabs } from "./ui";

const f3 = (v: number | null) => (v == null ? "–" : pct(v));
const f1 = (v: number | null) => (v == null ? "–" : v.toFixed(1));

/** Season-by-season career table and small charts (player card). */
export function PlayerHistory({ id }: { id: number }) {
  const f = useFetch<any>(`/api/players/${id}/history`);
  const [kind, setKind] = useState<"regular" | "playoffs">("regular");
  const data = f.data && f.data.player_id === id ? f.data : null;
  return (
    <Fetched f={{ ...f, data }} what="Loading career">
      {(h) => {
        const rows = h.seasons.filter((r: any) => r.playoffs === (kind === "playoffs"));
        const hasPlayoffs = h.seasons.some((r: any) => r.playoffs);
        const chart = h.chart as any[];
        const stats = chart.filter((c) => c.ppg != null);
        const pt = (key: string) => chart.map((c) => ({ x: c.season, y: c[key], partial: c.in_progress }));
        return (
          <div className="history-block">
            <h3>Career</h3>
            {stats.length > 0 || chart.length > 1 ? (
              <>
                <div className="chart-row">
                  {stats.length > 0 && <SeasonLine title="Points per game" points={pt("ppg")} />}
                  {stats.length > 0 && <SeasonLine title="Rebounds per game" points={pt("rpg")} />}
                  {stats.length > 0 && <SeasonLine title="Assists per game" points={pt("apg")} />}
                  <SeasonLine title="Current ability" points={pt("ca")} zero={false} format={(v) => v.toFixed(0)} />
                </div>
                <p className="muted small">
                  Regular season. Hollow points are the season in progress. Ability: {h.ability_source}.
                </p>
              </>
            ) : null}
            {h.seasons.length === 0 ? (
              <p className="muted small">No games played yet.</p>
            ) : (
              <>
                {hasPlayoffs && (
                  <Tabs value={kind} onChange={setKind} tabs={[{ key: "regular", label: "Regular season" }, { key: "playoffs", label: "Playoffs" }]} />
                )}
                <Table dense rows={rows.map((r: any) => ({ ...r, key: `${r.season}-${r.playoffs}` }))} rowKey={(r) => r.key}
                  empty="No playoff games." rowClass={(r) => (r.in_progress ? "row-progress" : "")}
                  columns={[
                    { key: "season", label: "Season", num: true, desc: false,
                      render: (r) => <>{r.season}-{String((r.season + 1) % 100).padStart(2, "0")}{r.in_progress ? <span className="muted small"> so far</span> : null}</> },
                    { key: "team", label: "Team" },
                    { key: "age", label: "Age", num: true, render: (r) => r.age ?? "–" },
                    { key: "ca", label: "CA", num: true, title: "Current ability at the end of that season (now, for the season in progress)", render: (r) => r.ca ?? "–" },
                    { key: "gp", label: "GP", num: true },
                    { key: "gs", label: "GS", num: true },
                    { key: "mpg", label: "MPG", num: true, render: (r) => f1(r.mpg) },
                    { key: "pts", label: "PTS", num: true, render: (r) => f1(r.pts) },
                    { key: "trb", label: "REB", num: true, render: (r) => f1(r.trb) },
                    { key: "ast", label: "AST", num: true, render: (r) => f1(r.ast) },
                    { key: "stl", label: "STL", num: true, render: (r) => f1(r.stl) },
                    { key: "blk", label: "BLK", num: true, render: (r) => f1(r.blk) },
                    { key: "fg_pct", label: "FG%", num: true, render: (r) => f3(r.fg_pct) },
                    { key: "honours", label: "Honours", sort: (r) => (r.honours ?? []).length,
                      render: (r) => (r.honours ?? []).length ? <span className="small" title={r.honours.join(", ")}>{(r.honours_short ?? r.honours).join(",")}</span> : "" },
                    { key: "tp_pct", label: "3P%", num: true, render: (r) => f3(r.tp_pct) },
                    { key: "ft_pct", label: "FT%", num: true, render: (r) => f3(r.ft_pct) },
                    { key: "ts", label: "TS%", title: "True shooting: PTS / (2 × (FGA + 0.44 × FTA))", num: true, render: (r) => f3(r.ts) },
                  ]} />
              </>
            )}
          </div>
        );
      }}
    </Fetched>
  );
}
