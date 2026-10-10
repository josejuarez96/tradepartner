import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Page } from "@/components/Shell";

/** Loading: the real layout in grey, so nothing jumps when numbers arrive. */
export function OverviewLoading() {
  return (
    <Page rail={<>{[0, 1, 2].map((i) => <Skeleton key={i} className="mb-4 h-10 w-full" />)}</>}>
      <div aria-busy="true" aria-label="Loading">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="mt-2 h-10 w-72" />
        <Skeleton className="mt-2 h-4 w-48" />
        <Skeleton className="mt-6 h-[320px] w-full" />
        <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div>
      </div>
    </Page>
  );
}

function Notice({ title, children, action }: { title: string; children: React.ReactNode; action: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-start gap-3 px-4 py-16">
      <h2 className="text-base font-medium">{title}</h2>
      <p className="text-muted-foreground">{children}</p>
      {action}
    </div>
  );
}

/** Error: what happened, what still holds, one thing to do. */
export function LoadError({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <Notice title="Can't reach TradePartner" action={<Button size="sm" onClick={onRetry} disabled={retrying}><RefreshCw className={retrying ? "animate-spin" : ""} />{retrying ? "Trying again…" : "Try again"}</Button>}>
      Your strategies keep running on their schedule; only this view is affected. If it keeps happening, the computer running TradePartner may be asleep or offline.
    </Notice>
  );
}

/** Empty: before any book exists. */
export function NoBooks() {
  return (
    <Notice title="No books running yet" action={<Button size="sm" variant="outline" asChild><a href="#strategies">Open strategies</a></Button>}>
      When a strategy starts trading on paper, its value and its return against the S&amp;P 500 show up here.
    </Notice>
  );
}
