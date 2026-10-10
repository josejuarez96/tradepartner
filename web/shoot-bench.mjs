// Workstation view of the trial for review: the finished run, one rebalance mid-run (moved with the keyboard on the scrubber),
// and the month grid pointing the chart. Run: node shoot-bench.mjs [query] (needs the dev server). Output in shots/.
import { launch } from "./shoot-browser.mjs";
const q = process.argv[2] ?? "", tag = q ? "-" + q.replace(/[^a-z]/g, "") : "";
const b = await launch();
for (const [vp, opts] of [["desktop", { viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2 }], ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }]]) {
  const ctx = await b.newContext(opts); const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  await p.goto(`http://127.0.0.1:5173/${q ? "?" + q : ""}#strategies/h1-momentum-12-1/bench`); await p.waitForTimeout(1200);
  await p.screenshot({ path: `shots/bench${tag}-${vp}.png`, fullPage: vp === "desktop" });
  const s = p.getByRole("slider", { name: "Rebalance" }).filter({ visible: true }).first();
  await s.focus(); await p.keyboard.press("Home"); for (let i = 0; i < 6; i++) await p.keyboard.press("ArrowRight");
  await p.waitForTimeout(400);
  await p.screenshot({ path: `shots/bench${tag}-step-${vp}.png`, fullPage: vp === "desktop" });
  if (vp === "desktop") {
    await p.keyboard.press("End"); await p.waitForTimeout(300);
    await p.getByRole("cell", { name: "+1.8" }).first().hover(); await p.waitForTimeout(400);
    await p.screenshot({ path: `shots/bench${tag}-linked-desktop.png` });
  }
  await ctx.close();
}
await b.close(); console.log("ok");
