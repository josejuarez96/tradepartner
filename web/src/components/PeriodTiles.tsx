import { pct, tone } from "../lib/format";
import "./PeriodTiles.css";

export interface Period<K extends string> { key: K; label: string; ret: number | null }

interface Props<K extends string> {
  label: string;
  periods: Period<K>[];
  value: K;
  onChange: (k: K) => void;
}

/**
 * The chart's range control, which also answers "how did I do over …?":
 * each option carries your return for that period, tinted by its sign.
 */
export function PeriodTiles<K extends string>({ label, periods, value, onChange }: Props<K>) {
  return (
    <div className="ptiles" role="radiogroup" aria-label={label}>
      {periods.map((p) => {
        const t = p.ret === null ? "flat" : tone(p.ret);
        return (
          <button
            key={p.key}
            role="radio"
            aria-checked={p.key === value}
            aria-disabled={p.ret === null || undefined}
            title={p.ret === null ? "Not enough history yet" : undefined}
            className={`ptile ptile--${t}`}
            onClick={() => p.ret !== null && onChange(p.key)}
          >
            <span className="ptile__label">{p.label}</span>
            <span className="ptile__value num">{p.ret === null ? "—" : pct(p.ret, 1)}</span>
          </button>
        );
      })}
    </div>
  );
}
