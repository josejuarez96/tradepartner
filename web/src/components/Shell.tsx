import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { ThemeSwitch } from "./ThemeSwitch";

export type Screen = "today" | "strategies" | "books";
const NAV: { key: Screen; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "strategies", label: "Strategies" },
  { key: "books", label: "Books" },
];

interface Props {
  screen: Screen;
  mode: "paper" | "live";
  sample: boolean;
  counts: Partial<Record<Screen, number>>;
  children: ReactNode;
}

/** A quiet top bar; on phone the navigation moves to a bottom tab bar. */
export function Shell({ screen, mode, sample, counts, children }: Props) {
  const tab = (n: (typeof NAV)[number], phone: boolean) => (
    <a
      key={n.key}
      href={`#${n.key}`}
      aria-current={n.key === screen ? "page" : undefined}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 transition-colors",
        phone ? "h-14 flex-1 text-[13px]" : "h-14",
        n.key === screen ? "text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {n.label}
      {!!counts[n.key] && <span className="num text-attention text-xs" aria-label={`, ${counts[n.key]} waiting on you`}>{counts[n.key]}</span>}
    </a>
  );
  return (
    <div className="flex min-h-dvh flex-col overflow-x-clip">
      <header className="flex h-14 items-center gap-7 border-b px-4 sm:px-7">
        <a href="#today" className="text-[15px] font-semibold">TradePartner</a>
        <nav className="hidden items-center gap-6 sm:flex" aria-label="Main">{NAV.map((n) => tab(n, false))}</nav>
        <span className="text-muted-foreground ml-auto text-[13px]">{mode === "paper" ? "Paper account" : "Live account"}{sample && ", sample data"}</span>
        <ThemeSwitch />
      </header>
      <main className="flex-1 pb-16 sm:pb-0">{children}</main>
      <nav className="bg-background fixed inset-x-0 bottom-0 z-20 flex border-t pb-[env(safe-area-inset-bottom)] sm:hidden" aria-label="Main">
        {NAV.map((n) => tab(n, true))}
      </nav>
    </div>
  );
}

/** A wide main column and, on desktop, a side rail divided by a rule. */
export function Page({ children, rail }: { children: ReactNode; rail?: ReactNode }) {
  return (
    <div className="mx-auto grid max-w-[1180px] gap-2 px-4 pt-5 pb-12 sm:px-7 sm:pt-7 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-12">
      <div className="min-w-0">{children}</div>
      {rail && <aside className="min-w-0 lg:border-l lg:pl-7">{rail}</aside>}
    </div>
  );
}

/** A section with a plain heading and an optional quiet note beside it. */
export function Section({ title, note, children, className }: { title: ReactNode; note?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("mt-8", className)}>
      <h2 className="mb-3 flex flex-wrap items-baseline gap-x-2.5 text-lg font-medium">
        {title}
        {note && <span className="text-muted-foreground text-[13px] font-normal">{note}</span>}
      </h2>
      {children}
    </section>
  );
}

/** A side-rail list heading. */
export function RailHead({ children }: { children: ReactNode }) {
  return <h2 className="text-muted-foreground mt-7 mb-1 text-[13px] font-medium first:mt-0">{children}</h2>;
}

/**
 * The workstation's soft panel, calmed: one raised surface with a small
 * heading inside. Used where a group is a unit of attention (waiting on you,
 * the books, the specification), never around every row.
 */
export function Panel({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("bg-raised min-w-0 rounded-[10px] border", className)}>
      {title && (
        <header className="flex min-h-11 items-baseline gap-3 px-4 pt-3 pb-1">
          <h2 className="text-[13.5px] font-medium">{title}</h2>
          {aside && <span className="text-muted-foreground ml-auto text-xs">{aside}</span>}
        </header>
      )}
      {children}
    </section>
  );
}

/** The label every prototype-only write carries: what it would run, and why it can't yet. */
export function PrototypeWrite({ command, children }: { command: string; children?: ReactNode }) {
  return (
    <p className="text-muted-foreground border-t pt-3 text-xs leading-relaxed">
      Prototype only: this write waits on the ADR 0018 amendment being drafted{children}. Until then it is the command{" "}
      <code className="num text-foreground [overflow-wrap:anywhere]">{command}</code>, with the same sentence above.
    </p>
  );
}
