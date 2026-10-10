// Trial page for review: the finished run and one rebalance mid-run. Run: node shoot-trial.mjs (needs the dev server). Output in shots/.
import { chromium } from "playwright-core";
import { readdirSync, existsSync } from "node:fs";
const root = "/opt/pw-browsers", dir = readdirSync(root).find((d) => d.startsWith("chromium-"));
const exe = [`${root}/${dir}/chrome-linux/chrome`, `${root}/${dir}/chrome-linux64/chrome`].find(existsSync);
const b = await chromium.launch({ executablePath: exe });
for (const [vp, opts] of [["desktop", { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 }], ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }]]) {
  const ctx = await b.newContext(opts); const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  p.on("console", (m) => m.type() === "error" && console.log("console", m.text()));
  await p.goto("http://127.0.0.1:5173/#research/trial/h1"); await p.waitForTimeout(1200);
  await p.screenshot({ path: `shots/trial-${vp}.png`, fullPage: true });
  await p.getByRole("slider", { name: "Rebalance" }).fill("6"); await p.waitForTimeout(500);
  await p.screenshot({ path: `shots/trial-step-${vp}.png`, fullPage: vp === "desktop" });
  await ctx.close();
}
await b.close(); console.log("ok");
