// Lab and Today interactions for review: the engine's refusal on Run, the kill switch dialog,
// and the Result's disagree form. Run: node shoot-lab.mjs (needs the dev server). Output in shots/.
import { launch, VIEWPORTS } from "./shoot-browser.mjs";
const base = "http://127.0.0.1:5173/";
const b = await launch();
for (const [vp, opts] of VIEWPORTS) {
  const ctx = await b.newContext(opts); const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  await p.goto(base + "?engine=refused_window#strategies/h1-momentum-12-1/run"); await p.waitForTimeout(900);
  await p.getByRole("button", { name: "Run trial 9" }).click(); await p.waitForTimeout(1000);
  await p.screenshot({ path: `shots/run-refusal-${vp}.png`, fullPage: vp === "desktop" });
  await p.goto(base + "#strategies/h1-momentum-12-1/run"); await p.reload(); await p.waitForTimeout(900);
  await p.getByRole("button", { name: "Run trial 9" }).click(); await p.waitForTimeout(1000);
  await p.screenshot({ path: `shots/run-queued-${vp}.png`, fullPage: vp === "desktop" });
  await p.goto(base + "#today"); await p.reload(); await p.waitForTimeout(900);
  await p.getByRole("button", { name: "Kill switch…" }).click(); await p.waitForTimeout(400);
  await p.getByText("daily", { exact: true }).last().click();
  await p.getByLabel("Reason").fill("Broker outage, want to look before it trades");
  await p.screenshot({ path: `shots/kill-${vp}.png` });
  await ctx.close();
}
await b.close(); console.log("ok");
