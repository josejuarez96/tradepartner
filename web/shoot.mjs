// Screenshots for review: node shoot.mjs [name=query#hash ...]. Output in shots/ (gitignored).
// Desktop is full page; phone is the first screen plus a full-page shot (-full).
import { mkdirSync } from "node:fs";
import { launch, VIEWPORTS } from "./shoot-browser.mjs";

const base = process.env.URL ?? "http://127.0.0.1:5173/";
const jobs = process.argv.slice(2).length ? process.argv.slice(2) : ["today="];
mkdirSync("shots", { recursive: true });
const browser = await launch();
for (const job of jobs) {
  const i = job.indexOf("="); const name = job.slice(0, i), q = job.slice(i + 1);
  for (const [vp, opts] of VIEWPORTS) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && console.log("console:", m.text()));
    page.on("pageerror", (e) => console.log("pageerror:", e.message));
    const [qs, hash] = q.split("#");
    await page.goto(base + (qs ? `?${qs}` : "") + (hash ? `#${hash}` : ""));
    await page.waitForTimeout(900);
    if (vp === "desktop") await page.screenshot({ path: `shots/${name}-${vp}.png`, fullPage: true });
    else {
      await page.screenshot({ path: `shots/${name}-${vp}.png` });
      await page.screenshot({ path: `shots/${name}-${vp}-full.png`, fullPage: true });
    }
    await ctx.close();
  }
}
await browser.close();
console.log("ok");
