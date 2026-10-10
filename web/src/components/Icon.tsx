/** The handful of glyphs the app uses; 16px, stroke-based, inherit colour. */
const paths = {
  check: "M3.5 8.5l3 3 6-7",
  alert: "M8 5v3.5M8 11h.01M7.13 2.5L1.6 12a1 1 0 00.87 1.5h11.06a1 1 0 00.87-1.5L8.87 2.5a1 1 0 00-1.74 0z",
  pause: "M5.5 3.5v9M10.5 3.5v9",
  chevron: "M6 3.5L10.5 8 6 12.5",
  up: "M8 12.5v-9M4 7.5l4-4 4 4",
  down: "M8 3.5v9M4 8.5l4 4 4-4",
  refresh: "M13 8a5 5 0 11-1.46-3.54M13 2.5v3h-3",
  overview: "M2.5 13.5h11M4 11V8M8 11V4.5M12 11V6.5",
  books: "M3 2.5h7l3 3v8H3zM10 2.5v3h3M5.5 8.5h5M5.5 11h3",
  strategies: "M8 2.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM8 5.5v2.75l1.75 1.75",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 16, label }: { name: IconName; size?: number; label?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <path d={paths[name]} />
    </svg>
  );
}
