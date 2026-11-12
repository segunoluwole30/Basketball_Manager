"""FastAPI app: a JSON API over ``GameSession`` plus the built React front end.

    python -m hoopsim web [--load save.db] [--port 8000]

Needs the optional web dependencies: ``pip install -r requirements-web.txt``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .session import GameSession, UserError

STATIC = Path(__file__).parent / "static"


class NewGame(BaseModel):
    seed: Optional[int] = None
    num_teams: int = 12
    match_engine: str = "statistical"
    league_file: Optional[str] = None     # a file in the leagues folder (GET /api/new/files)


class TeamChoice(BaseModel):
    team_id: int


class PathBody(BaseModel):
    path: Optional[str] = None


class Starters(BaseModel):
    ids: list[int]


class Minutes(BaseModel):
    minutes: dict[str, int]


class Toggle(BaseModel):
    on: bool


class Signing(BaseModel):
    player_id: int
    salary: int
    years: int


class PlayerRef(BaseModel):
    player_id: int


class TradeBody(BaseModel):
    give: list[int] = []
    team_id: int
    get: list[int] = []


class SimBody(BaseModel):
    mode: str = "day"
    days: Optional[int] = None


class PlayoffSim(BaseModel):
    mode: str = "day"


class Answer(BaseModel):
    answer: Any = None


class AutoBody(BaseModel):
    scope: str = "one"


class OfferAnswer(BaseModel):
    accept: bool


class ReadBody(BaseModel):
    ids: Optional[list[int]] = None


def create_app(session: Optional[GameSession] = None) -> FastAPI:
    game = session or GameSession()
    app = FastAPI(title="hoopsim", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.game = game

    @app.exception_handler(UserError)
    async def user_error(request: Request, exc: UserError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    def run(fn, *args, **kwargs):
        with game.lock:
            return fn(*args, **kwargs)

    # -- game -----------------------------------------------------------------
    @app.get("/api/state")
    def state():
        return run(game.state)

    @app.post("/api/new")
    def new_game(body: NewGame):
        return run(game.new_game, body.seed, body.num_teams, body.match_engine, body.league_file)

    @app.get("/api/new/files")
    def new_files():
        return run(game.league_files)

    @app.get("/api/new/teams")
    def team_choices():
        return run(game.team_choices)

    @app.post("/api/new/team")
    def choose_team(body: TeamChoice):
        return run(game.choose, body.team_id)

    @app.post("/api/load")
    def load(body: PathBody):
        return run(game.load, body.path or "")

    @app.post("/api/save")
    def save(body: PathBody = Body(default=PathBody())):
        return run(game.save, body.path)

    @app.get("/api/saves")
    def saves():
        return run(game.saves)

    # -- home -------------------------------------------------------------------
    @app.get("/api/dashboard")
    def dashboard():
        return run(game.dashboard)

    @app.get("/api/inbox")
    def inbox():
        return run(game.inbox)

    @app.post("/api/inbox/read")
    def inbox_read(body: ReadBody = Body(default=ReadBody())):
        return run(game.mark_read, body.ids)

    @app.post("/api/offers/{item_id}")
    def answer_offer(item_id: int, body: OfferAnswer):
        return run(game.answer_offer, item_id, body.accept)

    # -- squad --------------------------------------------------------------------
    @app.get("/api/roster")
    def roster(team_id: Optional[int] = None):
        return run(game.roster, team_id)

    @app.get("/api/teams")
    def teams():
        return run(game.team_list)

    @app.get("/api/editor/players")
    def editor_players(team_id: Optional[int] = None, q: str = "", free_agents: bool = False):
        return run(game.editor_players, team_id, q, free_agents)

    @app.get("/api/editor/player/{pid}")
    def editor_player(pid: int):
        return run(game.editor_player, pid)

    @app.post("/api/editor/player/{pid}")
    def editor_edit_player(pid: int, body: dict = Body(...)):
        return run(game.editor_edit_player, pid, body)

    @app.post("/api/editor/team/{tid}")
    def editor_edit_team(tid: int, body: dict = Body(...)):
        return run(game.editor_edit_team, tid, body)

    @app.get("/api/player/{pid}")
    def player(pid: int):
        return run(game.player_card, pid)

    @app.post("/api/player/{pid}/instructions")
    def instructions(pid: int, body: dict[str, int]):
        return run(game.set_instructions, pid, body)

    @app.get("/api/finances")
    def finances():
        return run(game.finances)

    # -- tactics ------------------------------------------------------------------
    @app.get("/api/lineup")
    def lineup():
        return run(game.lineup)

    @app.post("/api/lineup/starters")
    def starters(body: Starters):
        return run(game.set_starters, body.ids)

    @app.post("/api/lineup/minutes")
    def minutes(body: Minutes):
        return run(game.set_minutes, body.minutes)

    @app.post("/api/lineup/auto")
    def auto_lineup(body: Toggle):
        return run(game.set_auto, body.on)

    @app.get("/api/tactics")
    def tactics():
        return run(game.tactics)

    @app.post("/api/tactics")
    def set_tactics(body: dict[str, Any]):
        return run(game.set_tactics, body)

    # -- transfers ----------------------------------------------------------------
    @app.get("/api/free_agents")
    def free_agents():
        return run(game.free_agents)

    @app.post("/api/sign")
    def sign(body: Signing):
        return run(game.sign, body.player_id, body.salary, body.years)

    @app.post("/api/release")
    def release(body: PlayerRef):
        return run(game.release, body.player_id)

    @app.post("/api/trade/check")
    def trade_check(body: TradeBody):
        return run(game.trade_check, body.give, body.team_id, body.get)

    @app.post("/api/trade")
    def trade(body: TradeBody):
        return run(game.trade, body.give, body.team_id, body.get)

    # -- league ---------------------------------------------------------------------
    @app.get("/api/standings")
    def standings():
        return run(game.standings)

    @app.get("/api/leaders")
    def leaders():
        return run(game.leaders)

    @app.get("/api/schedule")
    def schedule(team_id: Optional[int] = None):
        return run(game.schedule, team_id)

    @app.get("/api/results")
    def results(day: Optional[int] = None):
        return run(game.results, day)

    @app.get("/api/game/{game_id}")
    def box_score(game_id: int):
        return run(game.box_score, game_id)

    @app.get("/api/allstar")
    def all_star():
        return run(game.all_star)

    @app.get("/api/replay/{game_id}", response_class=HTMLResponse)
    def replay(game_id: int, live: bool = False):
        return HTMLResponse(run(game.replay_page, game_id, live))

    @app.post("/api/watch-next")
    def watch_next():
        return run(game.watch_next)

    # -- live match: coach your game as it's played ---------------------------------
    @app.middleware("http")
    async def live_guard(request: Request, call_next):
        # While a live game is on, the rest of the game waits (no sims, deals or saves).
        if (request.method == "POST" and game.live is not None
                and not request.url.path.startswith("/api/live")):
            return JSONResponse({"detail": "finish your live game first"}, status_code=409)
        return await call_next(request)

    @app.post("/api/live/start")
    def live_start():
        return run(game.live_start)

    @app.get("/api/live/page", response_class=HTMLResponse)
    def live_page():
        return HTMLResponse(run(game.live_page))

    @app.get("/api/live/next")
    def live_next(after: int = 0, want: int = 2):
        return run(game.live_next, after, want)

    @app.post("/api/live/command")
    def live_command(body: dict = Body(...)):
        return run(game.live_command, body)

    @app.post("/api/live/finish")
    def live_finish():
        return run(game.live_finish)

    @app.get("/api/history")
    def history():
        return run(game.history)

    @app.get("/api/playoffs")
    def playoffs():
        return run(game.playoffs)

    # -- simulation -------------------------------------------------------------------
    @app.post("/api/sim")
    def sim(body: SimBody):
        return run(game.sim, body.mode, body.days)

    @app.post("/api/playoffs/start")
    def start_playoffs():
        return run(game.start_playoffs)

    @app.post("/api/playoffs/sim")
    def sim_playoffs(body: PlayoffSim):
        return run(game.sim_playoffs, body.mode)

    # -- offseason ---------------------------------------------------------------------
    @app.get("/api/offseason")
    def offseason():
        return run(game.offseason)

    @app.post("/api/offseason/start")
    def offseason_start():
        return run(game.start_offseason)

    @app.post("/api/offseason/answer")
    def offseason_answer(body: Answer):
        return run(game.answer, body.answer)

    @app.post("/api/offseason/auto")
    def offseason_auto(body: AutoBody = Body(default=AutoBody())):
        return run(game.auto, body.scope)

    # -- stats pages (read-only) --------------------------------------------------------
    # -- calendar ----------------------------------------------------------------------
    from . import calendar as cal

    @app.get("/api/calendar")
    def calendar_view(team_id: Optional[int] = None):
        return run(cal.calendar, game, team_id)

    @app.get("/api/calendar/day/{day}")
    def calendar_day(day: int):
        return run(cal.calendar_day, game, day)

    @app.post("/api/sim/to")
    def sim_to(body: dict = Body(...)):
        return run(cal.sim_to, game, body.get("day"))

    @app.get("/api/stats/leaders")
    def stats_leaders(playoffs: bool = False, pos: Optional[str] = None,
                      season: Optional[int] = None, limit: int = 10):
        return run(game.stats_leaders, playoffs, pos, season, limit)

    @app.get("/api/stats/teams")
    def stats_teams(playoffs: bool = False, season: Optional[int] = None):
        return run(game.stats_teams, playoffs, season)

    @app.get("/api/players/{pid}/history")
    def player_history(pid: int):
        return run(game.player_history, pid)

    @app.get("/api/team/gamelog")
    def team_gamelog(team_id: Optional[int] = None):
        return run(game.team_gamelog, team_id)

    # -- front end ---------------------------------------------------------------------
    @app.get("/api/{rest:path}")
    def api_404(rest: str):
        return JSONResponse(status_code=404, content={"detail": f"no endpoint /api/{rest}"})

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets", check_dir=False), name="assets")

        @app.get("/{rest:path}")
        def index(rest: str):
            target = STATIC / rest
            if rest and target.is_file() and STATIC in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(STATIC / "index.html")
    else:
        @app.get("/")
        def no_frontend():
            return JSONResponse({"detail": "the front end isn't built: see web/README or the API at /api/docs"})

    return app


def serve(load: Optional[str] = None, port: int = 8000, host: str = "127.0.0.1",
          open_browser: bool = True, save: Optional[str] = None, leagues: Optional[str] = None) -> None:
    import threading
    import webbrowser

    import uvicorn

    session = GameSession(save_path=save or load or "hoopsim_save.db", leagues_dir=leagues)
    if load:
        session.load(load)
    app = create_app(session)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"hoopsim is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
