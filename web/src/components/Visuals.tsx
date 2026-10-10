import type { Idea } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Small graphics that explain themselves: each mark carries its own symbol,
 * so nothing needs a legend and nothing relies on colour alone.
 */

type Ev = NonNullable<Idea["evidence"]>;

/** Three drawn marks, one per kind of study: a tick, a level dash, a cross. */
const MARK = { for: "M2.5 6.5 5 9l4.5-6", mixed: "M2.5 6h7", against: "M3 3l6 6M9 3 3 9" } as const;
const KIND = {
  for: { cls: "bg-gain/15 text-gain", label: "supports it" },
  mixed: { cls: "bg-attention/15 text-attention", label: "mixed" },
  against: { cls: "bg-loss/15 text-loss", label: "against it" },
} as const;

export function evidenceVerdict(e: Ev) {
  const known = e.for + e.mixed + e.against;
  if (!known) return { text: "No evidence yet", cls: "text-muted-foreground" };
  if (e.for > e.against && e.for >= e.mixed) return { text: "Mostly supported", cls: "text-gain" };
  if (e.against > e.for && e.against >= e.mixed) return { text: "Mostly against", cls: "text-loss" };
  return { text: "Mixed", cls: "text-attention" };
}

/** One tile per published study: ✓ supports, – mixed, ✕ against. */
export function EvidenceTally({ e, showVerdict = true }: { e: Ev; showVerdict?: boolean }) {
  const tiles = [
    ...Array.from({ length: e.for }, () => "for" as const),
    ...Array.from({ length: e.mixed }, () => "mixed" as const),
    ...Array.from({ length: e.against }, () => "against" as const),
  ];
  const v = evidenceVerdict(e);
  const summary = `${v.text}: ${e.for} supporting, ${e.mixed} mixed, ${e.against} against`;
  return (
    <div className="flex items-center gap-2" role="img" aria-label={summary} title={summary}>
      {tiles.length ? (
        <span className="flex gap-1">
          {tiles.map((k, i) => {
            return (
              <span key={i} className={cn("grid size-5 place-items-center rounded", KIND[k].cls)}>
                <svg viewBox="0 0 12 12" className="size-3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d={MARK[k]} />
                </svg>
              </span>
            );
          })}
        </span>
      ) : (
        <span className="text-muted-foreground grid size-5 place-items-center rounded border border-dashed text-[10px]">?</span>
      )}
      {showVerdict && <span className={cn("font-medium whitespace-nowrap", v.cls)}>{v.text}</span>}
    </div>
  );
}

/**
 * The luck check on a labelled scale: under 50% probably luck, 50–95% could
 * be luck, above 95% likely real. The marker shows where the result sits.
 */
export function LuckScale({ v, compact = false }: { v: number; compact?: boolean }) {
  const pctV = Math.round(v * 100);
  const zone = v >= 0.95 ? "likely real" : v >= 0.5 ? "could be luck" : "probably luck";
  return (
    <div className={cn("flex flex-col gap-1.5", compact ? "w-40" : "w-full")} role="img" aria-label={`Luck check ${pctV}%, ${zone}`}>
      <div className="flex items-baseline justify-between text-sm">
        <span className="num font-medium">{pctV}%</span>
        <span className="text-muted-foreground text-xs">{zone}</span>
      </div>
      <div className="relative h-2">
        <div className="absolute inset-0 flex gap-0.5 overflow-hidden rounded-full">
          <span className="bg-loss/25 h-full" style={{ width: "50%" }} />
          <span className="bg-attention/25 h-full" style={{ width: "45%" }} />
          <span className="bg-gain/25 h-full" style={{ width: "5%" }} />
        </div>
        <span
          className="bg-foreground ring-background absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2"
          style={{ left: `${Math.min(98, Math.max(2, pctV))}%` }}
        />
      </div>
      {!compact && (
        <div className="text-muted-foreground relative h-4 text-[11px]">
          <span className="absolute left-0">luck</span>
          <span className="absolute left-1/2 -translate-x-1/2">50%</span>
          <span className="absolute right-0">95%+ real</span>
        </div>
      )}
    </div>
  );
}

/** Exam progress: one step per rebalance when there are few, a labelled track when there are many. */
export function ExamSteps({ done, of, unit }: { done: number; of: number; unit: string }) {
  const label = `${done} of ${of} ${unit}`;
  return (
    <div className="flex flex-col gap-1.5" role="img" aria-label={`Exam: ${label}`}>
      {of <= 13 ? (
        <div className="flex gap-1">
          {Array.from({ length: of }, (_, i) => (
            <span key={i} className={cn("h-2 flex-1 rounded-sm", i < done ? "bg-foreground" : "bg-muted")} />
          ))}
        </div>
      ) : (
        <div className="bg-muted h-2 overflow-hidden rounded-full">
          <div className="bg-foreground h-full rounded-full" style={{ width: `${(done / of) * 100}%` }} />
        </div>
      )}
      <p className="text-muted-foreground num text-xs">{label}</p>
    </div>
  );
}

/** Countable slots: filled = used. */
export function Slots({ used, of, label }: { used: number; of: number; label: string }) {
  return (
    <span className="flex items-center gap-1" role="img" aria-label={`${used} of ${of} ${label} used`}>
      {Array.from({ length: of }, (_, i) => (
        <span key={i} className={cn("size-3 rounded-sm border", i < used ? "bg-foreground border-foreground" : "border-muted-foreground/50")} />
      ))}
    </span>
  );
}
