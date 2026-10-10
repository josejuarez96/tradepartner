import { useState } from "react";
import { ChevronRight, CircleHelp, FlaskConical, Lock, MessageSquareText, NotebookPen, Play, Scale } from "lucide-react";
import type { AppData, Idea, Lesson, Stage, WaitingItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { shortDate } from "@/lib/format";
import { Panel } from "@/components/Shell";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { EvidenceTally, ExamSteps, LuckScale, Slots } from "@/components/Visuals";

const STAGES: { key: Stage; label: string; hint: string }[] = [
  { key: "on_paper", label: "On paper", hint: "trading practice money while it takes its exam" },
  { key: "ready", label: "Ready to test", hint: "written and built, waiting for a run" },
  { key: "blocked", label: "Blocked", hint: "can't move until something else lands" },
  { key: "exploring", label: "Exploring", hint: "needs data or work first" },
  { key: "parked", label: "Parked", hint: "set aside on purpose" },
];

const KIND_ICON: Record<WaitingItem["kind"], typeof Play> = { run: Play, answer: MessageSquareText, decide: Scale, record: NotebookPen };

const pct1 = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
const SampleMark = () => <span className="text-muted-foreground ml-1 text-[10px]" title="Sample value">sample</span>;

/**
 * Research, organised around the owner's questions: what's waiting on me,
 * what's on the go, did it work and could it be luck, what have I used up,
 * what have I learned. Each idea opens one story from why to what's next.
 */
export function Research({ data }: { data: AppData }) {
  const r = data.research;
  const [open, setOpen] = useState<Idea | null>(null);
  const byId = (id?: string) => r.ideas.find((i) => i.id === id) ?? null;
  const waitingIds = new Set(r.waiting.map((w) => w.idea_id));
  const tested = r.ideas.filter((i) => i.result);

  return (
    <div className="bg-border grid gap-px lg:grid-cols-[minmax(0,1.45fr)_minmax(360px,1fr)]">
      <div className="bg-border flex flex-col gap-px">
        <Panel title="Waiting on you" aside={<span className="num text-attention">{r.waiting.length}</span>}>
          <ul>
            {r.waiting.map((w) => {
              const Icon = KIND_ICON[w.kind];
              const idea = byId(w.idea_id);
              const body = (
                <>
                  <Icon className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{w.title}</span>
                    <span className="text-muted-foreground block">{w.why}</span>
                    <span className="text-muted-foreground mt-0.5 block text-xs sm:hidden">{w.effort}</span>
                  </span>
                  <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">{w.effort}</span>
                  {idea && <ChevronRight className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />}
                </>
              );
              return (
                <li key={w.id} className="border-b last:border-0">
                  {idea ? (
                    <button onClick={() => setOpen(idea)} className="hover:bg-raised -mx-2 flex w-[calc(100%+1rem)] items-start gap-2.5 rounded px-2 py-2.5 text-left">{body}</button>
                  ) : (
                    <div className="flex items-start gap-2.5 py-2.5">{body}</div>
                  )}
                </li>
              );
            })}
          </ul>
        </Panel>

        <Panel title="Ideas" aside={`${r.ideas.length} in the backlog`} className="flex-1">
          <table className="w-full">
            <thead className="text-muted-foreground text-xs">
              <tr className="border-b">
                <th className="py-1.5 text-left font-normal">Idea</th>
                <th className="hidden py-1.5 text-left font-normal md:table-cell">Published evidence</th>
                <th className="py-1.5 text-left font-normal">Status</th>
              </tr>
            </thead>
            {STAGES.map((s) => {
              const ideas = r.ideas.filter((i) => i.stage === s.key);
              if (!ideas.length) return null;
              return (
                <tbody key={s.key}>
                  <tr>
                    <th colSpan={3} className="pt-3 pb-1 text-left text-xs font-medium">
                      {s.label} <span className="num text-muted-foreground font-normal">{ideas.length}</span>
                      <span className="text-muted-foreground font-normal">, {s.hint}</span>
                    </th>
                  </tr>
                  {ideas.map((i) => (
                    <tr key={i.id} onClick={() => setOpen(i)} className="hover:bg-raised cursor-pointer border-b align-top">
                      <td className="py-2 pr-3">
                        <button className="text-left font-medium hover:underline" onClick={(e) => { e.stopPropagation(); setOpen(i); }}>{i.name}</button>
                        <div className="text-muted-foreground line-clamp-1 text-[11.5px]">{i.idea}</div>
                      </td>
                      <td className="hidden py-2 pr-3 md:table-cell">{i.evidence && <EvidenceTally e={i.evidence} />}</td>
                      <td className="w-[34%] py-2"><Status idea={i} waiting={waitingIds.has(i.id)} /></td>
                    </tr>
                  ))}
                </tbody>
              );
            })}
          </table>
        </Panel>
      </div>

      <div className="bg-border flex flex-col gap-px">
        <Panel title="Results" aside="in-sample, after costs">
          <table className="w-full">
            <thead className="text-muted-foreground text-xs">
              <tr className="border-b">
                <th className="py-1.5 text-left font-normal">Idea</th>
                <th className="py-1.5 text-right font-normal">vs S&amp;P / yr</th>
                <th className="py-1.5 pl-4 text-left font-normal">Luck check</th>
              </tr>
            </thead>
            <tbody>
              {tested.map((i) => (
                <tr key={i.id} onClick={() => setOpen(i)} className="hover:bg-raised cursor-pointer border-b last:border-0">
                  <td className="py-2">{i.name}<div className="text-muted-foreground num text-[11px]">{i.result!.tries} {i.result!.tries === 1 ? "version" : "versions"} counted</div></td>
                  <td className={cn("num py-2 text-right", i.result!.vs_spy >= 0 ? "text-gain" : "text-loss")}>{pct1(i.result!.vs_spy)} pts</td>
                  <td className="py-2 pl-4"><LuckScale v={i.result!.luck} compact /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-muted-foreground mt-2 text-[11px]">Some values are sample numbers; open an idea to see which.</p>
        </Panel>

        <Panel title="Honesty budget" aside="per family">
          <table className="w-full">
            <thead className="text-muted-foreground text-xs">
              <tr className="border-b">
                <th className="py-1.5 text-left font-normal">Family</th>
                <th className="py-1.5 text-right font-normal">Tried</th>
                <th className="py-1.5 pl-3 text-left font-normal">Final exam</th>
                <th className="py-1.5 text-left font-normal">Promotions</th>
              </tr>
            </thead>
            <tbody>
              {r.families.map((f) => (
                <tr key={f.id} className="border-b last:border-0">
                  <td className="py-2">{f.name}</td>
                  <td className="num py-2 text-right">{f.tries}</td>
                  <td className={cn("py-2 pl-3", f.exam === "unspent" && "text-gain")}>{f.exam === "spent" ? `Used ${shortDate(f.exam_date!)}` : "Unseen"}</td>
                  <td className="py-2"><Slots used={f.promotions[0]} of={f.promotions[1]} label="promotions" /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-muted-foreground mt-2 text-[11px]">Each version tried raises the bar for the next result; each family gets one final exam on unseen data.</p>
        </Panel>

        <Panel title="Lessons" className="flex-1">
          <div className="border-b pb-2.5">
            <p>None of your own recorded yet.</p>
            <button onClick={() => setOpen(byId("h1"))} className="text-muted-foreground hover:text-foreground mt-0.5 inline-flex items-center gap-1 text-xs">
              Monthly momentum is ready to write up<ChevronRight className="size-3" />
            </button>
          </div>
          <ul>{r.lessons.map((l) => <LessonRow key={l.id} lesson={l} />)}</ul>
        </Panel>
      </div>

      <IdeaSheet idea={open} data={data} onClose={() => setOpen(null)} />
    </div>
  );
}

/** One status per idea, in priority order: waiting on you, blocked, parked, exam progress, next. */
function Status({ idea, waiting }: { idea: Idea; waiting: boolean }) {
  if (waiting) return <span className="text-attention font-medium">Waiting on you</span>;
  if (idea.blocked_by) return <span className="text-muted-foreground"><Lock className="mr-1 inline size-3" />{idea.blocked_by[0]}{idea.blocked_by.length > 1 && ` +${idea.blocked_by.length - 1}`}</span>;
  if (idea.parked) return <span className="text-muted-foreground line-clamp-2">{idea.parked}</span>;
  if (idea.exam?.kind === "paper" && idea.exam.of) return <ExamSteps done={idea.exam.done!} of={idea.exam.of} unit={idea.exam.unit!} />;
  return <span className="text-muted-foreground">{idea.next}</span>;
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
    <li className="flex items-baseline gap-3 border-b py-2 last:border-0">
      <span className={cn("w-24 shrink-0 text-xs font-medium", g.cls)}>{g.label}</span>
      <span className="flex-1">{l.text}</span>
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
                      <p className="text-muted-foreground mb-2 text-xs">Luck check{idea.result.sample?.includes("luck") && <SampleMark />}</p>
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
