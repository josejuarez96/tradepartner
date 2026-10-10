/** Plain-language formatting. Every number in the UI goes through here. */
const TZ = "America/New_York";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export const money = (v: number) => usd.format(v);
export const moneyRound = (v: number) => usd0.format(v);
export const signedMoney = (v: number) => (v > 0 ? "+" : v < 0 ? "−" : "") + usd.format(Math.abs(v));

/** "+1.24%" with a true minus sign; 0 prints as "0.00%". */
export const pct = (v: number, digits = 2) => {
  const s = Math.abs(v * 100).toFixed(digits);
  if (Number(s) === 0) return `0.${"0".repeat(digits)}%`;
  return (v > 0 ? "+" : "−") + s + "%";
};
/** Percentage points, for "ahead of SPY by 0.6 pts". */
export const pts = (v: number) => `${Math.abs(v * 100).toFixed(1)} pts`;

export const tone = (v: number): "gain" | "loss" | "flat" => (Math.abs(v) < 5e-5 ? "flat" : v > 0 ? "gain" : "loss");

const day = (iso: string) => new Date(iso.length === 10 ? iso + "T16:00:00-04:00" : iso);

export const shortDate = (iso: string) =>
  day(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: TZ });
export const weekdayDate = (iso: string) =>
  day(iso).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: TZ });
export const longDate = (iso: string) =>
  day(iso).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: TZ });
export const clock = (iso: string) =>
  day(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ }).replace(" AM", " am").replace(" PM", " pm");
