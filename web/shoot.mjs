// Screenshots for review: node shoot.mjs [name=query ...]. Output in shots/ (gitignored).
import { chromium } from "playwright-core";
import { mkdirSync, readdirSync, existsSync } from "node:fs";

const root = "/opt/pw-browsers";
const dir = readdirSync(root).find((d) => d.startsWith("chromium-"));
const exe = [`${root}/${dir}/chrome-linux/chrome`, `${root}/${dir}/chrome-linux64/chrome`].find(existsSync);
const base = process.env.URL ?? "http://127.0.0.1:5173/";
const jobs = process.argv.slice(2).length ? process.argv.slice(2) : ["overview="];
mkdirSync("shots", { recursive: true });
const browser = await chromium.launch({ executablePath: exe });
for (const job of jobs) {
  const i = job.indexOf("="); const name = job.slice(0, i), q = job.slice(i + 1);
  for (const [vp, opts] of [
    ["desktop", { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 }],
    ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }],
  ]) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && console.log("console:", m.text()));
    await page.goto(base + (q ? `?${q}` : ""));
    await page.waitForTimeout(900);
    if (vp === "desktop") await page.screenshot({ path: `shots/${name}-${vp}.png`, fullPage: true });
    else {
      await page.screenshot({ path: `shots/${name}-${vp}.png` });
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(200);
      await page.screenshot({ path: `shots/${name}-${vp}-2.png` });
    }
    await ctx.close();
  }
}
await browser.close();
console.log("ok");
