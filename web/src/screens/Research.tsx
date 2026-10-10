import { useState } from "react";
import {
  CircleHelp, FlaskConical, Lock, MessageSquareText, NotebookPen, Pencil, Play, Scale, ChevronRight,
} from "lucide-react";
import type { AppData, Family, Idea, Lesson, Stage, WaitingItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const STAGES: { key: Stage; label: string; hint: string }[] = [
  { key: "on_paper", label: "On paper", hint: "Trading with practice money while it takes its exam" },
  { key: "ready", label: "Ready to test", hint: "Written and built; waiting for a run" },
  { key: "blocked", label: "Blocked", hint: "Can't move until something else lands" },
  { key: "exploring", label: "Exploring", hint: "Promising on paper; needs data or work first" },
  { key: "parked", label: "Parked", hint: "Set aside on purpose, with a reason" },
];

const KIND_ICON: Record<WaitingItem["kind"], typeof Play> = { run: Play, answer: MessageSquareText, decide: Scale, record: NotebookPen };

const pct1 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
const SampleMark = () => <span className="text-muted-foreground ml-1 text-[10px] uppercase" title="Sample value">sample</span>;

/**
 * Research, built around the owner's questions rather than the pipeline:
 * what's waiting on me, what's on the go, did it work (and could it be luck),
 * what have I spent, what have I learned. Every idea opens the same story:
 * why → what we expected → what happened → the exam → what's next.
 */
export function Research({ data }: { data: AppData }) {
  const r = data.research;
  const [open, setOpen] = useState<Idea | null>(null);
  const byId = (id?: string) => r.ideas.find((i) => i.id === id) ?? null;
  const tested = r.ideas.filter((i) => i.result);

  return (
    <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6">
      <div className="px-4 lg:px-6">
        <Card className="shadow-xs">
          <CardHeader>
            <CardTitle>Waiting on you</CardTitle>
            <CardDescription>Only you can do these. Each one unblocks something.</CardDescription>
            <CardAction><Badge variant="outline" className="text-attention">{r.waiting.length}</Badge></CardAction>
          </CardHeader>
          <CardContent className="px-0">
            <ul className="divide-y border-t">
              {r.waiting.map((w) => {
                const Icon = KIND_ICON[w.kind];
                const idea = byId(w.idea_id);
                return (
                  <li key={w.id} className="flex items-start gap-3 px-4 py-4 sm:px-6">
                    <span className="bg-muted text-muted-foreground mt-0.5 hidden size-8 shrink-0 place-items-center rounded-md sm:grid"><Icon className="size-4" /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">{w.title}</p>
                        {idea && <Badge variant="secondary" className="font-mono">{idea.code}</Badge>}
                      </div>
                      <p className="text-muted-foreground mt-1 text-sm">{w.why}</p>
                      <p className="text-muted-foreground mt-1 text-xs">Next step: <span className="text-foreground">{w.step}</span> · {w.effort}</p>
                    </div>
                    {idea && (
                      <Button variant="ghost" size="sm" className="shrink-0 max-sm:size-9 max-sm:px-0" aria-label={`Open ${idea.name}`} onClick={() => setOpen(idea)}>
                        <span className="max-sm:sr-only">Open</span> <ChevronRight />
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      </div>

      <div className="px-4 lg:px-6">
        <Tabs defaultValue="ideas" className="gap-4">
          <TabsList>
            <TabsTrigger value="ideas">Ideas</TabsTrigger>
            <TabsTrigger value="results">Results</TabsTrigger>
            <TabsTrigger value="budget">Honesty budget</TabsTrigger>
            <TabsTrigger value="lessons">Lessons</TabsTrigger>
          </TabsList>

          <TabsContent value="ideas" className="flex flex-col gap-6">
            {STAGES.map((s) => {
              const ideas = r.ideas.filter((i) => i.stage === s.key);
              if (!ideas.length) return null;
              return (
                <section key={s.key} className="flex flex-col gap-3">
                  <div className="flex items-baseline gap-2">
                    <h2 className="text-sm font-medium">{s.label}</h2>
                    <span className="text-muted-foreground text-sm">{ideas.length}</span>
                    <span className="text-muted-foreground hidden text-sm sm:inline">· {s.hint}</span>
                  </div>
                  <div className="grid grid-cols-1 gap-3 @3xl/main:grid-cols-2 @6xl/main:grid-cols-3">
                    {ideas.map((i) => <IdeaCard key={i.id} idea={i} onOpen={() => setOpen(i)} />)}
                  </div>
                </section>
              );
            })}
          </TabsContent>

          <TabsContent value="results">
            <Card className="shadow-xs">
              <CardHeader>
                <CardTitle>Finished tests</CardTitle>
                <CardDescription>In-sample results after costs. The luck check counts every version ever tried in the same family.</CardDescription>
              </CardHeader>
              <CardContent className="px-0">
                <Table>
                  <TableHeader className="bg-muted/50">
                    <TableRow>
                      <TableHead className="pl-6">Idea</TableHead>
                      <TableHead className="hidden md:table-cell">Window</TableHead>
                      <TableHead className="text-right">vs S&amp;P / yr</TableHead>
                      <TableHead className="w-48">Luck check</TableHead>
                      <TableHead className="hidden text-right sm:table-cell">Tries</TableHead>
                      <TableHead className="hidden pr-6 lg:table-cell">Exam</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tested.map((i) => (
                      <TableRow key={i.id} className="cursor-pointer" onClick={() => setOpen(i)}>
                        <TableCell className="pl-6">
                          <span className="font-medium">{i.name}</span> <span className="text-muted-foreground font-mono text-xs">{i.code}</span>
                        </TableCell>
                        <TableCell className="text-muted-foreground hidden md:table-cell">{i.result!.window}</TableCell>
                        <TableCell className={cn("num text-right", i.result!.vs_spy >= 0 ? "text-gain" : "text-loss")}>
                          {pct1(i.result!.vs_spy)} pts{i.result!.sample?.includes("vs_spy") && <SampleMark />}
                        </TableCell>
                        <TableCell><Luck v={i.result!.luck} sample={i.result!.sample?.includes("luck")} /></TableCell>
                        <TableCell className="num hidden text-right sm:table-cell">{i.result!.tries}</TableCell>
                        <TableCell className="text-muted-foreground hidden pr-6 lg:table-cell"><ExamLine idea={i} /></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="budget" className="flex flex-col gap-4">
            <p className="text-muted-foreground max-w-3xl text-sm">
              Every test you run makes the next good-looking result a little more likely to be luck, and each family gets one
              final exam on data nobody has looked at. This is what each family has used.
            </p>
            <div className="grid grid-cols-1 gap-4 @3xl/main:grid-cols-3">
              {r.families.map((f) => <FamilyCard key={f.id} family={f} />)}
            </div>
          </TabsContent>

          <TabsContent value="lessons" className="flex flex-col gap-4">
            <Card className="shadow-xs">
              <CardHeader>
                <CardTitle>What your own tests taught you</CardTitle>
                <CardDescription>Lessons you've recorded from TradePartner's results.</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-6">
                  <p className="font-medium">None recorded yet</p>
                  <p className="text-muted-foreground text-sm">Monthly momentum has a finished test and a spent exam. Writing down what it showed is the first lesson.</p>
                  <Button variant="outline" size="sm" onClick={() => setOpen(byId("h1"))}><Pencil />Open monthly momentum</Button>
                </div>
              </CardContent>
            </Card>
            <Card className="shadow-xs">
              <CardHeader>
                <CardTitle>What published research says</CardTitle>
                <CardDescription>The findings your ideas lean on, graded by how well they hold up.</CardDescription>
              </CardHeader>
              <CardContent className="px-0">
                <ul className="divide-y border-t">
                  {r.lessons.map((l) => <LessonRow key={l.id} lesson={l} />)}
                </ul>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      <IdeaSheet idea={open} data={data} onClose={() => setOpen(null)} />
    </div>
  );
}

function stageBadge(stage: Stage) {
  const s = STAGES.find((x) => x.key === stage)!;
  const cls = stage === "on_paper" ? "text-gain" : stage === "ready" ? "text-attention" : "text-muted-foreground";
  return <Badge variant="outline" className={cls}>{s.label}</Badge>;
}

function Evidence({ e }: { e: NonNullable<Idea["evidence"]> }) {
  const total = e.for + e.mixed + e.against + (e.untested ?? 0) || 1;
  const seg = (n: number, cls: string) => n > 0 && <span className={cls} style={{ width: `${(n / total) * 100}%` }} />;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="bg-muted flex h-1.5 w-full gap-0.5 overflow-hidden rounded-full" aria-hidden>
        {seg(e.for, "bg-gain")}{seg(e.mixed, "bg-attention")}{seg(e.against, "bg-loss")}{seg(e.untested ?? 0, "bg-muted-foreground/40")}
      </div>
      <p className="text-muted-foreground text-xs">
        {e.for} for · {e.mixed} mixed · {e.against} against{e.untested ? ` · ${e.untested} untested` : ""}
      </p>
    </div>
  );
}

function IdeaCard({ idea, onOpen }: { idea: Idea; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="bg-card hover:bg-accent/40 focus-visible:ring-ring/50 flex flex-col gap-3 rounded-xl border p-4 text-left shadow-xs transition-colors outline-none focus-visible:ring-[3px]">
      <div className="flex items-center gap-2">
        <span className="font-medium">{idea.name}</span>
        <Badge variant="secondary" className="font-mono">{idea.code}</Badge>
        {idea.cost !== "—" && <span className="text-muted-foreground ml-auto text-xs" title="Effort to test">effort {idea.cost}</span>}
      </div>
      <p className="text-muted-foreground line-clamp-2 text-sm">{idea.idea}</p>
      {idea.evidence && <Evidence e={idea.evidence} />}
      {idea.exam?.kind === "paper" && idea.exam.of ? (
        <div className="flex flex-col gap-1.5">
          <Progress value={(idea.exam.done! / idea.exam.of) * 100} className="h-1.5" />
          <p className="text-muted-foreground text-xs num">Exam: {idea.exam.done} of {idea.exam.of} {idea.exam.unit}</p>
        </div>
      ) : null}
      <p className="text-xs">
        {idea.blocked_by ? <span className="text-muted-foreground"><Lock className="mr-1 inline size-3" />{idea.blocked_by.join(" · ")}</span>
          : idea.parked ? <span className="text-muted-foreground">{idea.parked}</span>
          : <span>{idea.next}</span>}
      </p>
    </button>
  );
}

function Luck({ v, sample }: { v: number; sample?: boolean }) {
  const label = v >= 0.95 ? "likely real" : v >= 0.5 ? "could be luck" : "probably luck";
  return (
    <div className="flex items-center gap-2" title="Chance the edge is real, after counting every version tried in this family (deflated Sharpe)">
      <Progress value={v * 100} className="h-1.5 w-16" />
      <span className="num text-sm">{Math.round(v * 100)}%</span>
      <span className="text-muted-foreground hidden text-xs xl:inline">{label}</span>
      {sample && <SampleMark />}
    </div>
  );
}

function ExamLine({ idea }: { idea: Idea }) {
  const e = idea.exam;
  if (!e) return <>Not scheduled</>;
  if (e.kind === "paper") return <span className="num">{e.label}: {e.done} of {e.of} {e.unit}</span>;
  return <>{e.label}: {e.status}</>;
}

function FamilyCard({ family: f }: { family: Family }) {
  return (
    <Card className="shadow-xs">
      <CardHeader>
        <CardDescription>Family</CardDescription>
        <CardTitle>{f.name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <div className="flex items-baseline justify-between">
          <span className="text-muted-foreground">Versions tried</span>
          <span className="num font-medium">{f.tries}{f.sample?.includes("tries") && <SampleMark />}</span>
        </div>
        <Separator />
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-muted-foreground">Final exam</span>
          <span className={cn("font-medium", f.exam === "spent" ? "text-foreground" : "text-gain")}>
            {f.exam === "spent" ? `Used ${f.exam_date}` : "Still unseen"}
          </span>
        </div>
        <Separator />
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <span className="text-muted-foreground">Promotions to paper</span>
            <span className="num font-medium">{f.promotions[0]} of {f.promotions[1]}</span>
          </div>
          <Progress value={(f.promotions[0] / f.promotions[1]) * 100} className="h-1.5" />
        </div>
        {f.luck_bar_note && <p className="text-muted-foreground text-xs">{f.luck_bar_note}</p>}
      </CardContent>
    </Card>
  );
}

const GRADE: Record<Lesson["grade"], { label: string; cls: string }> = {
  supported: { label: "Holds up", cls: "text-gain" },
  mixed: { label: "Mixed", cls: "text-attention" },
  against: { label: "Doesn't hold", cls: "text-loss" },
  untested: { label: "Not enough evidence", cls: "text-muted-foreground" },
};

function LessonRow({ lesson: l }: { lesson: Lesson }) {
  const g = GRADE[l.grade];
  return (
    <li className="flex flex-col gap-1 px-6 py-3 sm:flex-row sm:items-center sm:gap-4">
      <Badge variant="outline" className={cn("w-fit shrink-0", g.cls)}>{g.label}</Badge>
      <p className="flex-1 text-sm">{l.text}</p>
      <span className="text-muted-foreground text-xs">{l.source}</span>
    </li>
  );
}

function Block({ title, icon: Icon, children }: { title: string; icon: typeof FlaskConical; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-muted-foreground flex items-center gap-2 text-xs font-medium"><Icon className="size-3.5" />{title}</h3>
      <div className="text-sm">{children}</div>
    </section>
  );
}

/** One idea's story, in the order a person would ask about it. */
function IdeaSheet({ idea, data, onClose }: { idea: Idea | null; data: AppData; onClose: () => void }) {
  const waiting = idea ? data.research.waiting.filter((w) => w.idea_id === idea.id) : [];
  return (
    <Sheet open={!!idea} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        {idea && (
          <>
            <SheetHeader className="gap-2 border-b">
              <div className="flex items-center gap-2">
                <Badge variant="secondary" className="font-mono">{idea.code}</Badge>
                {stageBadge(idea.stage)}
              </div>
              <SheetTitle className="text-lg">{idea.name}</SheetTitle>
              <SheetDescription>{idea.idea}</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-6 px-4 pb-8">
              {waiting.map((w) => (
                <div key={w.id} className="border-attention/40 bg-attention-soft rounded-lg border p-3 text-sm">
                  <p className="text-attention font-medium">Waiting on you</p>
                  <p className="mt-1">{w.step}</p>
                </div>
              ))}

              {idea.evidence && (
                <Block title="Why it might work" icon={CircleHelp}>
                  <Evidence e={idea.evidence} />
                  <p className="text-muted-foreground mt-2">{idea.evidence.note}</p>
                </Block>
              )}

              {(idea.expected || idea.stop_rule) && (
                <Block title="What you said before testing" icon={NotebookPen}>
                  {idea.expected && <p><span className="text-muted-foreground">Expected: </span>{idea.expected}</p>}
                  {idea.stop_rule && <p className="mt-1"><span className="text-muted-foreground">Stop rule: </span>{idea.stop_rule}</p>}
                </Block>
              )}

              <Block title="What happened" icon={FlaskConical}>
                {idea.result ? (
                  <div className="grid grid-cols-2 gap-3">
                    <Stat label="vs S&P 500 a year" value={`${pct1(idea.result.vs_spy)} pts`} cls={idea.result.vs_spy >= 0 ? "text-gain" : "text-loss"} sample={idea.result.sample?.includes("vs_spy")} />
                    <Stat label="Luck check" value={`${Math.round(idea.result.luck * 100)}%`} sample={idea.result.sample?.includes("luck")} />
                    <Stat label="Versions counted" value={String(idea.result.tries)} />
                    <Stat label="Lost to costs a year" value={`${(idea.result.cost_drag * 100).toFixed(1)}%`} sample={idea.result.sample?.includes("cost_drag")} />
                    <p className="text-muted-foreground col-span-2 text-xs">
                      {idea.result.window}, after costs. The luck check is the chance the edge is real once every version tried in the
                      {" "}{idea.family} family is counted; above 95% is strong, under 50% is more likely luck than skill.
                    </p>
                  </div>
                ) : (
                  <p className="text-muted-foreground">Not tested yet.</p>
                )}
              </Block>

              {idea.exam && (
                <Block title="The exam" icon={Lock}>
                  {idea.exam.kind === "paper" ? (
                    <div className="flex flex-col gap-2">
                      <p>{idea.exam.label}: <span className="num">{idea.exam.done} of {idea.exam.of}</span> {idea.exam.unit}</p>
                      <Progress value={(idea.exam.done! / idea.exam.of!) * 100} className="h-1.5" />
                      <p className="text-muted-foreground text-xs">A forward exam: the strategy trades on paper with data that didn't exist when it was designed.</p>
                    </div>
                  ) : (
                    <p>{idea.exam.label}: <span className="font-medium">{idea.exam.status}</span>{idea.exam.note && <span className="text-muted-foreground"> · {idea.exam.note}</span>}</p>
                  )}
                </Block>
              )}

              {(idea.blocked_by || idea.parked || idea.next) && (
                <Block title={idea.blocked_by ? "Blocked by" : idea.parked ? "Parked because" : "What's next"} icon={ChevronRight}>
                  {idea.blocked_by ? (
                    <ul className="list-disc pl-5">{idea.blocked_by.map((b) => <li key={b}>{b}</li>)}</ul>
                  ) : (
                    <p>{idea.parked ?? idea.next}</p>
                  )}
                </Block>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Stat({ label, value, cls, sample }: { label: string; value: string; cls?: string; sample?: boolean }) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={cn("num mt-1 text-lg font-semibold", cls)}>{value}{sample && <SampleMark />}</p>
    </div>
  );
}
