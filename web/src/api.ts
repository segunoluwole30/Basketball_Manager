// Thin client for the hoopsim JSON API (hoopsim/web/app.py).

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the game server. Is `python -m hoopsim web` still running?", 0);
  }
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

export const api = {
  get: <T = any>(url: string) => request<T>("GET", url),
  post: <T = any>(url: string, body: unknown = {}) => request<T>("POST", url, body),
};

// ---------------------------------------------------------------------------
// Shapes returned by the API (only the fields the UI uses)
// ---------------------------------------------------------------------------
export interface Potential {
  peaked: boolean;
  est?: number;
  lo?: number;
  hi?: number;
}

export interface PlayerRow {
  id: number;
  name: string;
  jersey: number | null;
  pos: string;
  secondary: string[];
  age: number;
  height: string;
  height_cm: number;
  ca: number;
  potential: Potential;
  team_id: number | null;
  team: string | null;
  salary: number | null;
  thru: number | null;
  option: string | null;
  injury: { description: string; games: number } | null;
  morale: number;
  morale_word: string;
  three: number;
  fin: number;
  mid: number;
  pas: number;
  hnd: number;
  def: number;
  reb: number;
  ath: number;
  iq: number;
  gp: number;
  mpg: number;
  ppg: number;
  rpg: number;
  apg: number;
  starter?: boolean;
  minutes_target?: number;
  ask?: number;
  ask_years?: number;
  [key: string]: any;
}

export interface TeamRef {
  id: number;
  abbr: string;
  name: string;
  conference: string;
  city?: string;
  nickname?: string;
  reputation?: number;
}

export interface GameState {
  loaded: boolean;
  league?: string;
  season?: number;
  season_label?: string;
  match_engine?: "statistical" | "spatial" | "hybrid";
  live_game?: number | null;
  date?: string;
  phase?: "regular_season" | "playoffs" | "offseason";
  stage?: "regular_season" | "season_over" | "playoffs" | "awards" | "offseason";
  user_team_id?: number | null;
  team?: TeamRef;
  teams?: Record<string, TeamRef>;
  record?: [number, number];
  conference_rank?: number;
  save_path?: string;
  unsaved?: boolean;
  day?: number;
  /** the date the league is at (the last day done) and the day Next day plays */
  today_date?: string;
  next_date?: string;
  next_day_games?: number;
  next_day_game?: GameView | null;
  next_day_mine?: boolean;
  deadline_in?: number | null;
  season_over?: boolean;
  open_offers?: number;
  unread?: number;
  offseason_step?: string | null;
  offseason_kind?: string | null;
}

export interface GameView {
  id: number;
  day: number;
  date: string;
  playoff: boolean;
  home_id: number;
  home: string;
  away_id: number;
  away: string;
  played: boolean;
  has_box: boolean;
  has_replay?: boolean;
  home_score?: number;
  away_score?: number;
  ot?: number;
  opponent?: string;
  opponent_id?: number;
  at_home?: boolean;
  won?: boolean;
  score?: string;
}

export interface InboxItem {
  id: number;
  date: string;
  kind: string;
  title: string;
  body: string[];
  read: boolean;
  status?: string;
  give?: PlayerRow[];
  get?: PlayerRow[];
  offer?: { team_id: number; you_give: number[]; you_get: number[] };
  player_id?: number;
  game_ids?: number[];
  replay_id?: number;        // an All-Star exhibition's recording
  watch_url?: string;        // something to watch in the viewer (the Three-Point Contest)
}
