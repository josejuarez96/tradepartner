import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { ThemeSwitch } from "./ThemeSwitch";

export type Screen = "overview" | "books" | "research";
const NAV: { key: Screen; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "books", label: "Books" },
  { key: "research", label: "Research" },
];

interface Props {
  screen: Screen;
  mode: "paper" | "live";
  sample: boolean;
  /** Things waiting on the owner, per screen; shown as a count on the tab. */
  counts: Partial<Record<Screen, number>>;
  children: ReactNode;
}

/** Direction C shell: one slim top bar; on phone the navigation moves to a bottom tab bar. */
export function Shell({ screen, mode, sample, counts, children }: Props) {
  const tab = (n: (typeof NAV)[number], phone: boolean) => (
    <a
      key={n.key}
      href={`#${n.key}`}
      aria-current={n.key === screen ? "page" : undefined}
      className={cn(
        "relative inline-flex items-center justify-center gap-1.5 transition-colors",
        phone ? "h-14 flex-1 text-[13px]" : "h-11 px-3",
        n.key === screen ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        n.key === screen && (phone ? "shadow-[inset_0_2px_0_var(--foreground)]" : "shadow-[inset_0_-2px_0_var(--foreground)]"),
      )}
    >
      {n.label}
      {!!counts[n.key] && <span className="num text-attention text-[11px]" aria-label={`, ${counts[n.key]} waiting on you`}>{counts[n.key]}</span>}
    </a>
  );
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="bg-raised sticky top-0 z-20 flex h-11 items-center gap-6 border-b px-4">
        <a href="#overview" className="font-semibold">TradePartner</a>
        <nav className="hidden items-center sm:flex" aria-label="Main">{NAV.map((n) => tab(n, false))}</nav>
        <span className="text-muted-foreground ml-auto text-xs">
          {mode === "paper" ? "Paper account" : "Live account"}{sample && ", sample data"}
        </span>
        <ThemeSwitch />
      </header>
      <main className="flex-1 pb-14 sm:pb-0">{children}</main>
      <nav className="bg-raised fixed inset-x-0 bottom-0 z-20 flex border-t pb-[env(safe-area-inset-bottom)] sm:hidden" aria-label="Main">
        {NAV.map((n) => tab(n, true))}
      </nav>
    </div>
  );
}

/** A region of the grid: square, divided from its neighbours by 1px rules, with a slim header. */
export function Panel({ title, aside, className, children }: { title?: ReactNode; aside?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section className={cn("bg-background px-4 py-3", className)}>
      {(title || aside) && (
        <div className="mb-2.5 flex items-center justify-between gap-3">
          {title && <h2 className="text-[13px] font-medium">{title}</h2>}
          {aside && <div className="text-muted-foreground flex items-center gap-2 text-xs">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
