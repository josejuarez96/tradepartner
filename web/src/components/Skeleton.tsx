import "./Skeleton.css";

/** Placeholder block shaped like the content it stands in for. */
export function Skeleton({ w, h, r = "var(--radius-sm)" }: { w: string | number; h: string | number; r?: string }) {
  return <span className="skel" style={{ width: w, height: h, borderRadius: r }} aria-hidden />;
}
