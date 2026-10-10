import { useLayoutEffect, useRef, useState } from "react";
import { ChevronRight, CircleHelp, FlaskConical, Lock, NotebookPen } from "lucide-react";
import type { AppData, Idea, Lesson, Stage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { shortDate } from "@/lib/format";
import { Page, RailHead, Section } from "@/components/Shell";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { LuckInfo } from "@/components/LuckInfo";
import { EvidenceTally, ExamSteps, LuckScale, Slots } from "@/components/Visuals";

const STAGES: { key: Stage; label: string }[] = [
  { key: "on_paper", label: "On paper" },
  { key: "ready", label: "Ready to test" },
  { key: "blocked", label: "Blocked" },
  { key: "exploring", label: "Exploring" },
  { key: "parked", label: "Parked" },
];

const pct1 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
const SampleMark = () => <span className="text-muted-foreground ml-1 text-[10px]" title="Sample value">sample</span>;

/**
 * Research in direction E: the results as a picture (return vs the S&P
 * against the luck check), what needs you, the ideas as short rows; detail
 * opens on tap. A side rail holds the honesty budget and lessons.
 */
export function Research({ data }: { data: AppData }) {
  const r = data.research;
  const [open, setOpen] = useState<Idea | null>(null);
  const byId = (id?: string) => r.ideas.find((i) => i.id === id) ?? null;
  const waitingIds = new Set(r.waiting.map((w) => w.idea_id));

  return (
    <Page rail={<Rail data={data} onOpen={(id) => setOpen(byId(id))} />}>
      <p className="text-muted-foreground">Tested ideas</p>
      <p className="mt-0.5 flex items-center gap-3 text-[26px] font-medium tracking-[-0.02em] sm:text-[30px]">Could any of it be luck? <LuckInfo /></p>
      <ResultsMap ideas={r.ideas.filter((i) => i.result)} onOpen={setOpen} />

      <Section title="Needs you" note={<span className="num text-attention">{r.waiting.length}</span>}>
        <ul className="border-t">
          {r.waiting.map((w) => {
            const idea = byId(w.idea_id);
            return (
              <li key={w.id} className="border-b">
                <button
                  disabled={!idea}
                  onClick={() => idea && setOpen(idea)}
                  className="hover:bg-raised -mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-3 text-left disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <span className="min-w-0 flex-1 font-medium">{w.title}</span>
                  <span className="text-muted-foreground shrink-0 text-xs">{w.effort}</span>
                  <ChevronRight className={cn("text-muted-foreground size-4 shrink-0", !idea && "invisible")} />
                </button>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="Ideas" note={`${r.ideas.length} in the backlog`}>
        {STAGES.map((st) => {
          const ideas = r.ideas.filter((i) => i.stage === st.key);
          if (!ideas.length) return null;
          return (
            <div key={st.key} className="mt-5 first:mt-0">
              <h3 className="text-muted-foreground mb-1 text-[13px]">{st.label} <span className="num">{ideas.length}</span></h3>
              <ul className="border-t">
                {ideas.map((i) => (
                  <li key={i.id} className="border-b">
                    <button onClick={() => setOpen(i)} className="hover:bg-raised -mx-2 grid w-[calc(100%+1rem)] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 rounded-md px-2 py-3 text-left sm:grid-cols-[minmax(0,1fr)_auto_minmax(150px,200px)]">
                      <span className="font-medium">{i.name}</span>
                      <span className="col-start-1 row-start-2 sm:col-start-2 sm:row-start-1">{i.evidence ? <EvidenceTally e={i.evidence} showVerdict={false} /> : <span className="text-muted-foreground text-xs">no evidence yet</span>}</span>
                      <span className="col-start-2 row-span-2 row-start-1 justify-self-end text-right text-[13px] sm:col-start-3 sm:row-span-1 sm:w-full sm:text-left">
                        <Status idea={i} waiting={waitingIds.has(i.id)} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </Section>

      <IdeaSheet idea={open} data={data} onClose={() => setOpen(null)} />
    </Page>
  );
}

/** The luck check on equal-height zones: under 50%, 50 to 95%, 95% and up. Returns 0..1 from the bottom. */
function zoneY(v: number) {
  if (v < 0.5) return v / 0.5 / 3;
  if (v < 0.95) return 1 / 3 + (v - 0.5) / 0.45 / 3;
  return 2 / 3 + (v - 0.95) / 0.05 / 3;
}

/**
 * Each tested idea as a dot: left to right its yearly return against the
 * S&P after costs, bottom to top its luck check. The zones get equal height
 * so "likely real" is readable; labels sit on the side with room and are
 * nudged apart when two ideas land close together.
 */
function ResultsMap({ ideas, onOpen }: { ideas: Idea[]; onOpen: (i: Idea) => void }) {
  const H = typeof window !== "undefined" && window.innerWidth < 640 ? 240 : 280;
  const X = 0.02; // the axis runs from −2 to +2 points a year
  const xOf = (v: number) => ((Math.max(-X, Math.min(X, v)) + X) / (2 * X)) * 100;
  const plot = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  useLayoutEffect(() => {
    const el = plot.current!;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const dots = ideas.map((i) => ({ i, x: (xOf(i.result!.vs_spy) / 100) * W, y: H - zoneY(i.result!.luck) * H }));
  const labels = placeLabels(dots.map((d) => ({ x: d.x, y: d.y, w: (d.i.name.length + 6) * 7.4 + 10 })), W, H);
  const zones = [
    { label: "Likely real", sub: "95%+", cls: "bg-gain/10" },
    { label: "Could be luck", sub: "50–95%", cls: "bg-attention/[0.06]" },
    { label: "Probably luck", sub: "under 50%", cls: "bg-loss/[0.07]" },
  ];
  return (
    <div className="mt-6">
      <div className="flex">
        <div className="text-muted-foreground flex w-[84px] shrink-0 flex-col text-xs sm:w-24" style={{ height: H }} aria-hidden>
          {zones.map((z) => (
            <div key={z.label} className="flex flex-1 flex-col justify-center pr-2">
              <span className="text-foreground/80">{z.label}</span>
              <span className="num">{z.sub}</span>
            </div>
          ))}
        </div>
        <div
          ref={plot}
          className="relative min-w-0 flex-1"
          style={{ height: H }}
          role="img"
          aria-label={ideas.map((i) => `${i.name}: ${pct1(i.result!.vs_spy)} points a year, luck check ${Math.round(i.result!.luck * 100)}%`).join("; ")}
        >
          {zones.map((z, k) => (
            <div key={z.label} className={cn("absolute inset-x-0 h-1/3", z.cls, k > 0 && "border-t border-dashed")} style={{ top: `${(k * 100) / 3}%` }} />
          ))}
          <span className="bg-ring/60 absolute inset-y-0 w-px" style={{ left: "50%" }} aria-hidden />
          {dots.map((p, k) => {
            const res = p.i.result!;
            const l = labels[k];
            return (
              <span key={p.i.id}>
                <button
                  onClick={() => onOpen(p.i)}
                  aria-label={`Open ${p.i.name}`}
                  className={cn("ring-background absolute size-3 rounded-full ring-2", res.vs_spy >= 0 ? "bg-gain" : "bg-loss")}
                  style={{ left: p.x, top: p.y, transform: "translate(-50%, -50%)" }}
                />
                <button
                  onClick={() => onOpen(p.i)}
                  className="bg-background/85 absolute -translate-y-1/2 rounded px-1 text-[13px] whitespace-nowrap hover:underline"
                  style={{ left: l.x, top: l.y }}
                >
                  {p.i.name} <span className={cn("num text-xs", res.vs_spy >= 0 ? "text-gain" : "text-loss")}>{pct1(res.vs_spy)}</span>
                </button>
              </span>
            );
          })}
        </div>
      </div>
      <div className="num text-muted-foreground mt-1.5 ml-[84px] flex justify-between text-xs sm:ml-24">
        <span>−2 pts</span>
        <span className="font-sans">vs S&amp;P a year, after costs</span>
        <span>+2 pts</span>
      </div>
      {ideas.some((i) => i.result!.sample?.length) && (
        <p className="text-muted-foreground mt-2 text-xs">Returns and some luck checks here are sample values; monthly momentum's 73% is real.</p>
      )}
    </div>
  );
}

/**
 * Puts each label beside its dot without covering another label or dot and
 * without leaving the plot: try right, then left, then step up and down.
 */
function placeLabels(dots: { x: number; y: number; w: number }[], W: number, H: number) {
  const LH = 20, R = 8;
  const boxes: { x: number; y: number; w: number }[] = [];
  const hits = (b: { x: number; y: number; w: number }) =>
    boxes.some((o) => b.x < o.x + o.w && o.x < b.x + b.w && Math.abs(b.y - o.y) < LH) ||
    dots.some((d) => d.x + R > b.x && d.x - R < b.x + b.w && Math.abs(d.y - b.y) < LH / 2 + R);
  const order = dots.map((d, k) => ({ d, k })).sort((a, b) => a.d.y - b.d.y);
  const out: { x: number; y: number }[] = [];
  for (const { d, k } of order) {
    let best: { x: number; y: number; w: number } | null = null;
    for (const dy of [0, -22, 22, -44, 44, -66, 66, -88, 88]) {
      const centred = Math.max(0, Math.min(W - d.w, d.x - d.w / 2));
      for (const x of dy === 0 ? [d.x + 10, d.x - 10 - d.w] : [d.x + 10, d.x - 10 - d.w, centred]) {
        const b = { x, y: d.y + dy, w: d.w };
        if (x < 0 || x + d.w > W || b.y < LH / 2 || b.y > H - LH / 2 || hits(b)) continue;
        best = b; break;
      }
      if (best) break;
    }
    best ??= { x: Math.max(0, Math.min(W - d.w, d.x + 10)), y: d.y, w: d.w };
    boxes.push(best);
    out[k] = { x: best.x, y: best.y };
  }
  return out;
}

/** One status per idea, in priority order. */
function Status({ idea, waiting }: { idea: Idea; waiting: boolean }) {
  if (waiting) return <span className="text-attention font-medium">Waiting on you</span>;
  if (idea.blocked_by) return <span className="text-muted-foreground inline-flex items-center gap-1"><Lock className="size-3" />Blocked</span>;
  if (idea.parked) return <span className="text-muted-foreground">Parked</span>;
  if (idea.exam?.kind === "paper" && idea.exam.of) return <span className="block w-full min-w-[120px]"><ExamSteps done={idea.exam.done!} of={idea.exam.of} unit={idea.exam.unit!} /></span>;
  if (idea.exam?.kind === "holdout") return <span className="text-muted-foreground">Exam on hold</span>;
  return <span className="text-muted-foreground">Later</span>;
}

function Rail({ data, onOpen }: { data: AppData; onOpen: (id: string) => void }) {
  const r = data.research;
  return (
    <>
      <RailHead>Honesty budget</RailHead>
      {r.families.map((f) => (
        <div key={f.id} className="flex items-center gap-3 border-b py-3 last-of-type:border-0">
          <span className="min-w-0 flex-1">
            <span className="block font-medium">{f.name}</span>
            <span className="num text-muted-foreground block text-xs">{f.tries} tried, exam {f.exam === "spent" ? `used ${shortDate(f.exam_date!)}` : "unseen"}</span>
          </span>
          <span className="text-right">
            <Slots used={f.promotions[0]} of={f.promotions[1]} label="promotions to paper" />
            <span className="text-muted-foreground mt-1 block text-xs">to paper</span>
          </span>
        </div>
      ))}
      <p className="text-muted-foreground mt-2 text-xs">Every version tried raises the bar for the next one. Each family gets one final exam on data nobody has seen.</p>

      <RailHead>Lessons</RailHead>
      <button onClick={() => onOpen("h1")} className="hover:bg-raised -mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md border-b px-2 py-3 text-left">
        <span className="min-w-0 flex-1">
          <span className="block font-medium">None of your own yet</span>
          <span className="text-muted-foreground block text-xs">Monthly momentum is ready to write up</span>
        </span>
        <ChevronRight className="text-muted-foreground size-4" />
      </button>
      <ul>{r.lessons.map((l) => <LessonRow key={l.id} lesson={l} />)}</ul>
    </>
  );
}

function LessonRow({ lesson: l }: { lesson: Lesson }) {
  const g = GRADE[l.grade];
  return (
    <li className="border-b py-3 last:border-0">
      <span className={cn("block text-xs font-medium", g.cls)}>{g.label}</span>
      <span className="block text-[13px]">{l.text}</span>
    </li>
  );
}

const GRADE: Record<Lesson["grade"], { label: string; cls: string }> = {
  supported: { label: "Holds up", cls: "text-gain" },
  mixed: { label: "Mixed", cls: "text-attention" },
  against: { label: "Doesn't hold", cls: "text-loss" },
  untested: { label: "Not enough evidence", cls: "text-muted-foreground" },
};

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
            <SheetHeader className="gap-1 border-b">
              <p className="text-muted-foreground text-sm">{STAGES.find((x) => x.key === idea.stage)?.label}</p>
              <SheetTitle className="text-lg">{idea.name}</SheetTitle>
              <SheetDescription>{idea.idea}</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-6 px-4 pb-8">
              {waiting.map((w) => (
                <div key={w.id} className="bg-attention-soft rounded-lg p-3 text-sm">
                  <p className="text-attention font-medium">Waiting on you: {w.step.charAt(0).toLowerCase() + w.step.slice(1)}</p>
                  <p className="text-muted-foreground mt-1">{w.effort}</p>
                </div>
              ))}

              {idea.evidence && (
                <Block title="Published evidence" icon={CircleHelp}>
                  <EvidenceTally e={idea.evidence} />
                  <p className="text-muted-foreground mt-1">{idea.evidence.note}</p>
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
                    <Stat label="Versions counted" value={String(idea.result.tries)} />
                    <Stat label="Lost to costs a year" value={`${(idea.result.cost_drag * 100).toFixed(1)}%`} sample={idea.result.sample?.includes("cost_drag")} />
                    <div className="col-span-2 rounded-lg border p-3">
                      <p className="text-muted-foreground mb-2 flex items-center gap-2 text-xs">Luck check{idea.result.sample?.includes("luck") && <SampleMark />}<LuckInfo idea={idea} /></p>
                      <LuckScale v={idea.result.luck} />
                    </div>
                    <p className="text-muted-foreground col-span-2 text-xs">
                      {idea.result.window}, after costs. The luck check is the chance the edge is real once every version tried in the
                      {" "}{idea.family} family is counted.
                    </p>
                  </div>
                ) : (
                  <p className="text-muted-foreground">Not tested yet.</p>
                )}
              </Block>

              {idea.exam && (
                <Block title="The exam" icon={Lock}>
                  {idea.exam.kind === "paper" ? (
                    <div className="flex flex-col gap-1">
                      <p>{idea.exam.label}</p>
                      <ExamSteps done={idea.exam.done!} of={idea.exam.of!} unit={idea.exam.unit!} />
                      <p className="text-muted-foreground text-xs">The strategy trades on paper with data that didn't exist when it was designed.</p>
                    </div>
                  ) : (
                    <p>{idea.exam.label}: <span className="font-medium">{idea.exam.status}</span>{idea.exam.note && <span className="text-muted-foreground"> · {idea.exam.note}</span>}</p>
                  )}
                </Block>
              )}

              {(idea.blocked_by || idea.parked || (idea.next && !waiting.length)) && (
                <Block title={idea.blocked_by ? "Blocked by" : idea.parked ? "Parked because" : "What's next"} icon={ChevronRight}>
                  {idea.blocked_by ? (
                    <ul className="list-disc pl-5">{idea.blocked_by.map((b) => <li key={b}>{b}</li>)}</ul>
                  ) : (
                    <p>{idea.parked ?? idea.next}</p>
                  )}
                </Block>
              )}
              <p className="text-muted-foreground border-t pt-4 text-xs">{idea.code} in the backlog, {idea.family} family</p>
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
