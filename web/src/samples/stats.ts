/** Research-grade statistics from a daily value series. Annualised with 252 sessions. */
export function stats(values: number[], bench: number[]) {
  const r = values.slice(1).map((v, i) => v / values[i] - 1);
  const b = bench.slice(1).map((v, i) => v / bench[i] - 1);
  const mean = (x: number[]) => x.reduce((a, c) => a + c, 0) / x.length;
  const sd = (x: number[]) => { const m = mean(x); return Math.sqrt(x.reduce((a, c) => a + (c - m) ** 2, 0) / (x.length - 1)); };
  const diff = r.map((v, i) => v - b[i]);
  let peak = values[0], mdd = 0;
  for (const v of values) { peak = Math.max(peak, v); mdd = Math.min(mdd, v / peak - 1); }
  return {
    days: values.length,
    vol: sd(r) * Math.sqrt(252),
    sharpe: (mean(r) / sd(r)) * Math.sqrt(252),
    maxDD: mdd,
    te: sd(diff) * Math.sqrt(252),
    beta: (() => { const mb = mean(b), mr = mean(r); const cov = r.reduce((a, v, i) => a + (v - mr) * (b[i] - mb), 0) / (r.length - 1); return cov / sd(b) ** 2; })(),
  };
}
