import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Panel } from "@/components/Shell";

/** Loading: the real grid in grey, so nothing jumps when numbers arrive. */
export function OverviewLoading() {
  return (
    <div className="flex flex-col" aria-busy="true" aria-label="Loading">
      <div className="bg-border grid grid-cols-2 gap-px border-b sm:flex">
        {[0, 1, 2, 3].map((i) => <div key={i} className="bg-background px-4 py-3"><Skeleton className="h-4 w-36" /></div>)}
      </div>
      <div className="bg-border grid gap-px lg:grid-cols-[minmax(0,1.7fr)_minmax(340px,1fr)]">
        <Panel className="lg:row-span-2"><Skeleton className="mb-3 h-4 w-40" /><Skeleton className="h-[380px] w-full" /></Panel>
        <Panel>{[0, 1, 2].map((i) => <Skeleton key={i} className="mb-3 h-8 w-full" />)}</Panel>
        <Panel>{[0, 1, 2].map((i) => <Skeleton key={i} className="mb-3 h-6 w-full" />)}</Panel>
      </div>
    </div>
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
    <Notice title="No books running yet" action={<Button size="sm" variant="outline" asChild><a href="#research">Open research</a></Button>}>
      When a strategy starts trading on paper, its value and its return against the S&amp;P 500 show up here.
    </Notice>
  );
}
