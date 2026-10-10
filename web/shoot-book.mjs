// Book detail interactions for review: stop dialog (empty reason, failure, retry), resume, holding sheet. Run: node shoot-book.mjs (needs the dev server). Output in shots/.
import { chromium } from "playwright-core";
const exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const base = "http://127.0.0.1:5173/";
const b = await chromium.launch({ executablePath: exe });
for (const [vp, opts] of [["desktop", { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 }], ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }]]) {
  const ctx = await b.newContext(opts); const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  await p.goto(base + "?state=stopfail#books/daily"); await p.waitForTimeout(900);
  await p.getByRole("button", { name: "Stop book" }).click(); await p.waitForTimeout(400);
  await p.screenshot({ path: `shots/stop-${vp}.png` });
  await p.getByRole("button", { name: "Stop daily" }).click(); await p.waitForTimeout(300);
  await p.screenshot({ path: `shots/stop-noreason-${vp}.png` });
  await p.getByLabel("Why are you stopping it?").fill("Earnings week, want to watch first");
  await p.getByRole("button", { name: "Stop daily" }).click(); await p.waitForTimeout(1000);
  await p.screenshot({ path: `shots/stop-failed-${vp}.png` });
  await p.getByRole("button", { name: "Try again" }).click(); await p.waitForTimeout(1000);
  await p.screenshot({ path: `shots/stop-done-${vp}.png` });
  await p.getByRole("button", { name: "Resume…" }).click(); await p.waitForTimeout(400);
  await p.screenshot({ path: `shots/resume-${vp}.png` });
  await p.keyboard.press("Escape"); await p.waitForTimeout(300);
  await p.getByRole("button", { name: /^TSLA/ }).click(); await p.waitForTimeout(500);
  await p.screenshot({ path: `shots/holding-${vp}.png` });
  await ctx.close();
}
await b.close(); console.log("ok");
