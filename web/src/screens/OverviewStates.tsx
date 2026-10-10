import { AlertTriangle, BookOpen, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Loading: the page's real shape, so nothing jumps when numbers arrive. */
export function OverviewLoading() {
  return (
    <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @6xl/main:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} className="shadow-xs">
            <CardHeader className="gap-3"><Skeleton className="h-4 w-24" /><Skeleton className="h-8 w-36" /></CardHeader>
            <CardContent><Skeleton className="h-4 w-44" /></CardContent>
          </Card>
        ))}
      </div>
      <div className="px-4 lg:px-6"><Skeleton className="h-[360px] w-full rounded-xl" /></div>
      <div className="px-4 lg:px-6"><Skeleton className="h-56 w-full rounded-xl" /></div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-1 items-center justify-center p-6"><div className="flex max-w-md flex-col items-center gap-3 text-center">{children}</div></div>;
}

/** Error: what happened in plain words, what still holds, one thing to do. */
export function LoadError({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <Centered>
      <span className="bg-attention-soft text-attention grid size-10 place-items-center rounded-full"><AlertTriangle className="size-5" /></span>
      <h2 className="text-lg font-semibold">Can't reach TradePartner</h2>
      <p className="text-muted-foreground text-sm">
        This screen couldn't load. Your strategies keep running on their schedule; only this view is affected.
        If it keeps happening, the computer running TradePartner may be asleep or offline.
      </p>
      <Button onClick={onRetry} disabled={retrying}><RefreshCw className={retrying ? "animate-spin" : ""} />{retrying ? "Trying again…" : "Try again"}</Button>
    </Centered>
  );
}

/** Empty: before any book exists. */
export function NoBooks() {
  return (
    <Centered>
      <span className="bg-muted text-muted-foreground grid size-10 place-items-center rounded-full"><BookOpen className="size-5" /></span>
      <h2 className="text-lg font-semibold">No books running yet</h2>
      <p className="text-muted-foreground text-sm">When a strategy starts trading on paper, its value and how it compares with the S&amp;P 500 show up here.</p>
      <Button variant="outline" asChild><a href="#research">See research</a></Button>
    </Centered>
  );
}
