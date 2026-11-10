import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, GameState } from "./api";

export interface Toast {
  id: number;
  text: string;
  kind: "ok" | "error" | "info";
}

interface Ctx {
  state: GameState | null;
  version: number;
  /** Re-read the game state and make every screen re-fetch its data. */
  changed: () => Promise<void>;
  openPlayer: (id: number) => void;
  playerId: number | null;
  closePlayer: () => void;
  toast: (text: string, kind?: Toast["kind"]) => void;
  /** Run an API call: on error, toast it. Returns the result or undefined. */
  act: <T>(fn: () => Promise<T>, success?: string | ((r: T) => string | undefined)) => Promise<T | undefined>;
  busy: boolean;
}

const GameContext = createContext<Ctx | null>(null);

export function useGame(): Ctx {
  const c = useContext(GameContext);
  if (!c) throw new Error("no game context");
  return c;
}

export function GameProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GameState | null>(null);
  const [version, setVersion] = useState(0);
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [busy, setBusy] = useState(false);
  const nextToast = useRef(1);

  const toast = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = nextToast.current++;
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);

  const changed = useCallback(async () => {
    try {
      setState(await api.get<GameState>("/api/state"));
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setVersion((v) => v + 1);
  }, [toast]);

  const act = useCallback(
    async <T,>(fn: () => Promise<T>, success?: string | ((r: T) => string | undefined)) => {
      setBusy(true);
      try {
        const r = await fn();
        const msg = typeof success === "function" ? success(r) : success;
        if (msg) toast(msg, "ok");
        await changed();
        return r;
      } catch (e) {
        toast(e instanceof ApiError ? e.message : String(e), "error");
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [changed, toast],
  );

  useEffect(() => {
    changed();
  }, [changed]);

  return (
    <GameContext.Provider
      value={{
        state,
        version,
        changed,
        openPlayer: setPlayerId,
        playerId,
        closePlayer: () => setPlayerId(null),
        toast,
        act,
        busy,
      }}
    >
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
            {t.text}
          </div>
        ))}
      </div>
    </GameContext.Provider>
  );
}

/** GET ``url`` and re-fetch whenever the game changes. ``url`` null = skip. */
export function useFetch<T = any>(url: string | null) {
  const { version } = useGame();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!url) return;
    let live = true;
    setLoading(true);
    api
      .get<T>(url)
      .then((d) => {
        if (live) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => live && setError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [url, version, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

/** Hash routing: #/squad -> "squad". */
export function useRoute(): [string, (r: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "") || "home";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [route, (r: string) => (window.location.hash = `/${r}`)];
}
