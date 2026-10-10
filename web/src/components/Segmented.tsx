import "./Segmented.css";

interface Props<K extends string> {
  label: string;
  options: { key: K; label: string }[];
  value: K;
  onChange: (k: K) => void;
}

/** A small set of mutually exclusive choices (the chart's time range). */
export function Segmented<K extends string>({ label, options, value, onChange }: Props<K>) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.key}
          role="radio"
          aria-checked={o.key === value}
          className="seg__opt"
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
