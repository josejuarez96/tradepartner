import { useEffect, useState } from "react";
import { sample } from "@/lib/data";
import type { AppData } from "@/lib/types";
import { scenario } from "@/lib/scenarios";
import { Shell, type Screen } from "@/components/Shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Overview } from "@/screens/Overview";
import { Research } from "@/screens/Research";
import { BookLoading, Books } from "@/screens/Book";
import { Trial } from "@/screens/Trial";
import { LoadError, NoBooks, OverviewLoading } from "@/screens/OverviewStates";
import { SampleA } from "@/samples/SampleA";
import { SampleB } from "@/samples/SampleB";
import { SampleC } from "@/samples/SampleC";
import { SampleD } from "@/samples/SampleD";
import { SampleE } from "@/samples/SampleE";

const SAMPLES: Record<string, () => React.JSX.Element> = { "sample-a": SampleA, "sample-b": SampleB, "sample-c": SampleC, "sample-d": SampleD, "sample-e": SampleE };

type Load = { kind: "loading" } | { kind: "error"; retrying: boolean } | { kind: "ready"; data: AppData };

const state = new URLSearchParams(location.search).get("state");

function useScreen(): Screen {
  const read = (): Screen => {
    const h = location.hash.replace("#", "").split("/")[0];
    return h === "research" || h === "books" ? h : "overview";
  };
  const [s, setS] = useState<Screen>(read);
  useEffect(() => {
    const on = () => setS(read());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return s;
}

/** `#research/trial/<idea>` opens that idea's backtest. */
function useTrialId(): string | null {
  const read = () => { const [a, b, c] = location.hash.replace("#", "").split("/"); return a === "research" && b === "trial" ? c ?? null : null; };
  const [id, setId] = useState(read);
  useEffect(() => {
    const on = () => { setId(read()); scrollTo(0, 0); };
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return id;
}

/** Prototype loader: reads the sample file; ?state= previews loading, error, empty, alert, stopped, safety and stopfail. */
export function App() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const screen = useScreen();
  const trialId = useTrialId();

  useEffect(() => {
    if (state === "loading") return;
    if (state === "error") { setLoad({ kind: "error", retrying: false }); return; }
    const t = setTimeout(() => setLoad({ kind: "ready", data: scenario(sample, state) }), 200);
    return () => clearTimeout(t);
  }, []);

  const data = load.kind === "ready" ? load.data : null;
  // Design samples render on their own, outside the app shell, for comparison.
  const Sample = SAMPLES[location.hash.slice(1)];
  if (Sample) return <Sample />;
  return (
    <TooltipProvider>
      <Shell
        screen={screen}
        mode={data?.account_mode ?? "paper"}
        sample={data?.sample ?? true}
        counts={{ overview: data?.alerts.length, research: data?.research.waiting.length }}
      >
        {load.kind === "loading" && (screen === "books" ? <BookLoading /> : <OverviewLoading />)}
        {load.kind === "error" && (
          <LoadError
            retrying={load.retrying}
            onRetry={() => { setLoad({ kind: "error", retrying: true }); setTimeout(() => setLoad({ kind: "error", retrying: false }), 1200); }}
          />
        )}
        {data && screen === "research" && (trialId ? <Trial data={data} ideaId={trialId} /> : <Research data={data} />)}
        {data && screen === "overview" && (data.books.length ? <Overview data={data} /> : <NoBooks />)}
        {data && screen === "books" && <Books data={data} />}
      </Shell>
    </TooltipProvider>
  );
}
