import { useState } from "react";
import { useFetch } from "../context";
import { Empty, Fetched, longDate, Panel, PlayerName } from "../components/ui";

// The season's awards: announced when the regular season ends (FM-style awards night),
// and kept for every finished season.

interface AwardRow {
  id: number; name: string; pos: string; team: string; team_id: number | null; mine: boolean;
  gp?: number; mpg?: number; ppg?: number; rpg?: number; apg?: number; spg?: number; bpg?: number;
  share?: number; first?: number; points?: number;
}

const seasonLabel = (s: number) => `${s}-${String((s + 1) % 100).padStart(2, "0")}`;

function statLine(key: string, r: AwardRow) {
  if (r.ppg == null) return null;
  return key === "dpoy" || key === "all_defense"
    ? `${r.spg} stl · ${r.bpg} blk · ${r.rpg} reb`
    : `${r.ppg} pts · ${r.rpg} reb · ${r.apg} ast`;
}

function SingleAward({ a }: { a: any }) {
  const [w, ...rest] = a.finalists as AwardRow[];
  return (
    <section className={`award-card ${w?.mine ? "award-mine" : ""}`} aria-label={a.name}>
      <div className="award-name"><span className="award-short">{a.short}</span> {a.name}</div>
      {!w ? <div className="muted small">Not awarded: nobody qualified.</div> : (
        <>
          <div className="award-winner">
            <span className="award-trophy" aria-hidden>🏆</span>
            <div>
              <PlayerName p={w} /> {w.mine && <span className="badge badge-good">Yours</span>}
              <div className="muted small">{w.pos} · {w.team}{statLine(a.key, w) ? ` · ${statLine(a.key, w)}` : ""}</div>
            </div>
          </div>
          {w.share != null && (
            <ol className="finalists" aria-label="Finalists">
              {[w, ...rest].map((r) => (
                <li key={r.id} className={r.mine ? "finalist-mine" : ""}>
                  <span className="finalist-name"><PlayerName p={r} /> <span className="muted small">{r.team}</span></span>
                  <span className="vote-bar" title={`${Math.round((r.share ?? 0) * 100)}% of the maximum vote${r.first ? `, ${r.first} first-place votes` : ""}`}>
                    <span className="vote-fill" style={{ width: `${Math.max(2, Math.round((r.share ?? 0) * 100))}%` }} />
                  </span>
                  <b className="vote-pct">{Math.round((r.share ?? 0) * 100)}%</b>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}

function TeamTier({ k, tier }: { k: string; tier: any }) {
  return (
    <div className="award-tier">
      <h3>{tier.name}</h3>
      <ul className="tier-list">
        {tier.players.map((r: AwardRow) => (
          <li key={r.id} className={r.mine ? "row-me" : ""}>
            <span><PlayerName p={r} /> <span className="muted small">{r.pos} · {r.team}</span></span>
            <span className="muted small">{statLine(k, r)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AwardsView({ initialSeason, compact }: { initialSeason?: number; compact?: boolean }) {
  const [season, setSeason] = useState<number | undefined>(initialSeason);
  const f = useFetch<any>(`/api/awards${season != null ? `?season=${season}` : ""}`);
  return (
    <Fetched f={f} what="Loading the awards">
      {(d) => !d.available ? <Empty>{d.message}</Empty> : (
        <div className="awards-view">
          <div className="row gap wrap awards-bar">
            {!compact && (
              <label className="row gap">
                <span className="muted small">Season</span>
                <select value={d.season} onChange={(e) => setSeason(+e.target.value)} aria-label="Season">
                  {d.seasons.map((s: number) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                </select>
              </label>
            )}
            <span className="muted small">
              {d.date ? `Announced ${longDate(d.date)}, at the end of the regular season. ` : ""}
              {d.rules ? `MVP, DPOY, MIP, All-NBA and All-Defensive need ${d.rules.required} of ${d.rules.games} games of ${d.rules.min_minutes}+ minutes.` : ""}
              {!d.full ? "Winners only: this season was played before the full vote was kept." : ""}
            </span>
          </div>
          <div className="award-grid">
            {d.single.map((a: any) => <SingleAward key={a.key} a={a} />)}
            {d.finals_mvp && (
              <section className={`award-card ${d.finals_mvp.mine ? "award-mine" : ""}`} aria-label="Finals MVP">
                <div className="award-name"><span className="award-short">FMVP</span> Finals MVP</div>
                <div className="award-winner">
                  <span className="award-trophy" aria-hidden>🏆</span>
                  <div><PlayerName p={d.finals_mvp} /><div className="muted small">{d.finals_mvp.team}</div></div>
                </div>
              </section>
            )}
            {d.current && !d.finals_mvp && (
              <section className="award-card award-pending" aria-label="Finals MVP">
                <div className="award-name"><span className="award-short">FMVP</span> Finals MVP</div>
                <div className="muted small">Named after the Finals.</div>
              </section>
            )}
          </div>
          {d.teams.filter((t: any) => t.tiers.length).map((t: any) => (
            <Panel key={t.key} title={`${t.name} Teams`}>
              <div className="tier-grid">
                {t.tiers.map((tier: any) => <TeamTier key={tier.name} k={t.key} tier={tier} />)}
              </div>
            </Panel>
          ))}
          <p className="muted small"><span className="key-me" /> your players. Votes: 100 media members; MVP ballots 10-7-5-3-1, other awards 5-3-1; the bar is the share of the most points possible.</p>
        </div>
      )}
    </Fetched>
  );
}
