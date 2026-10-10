import { RANGES, type RangeKey } from "@/lib/data";
import { cn } from "@/lib/utils";

/** The 1W / 1M / 3M / All pills under a hero chart; the chosen one takes the line's colour. */
export function RangePicker({ value, onChange, available, tone }: {
  value: RangeKey; onChange: (k: RangeKey) => void; available: (k: RangeKey) => boolean; tone: "gain" | "loss" | "flat";
}) {
  return (
    <div role="radiogroup" aria-label="Time range" className="mt-3 flex gap-1 border-b pb-4">
      {RANGES.map((r) => {
        const ok = available(r.key), on = r.key === value;
        return (
          <button key={r.key} role="radio" aria-checked={on} disabled={!ok} title={ok ? undefined : "Not enough history yet"} onClick={() => onChange(r.key)}
            className={cn("h-9 min-w-11 rounded-full px-3 text-[13px] font-medium transition-colors", on ? (tone === "loss" ? "bg-loss/15 text-loss" : "bg-gain/15 text-gain") : "text-muted-foreground hover:text-foreground", !ok && "opacity-35")}>
            {r.label}
          </button>
        );
      })}
    </div>
  );
}
