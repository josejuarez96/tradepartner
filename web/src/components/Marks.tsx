import { pct, tone } from "@/lib/format";
import { cn } from "@/lib/utils";

const toneText = { gain: "text-gain", loss: "text-loss", flat: "text-muted-foreground" } as const;

/** Small trend line for a rail row, coloured like the number beside it. */
export function Spark({ values, t }: { values: number[]; t: "gain" | "loss" | "flat" }) {
  const lo = Math.min(...values), hi = Math.max(...values);
  const d = values.map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * 64).toFixed(1)} ${(2 + (1 - (v - lo) / (hi - lo || 1)) * 20).toFixed(1)}`).join("");
  return <svg viewBox="0 0 64 24" className={cn("h-6 w-16 shrink-0", toneText[t])} aria-hidden><path d={d} fill="none" stroke="currentColor" strokeWidth="1.4" vectorEffect="non-scaling-stroke" /></svg>;
}

export function Pill({ v }: { v: number }) {
  const t = tone(v);
  return <span className={cn("num min-w-[76px] shrink-0 rounded-md px-2 py-1 text-center text-[13px]", t === "gain" ? "bg-gain/15 text-gain" : t === "loss" ? "bg-loss/15 text-loss" : "bg-secondary text-muted-foreground")}>{pct(v)}</span>;
}

/** ✓, ✕ or – drawn as a mark, so a pass or fail never relies on colour alone. */
export function PassMark({ pass }: { pass: boolean | null }) {
  const d = pass === true ? "M2.5 6.5 5 9l4.5-6" : pass === false ? "M3 3l6 6M9 3 3 9" : "M3 6h6";
  return (
    <span
      role="img"
      aria-label={pass === true ? "pass" : pass === false ? "fail" : "reported, not a gate"}
      className={cn("grid size-5 shrink-0 place-items-center rounded", pass === true ? "bg-gain/15 text-gain" : pass === false ? "bg-loss/15 text-loss" : "bg-secondary text-muted-foreground")}
    >
      <svg viewBox="0 0 12 12" className="size-3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={d} /></svg>
    </span>
  );
}

/**
 * A book's tracking gap against its tolerance, on a scale labelled on the
 * track: the shaded zone is ±tolerance, the marker is the gap.
 */
export function TrackScale({ gap, tolerance, className }: { gap: number; tolerance: number; className?: string }) {
  const span = tolerance * 2;
  const x = (v: number) => Math.min(97, Math.max(3, ((v + span) / (2 * span)) * 100));
  const inside = Math.abs(gap) <= tolerance;
  return (
    <span className={cn("relative block h-5", className)} role="img" aria-label={`Gap ${pct(gap)}, ${inside ? "inside" : "outside"} the tolerance of ±${(tolerance * 100).toFixed(1)}%`}>
      <span className="bg-border absolute inset-x-0 top-1/2 h-px" />
      <span className="bg-foreground/[0.09] absolute inset-y-0.5 rounded-sm" style={{ left: `${x(-tolerance)}%`, right: `${100 - x(tolerance)}%` }} />
      <span className="bg-muted-foreground/60 absolute top-0.5 bottom-0.5 w-px" style={{ left: "50%" }} />
      <span className={cn("ring-raised absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2", inside ? "bg-foreground" : "bg-attention")} style={{ left: `${x(gap)}%` }} />
    </span>
  );
}

/** Countable steps: one per rebalance when there are few, a track with a count when many. */
export function Steps({ done, of, label }: { done: number; of: number; label: string }) {
  return (
    <span className="flex items-center gap-3" role="img" aria-label={`${done} of ${of} ${label}`}>
      {of <= 13 ? (
        <span className="flex flex-1 gap-1">
          {Array.from({ length: of }, (_, i) => <span key={i} className={cn("h-2 flex-1 rounded-sm", i < done ? "bg-foreground" : "bg-muted")} />)}
        </span>
      ) : (
        <span className="bg-muted h-2 flex-1 overflow-hidden rounded-full"><span className="bg-foreground block h-full rounded-full" style={{ width: `${(done / of) * 100}%` }} /></span>
      )}
      <span className="num text-muted-foreground shrink-0 text-xs">{done} of {of}</span>
    </span>
  );
}
