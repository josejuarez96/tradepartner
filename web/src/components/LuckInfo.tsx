import { Info } from "lucide-react";
import type { Idea } from "@/lib/types";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * How the luck check is calculated, one tap away. The method is the deflated
 * Sharpe ratio (Bailey and López de Prado, 2014), as implemented in
 * src/tradepartner/backtest/metrics.py. With an idea, it also shows that
 * idea's own inputs.
 */
export function LuckInfo({ idea }: { idea?: Idea | null }) {
  const r = idea?.result;
  const psr = r ? r.distinct < 2 : false;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          aria-label="How the luck check is calculated"
          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -m-2 inline-grid size-8 shrink-0 place-items-center rounded-full align-middle outline-none focus-visible:ring-2"
        >
          <Info className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent collisionPadding={12} className="max-h-[var(--radix-popover-content-available-height)] w-[min(92vw,380px)] overflow-y-auto text-[13px] leading-relaxed" align="start">
        <p className="font-medium">How the luck check works</p>
        <p className="text-muted-foreground mt-1">
          The chance a result reflects a real edge, not the best of several lucky tries.
        </p>
        <ol className="mt-3 list-decimal space-y-1.5 pl-4">
          <li><span className="font-medium">Score the result.</span> The strategy's Sharpe ratio on returns above the S&amp;P, after costs.</li>
          <li><span className="font-medium">Set the luck bar.</span> The Sharpe you would expect from the <i>best</i> of all versions tried in this family if none had any real edge. More versions, and more spread between them, raise the bar.</li>
          <li><span className="font-medium">Ask how sure we are it clears the bar,</span> given how many months or days of data there are and how lopsided or fat-tailed the returns were. That probability is the luck check.</li>
        </ol>
        <p className="text-muted-foreground mt-3">Under 50%: more likely luck than skill. 50 to 95%: could be either. 95% and up: likely real.</p>

        {r && idea && (
          <div className="mt-3 rounded-md border p-3">
            <p className="font-medium">{idea.name}: {Math.round(r.luck * 100)}%{r.sample?.includes("luck") && <span className="text-muted-foreground font-normal"> (sample)</span>}</p>
            <dl className="num mt-1.5 grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 text-xs">
              <dt className="text-muted-foreground font-sans">Versions counted in the family</dt><dd>{r.tries}</dd>
              <dt className="text-muted-foreground font-sans">Distinct parameter sets</dt><dd>{r.distinct}</dd>
              <dt className="text-muted-foreground font-sans">Returns used</dt><dd>{r.periods} {r.period}</dd>
              <dt className="text-muted-foreground font-sans">Luck bar</dt><dd>{psr ? "0, see below" : "above 0"}</dd>
            </dl>
            {psr && (
              <p className="text-muted-foreground mt-2 text-xs">
                With only one distinct parameter set there is nothing to pick the best of, so the bar is 0 and this is the
                plain chance the Sharpe is above zero (the probabilistic Sharpe ratio).
              </p>
            )}
          </div>
        )}

        <details className="mt-3">
          <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-xs">The maths</summary>
          <div className="num mt-2 space-y-1.5 text-[11.5px] leading-snug">
            <p>luck = Φ( (SR − SR*) · √(T − 1) / √(1 − γ₃·SR + (γ₄ − 1)/4 · SR²) )</p>
            <p>SR* = √V · ( (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) )</p>
            <p className="font-sans text-muted-foreground">
              SR: Sharpe per period; T: number of periods; γ₃, γ₄: skew and kurtosis of the returns; N: versions tried in
              the family; V: variance of their Sharpes; γ: Euler's constant (0.577). Deflated Sharpe ratio, Bailey and
              López de Prado (2014). Promotion uses the highest bar the family has ever reached, so later tries can't lower it.
            </p>
          </div>
        </details>
      </PopoverContent>
    </Popover>
  );
}
