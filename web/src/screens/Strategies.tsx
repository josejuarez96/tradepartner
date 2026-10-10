import { Fragment } from "react";
import { ChevronRight } from "lucide-react";
import { STAGES, family, lab, num, pp, type Strategy } from "@/lib/lab";
import { cn } from "@/lib/utils";
import { TermInfo } from "@/components/Term";

const WHO = { you: "You", engine: "Engine", data: "Data", agent: "Agent" } as const;
const GRID = "grid items-center gap-x-4 grid-cols-[minmax(0,1fr)_16px] md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_36px_48px_48px_68px_104px_16px]";

/** The holdout's state on every registered row: spent, unspent or forward. */
function holdoutWord(s: Strategy) {
  if (!s.registered_at) return "";
  if (s.id === "h1-daily") return "forward";
  const f = family(s.family);
  if (!f) return "";
  return f.holdout.state === "spent" ? (f.holdout.spent_by === s.id ? "spent" : "spent by family") : f.holdout.state;
}

/**
 * Strategies: one list by the five stages, with Parked and Retired off to the
 * side. Registration is drawn as the step between Idea and Testing.
 */
export function Strategies() {
  const S = lab.strategies;
  const by = (k: string) => S.filter((s) => s.stage === k);
  const aside = [...by("parked"), ...by("retired")];
  return (
    <div className="mx-auto max-w-[1180px] px-4 pt-5 pb-12 sm:px-7 sm:pt-8">
      <h1 className="text-[22px] font-medium tracking-[-0.01em]">Strategies</h1>

      <StageTrack counts={STAGES.map((s) => by(s.key).length)} />

      <div className="mt-6 grid gap-10 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0">
          <div className={cn(GRID, "text-muted-foreground hidden border-b pb-2 text-xs md:grid")}>
            <span>Strategy</span>
            <span>Waits on</span>
            <span className="flex items-center justify-end">N<TermInfo k="n_trials" /></span>
            <span className="flex items-center justify-end">SR*<TermInfo k="sr_star" /></span>
            <span className="flex items-center justify-end">DSR<TermInfo k="dsr" /></span>
            <span className="text-right">vs SPY</span>
            <span className="flex items-center">Holdout<TermInfo k="holdout" /></span>
            <span />
          </div>
          {STAGES.map((st, i) => (
            <Fragment key={st.key}>
              {i === 1 && (
                <p className="text-muted-foreground mt-5 border-y border-dashed py-2.5 text-[13px]">
                  <span className="text-foreground font-medium">Registration</span> freezes the parameters; every run after it is a trial counted in N.<TermInfo k="registration" />
                </p>
              )}
              <section aria-labelledby={`st-${st.key}`} className="mt-5">
                <h2 id={`st-${st.key}`} className="flex items-baseline gap-2 pb-1.5 text-[15px] font-medium">
                  {st.label}
                </h2>
                {by(st.key).length === 0 ? (
                  <p className="text-muted-foreground border-t py-2.5 text-[13px]">{st.key === "live" ? "Nothing live; the live book waits on its own ADR (Phase 6)." : "None now."}</p>
                ) : (
                  <ul className="border-t">{by(st.key).map((s) => <Row key={s.id} s={s} />)}</ul>
                )}
              </section>
            </Fragment>
          ))}
        </div>

        <aside className="min-w-0 lg:border-l lg:pl-7">
          <h2 className="text-[15px] font-medium">Parked and retired</h2>
          <ul className="mt-2 border-t">
            {aside.map((s) => (
              <li key={s.id} className="border-b py-3">
                <a href={`#strategies/${s.id}`} className="group block">
                  <span className="flex items-baseline gap-2"><span className="font-medium group-hover:underline">{s.name}</span><span className="text-muted-foreground text-xs">{s.stage}</span></span>
                  <span className="mt-0.5 block text-[13px]">{s.parked_reason}</span>
                  {s.unpark_when && <span className="text-muted-foreground mt-0.5 block text-[13px]">Unparks when {s.unpark_when.charAt(0).toLowerCase() + s.unpark_when.slice(1)}</span>}
                </a>
              </li>
            ))}
            {!by("retired").length && <li className="text-muted-foreground py-3 text-[13px]">Nothing retired yet. A retired strategy shows its TP- claim here.</li>}
          </ul>
        </aside>
      </div>
      <p className="text-muted-foreground mt-10 border-t pt-4 text-xs">
        Sample data. N, SR* and DSR are the latest trial's stored values (trial_results); for a sweep, its argmax. Stages are derived from the registry, the lab tables and the paper windows.
      </p>
    </div>
  );
}

function Row({ s }: { s: Strategy }) {
  const l = s.latest;
  const dsr = l?.dsr ?? l?.dsr_excess;
  return (
    <li className="border-b">
      <a href={`#strategies/${s.id}`} className={cn(GRID, "hover:bg-raised focus-visible:ring-ring/50 -mx-2 min-h-12 rounded-md px-2 py-2 outline-none focus-visible:ring-2")}>
        <span className="min-w-0">
          <span className="block truncate font-medium">{s.name}</span>
          <span className="text-muted-foreground block truncate text-[13px] md:hidden">{s.waits_on && <>{WHO[s.waits_on.who]}: {s.waits_on.text}</>}</span>
        </span>
        <span className="hidden min-w-0 truncate text-[13px] md:block">
          {s.waits_on && <><span className={cn(s.waits_on.who === "you" ? "text-attention" : "text-muted-foreground")}>{WHO[s.waits_on.who]}</span> {s.waits_on.text}</>}
        </span>
        <span className="num hidden text-right text-[13px] md:block">{l ? l.n_trials : ""}</span>
        <span className="num hidden text-right text-[13px] md:block">{l ? num(l.sr_star) : ""}</span>
        <span className="num hidden text-right text-[13px] md:block">{dsr != null ? num(dsr) : ""}</span>
        <span className="num hidden text-right text-[13px] md:block">{l ? pp(l.excess_cagr_spy) : ""}</span>
        <span className="text-muted-foreground hidden truncate text-[13px] md:block">{holdoutWord(s)}</span>
        <ChevronRight className="text-muted-foreground size-4" />
      </a>
    </li>
  );
}

/** The five stages as one track, a count on each, so the pipeline reads at a glance. */
function StageTrack({ counts }: { counts: number[] }) {
  return (
    <ol className="mt-4 grid grid-cols-5 gap-1" aria-label="Strategies by stage">
      {STAGES.map((s, i) => (
        <li key={s.key}>
          <a href={`#strategies`} onClick={(e) => { e.preventDefault(); document.getElementById(`st-${s.key}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}
            className={cn("block rounded-md border px-2 py-2 sm:px-3", counts[i] ? "bg-raised" : "border-dashed")}>
            <span className="text-muted-foreground block truncate text-xs">{s.label}</span>
            <span className={cn("num block text-[18px]", !counts[i] && "text-muted-foreground")}>{counts[i]}</span>
          </a>
        </li>
      ))}
    </ol>
  );
}
