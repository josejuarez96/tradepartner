import { useEffect, useState } from "react";
import { sample } from "./lib/data";
import type { AppData } from "./lib/types";
import { Shell, type Screen } from "./components/Shell";
import { Overview } from "./screens/Overview";
import { OverviewLoading, LoadError, NoBooks } from "./screens/OverviewStates";
import { scenario } from "./lib/scenarios";

type Load = { kind: "loading" } | { kind: "error"; retrying: boolean } | { kind: "ready"; data: AppData };

const params = new URLSearchParams(location.search);
const state = params.get("state");

/** Prototype loader: reads the sample file; ?state= previews loading, error, empty, alert and stopped. */
export function App() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const screen: Screen = "overview";

  useEffect(() => {
    if (state === "loading") return;
    if (state === "error") { setLoad({ kind: "error", retrying: false }); return; }
    const t = setTimeout(() => setLoad({ kind: "ready", data: scenario(sample, state) }), 250);
    return () => clearTimeout(t);
  }, []);

  const data = load.kind === "ready" ? load.data : null;
  return (
    <Shell
      screen={screen}
      mode={data?.account_mode ?? "paper"}
      sample={data?.sample ?? true}
      attention={data?.alerts.length ?? 0}
      footer={
        <p>
          Sample data for design only. Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a> Lightweight Charts.
        </p>
      }
    >
      {load.kind === "loading" && <OverviewLoading />}
      {load.kind === "error" && (
        <LoadError
          retrying={load.retrying}
          onRetry={() => {
            setLoad({ kind: "error", retrying: true });
            setTimeout(() => setLoad({ kind: "error", retrying: false }), 1200);
          }}
        />
      )}
      {data && (data.books.length ? <Overview data={data} /> : <NoBooks />)}
    </Shell>
  );
}
