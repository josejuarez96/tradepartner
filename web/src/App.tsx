import { useEffect, useState } from "react";
import { sample } from "@/lib/data";
import type { AppData } from "@/lib/types";
import { scenario } from "@/lib/scenarios";
import { lab, strategy } from "@/lib/lab";
import { Shell, type Screen } from "@/components/Shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Today } from "@/screens/Today";
import { Strategies } from "@/screens/Strategies";
import { StrategyPage } from "@/screens/Strategy";
import { OverrideGapScreen, RegisterScreen, SpendHoldoutScreen } from "@/screens/StrategyActions";
import { BookLoading, Books } from "@/screens/Book";
import { TrialBench } from "@/screens/TrialBench";
import { LoadError, NoBooks, OverviewLoading } from "@/screens/OverviewStates";

type Load = { kind: "loading" } | { kind: "error"; retrying: boolean } | { kind: "ready"; data: AppData };

const state = new URLSearchParams(location.search).get("state");

/** `#today`, `#strategies[/<id>[/<tab or action>]]`, `#books/<id>`. */
interface Route { screen: Screen; id?: string; sub?: string }
function readRoute(): Route {
  const [a, b, c] = location.hash.replace("#", "").split("/");
  if (a === "strategies") return { screen: "strategies", id: b, sub: c };
  if (a === "books") return { screen: "books", id: b };
  return { screen: "today" };
}
function useRoute(): Route {
  const [r, setR] = useState<Route>(readRoute);
  useEffect(() => {
    const on = () => { const n = readRoute(); setR((p) => { if (p.screen !== n.screen || p.id !== n.id) scrollTo(0, 0); return n; }); };
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return r;
}

const ACTIONS = new Set(["register", "spend-holdout", "override-gap", "bench", "run"]);

/** Prototype loader: reads the sample files; ?state= previews loading, error, empty, alert, calm, stopped, safety and stopfail. */
export function App() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const route = useRoute();

  useEffect(() => {
    if (state === "loading") return;
    if (state === "error") { setLoad({ kind: "error", retrying: false }); return; }
    const t = setTimeout(() => setLoad({ kind: "ready", data: scenario(sample, state) }), 200);
    return () => clearTimeout(t);
  }, []);

  const data = load.kind === "ready" ? load.data : null;
  const s = route.id ? strategy(route.id) : undefined;
  const action = route.sub && ACTIONS.has(route.sub) ? route.sub : undefined;
  const tab = action ? undefined : route.sub;
  const replayId = s?.id === "h1-momentum-12-1" ? "h1" : null;

  return (
    <TooltipProvider>
      <Shell screen={route.screen} mode={data?.account_mode ?? "paper"} sample={data?.sample ?? true} counts={{ today: lab.waiting.length }}>
        {load.kind === "loading" && (route.screen === "books" ? <BookLoading /> : <OverviewLoading />)}
        {load.kind === "error" && (
          <LoadError
            retrying={load.retrying}
            onRetry={() => { setLoad({ kind: "error", retrying: true }); setTimeout(() => setLoad({ kind: "error", retrying: false }), 1200); }}
          />
        )}
        {data && route.screen === "today" && (data.books.length ? <Today data={data} /> : <NoBooks />)}
        {data && route.screen === "strategies" && !route.id && <Strategies />}
        {data && route.screen === "strategies" && route.id && (
          s && action === "register" ? <RegisterScreen s={s} />
          : s && action === "spend-holdout" ? <SpendHoldoutScreen s={s} />
          : s && action === "override-gap" ? <OverrideGapScreen s={s} />
          : s && action === "bench" && replayId ? <TrialBench data={data} ideaId={replayId} back={`#strategies/${s.id}/result`} />
          : <StrategyPage data={data} id={route.id} tab={tab} action={action} />
        )}
        {data && route.screen === "books" && <Books data={data} />}
      </Shell>
    </TooltipProvider>
  );
}
