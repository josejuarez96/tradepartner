// Shared by the shoot scripts: a cloud session's Chromium under /opt/pw-browsers if
// present, else playwright's own (on a Mac: `npx playwright-core install chromium`).
import { chromium } from "playwright-core";
import { existsSync, readdirSync } from "node:fs";

export function launch() {
  const root = "/opt/pw-browsers";
  if (existsSync(root)) {
    const dir = readdirSync(root).find((d) => d.startsWith("chromium-"));
    const exe = [`${root}/${dir}/chrome-linux/chrome`, `${root}/${dir}/chrome-linux64/chrome`].find(existsSync);
    if (exe) return chromium.launch({ executablePath: exe });
  }
  return chromium.launch();
}

export const VIEWPORTS = [
  ["desktop", { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 }],
  ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }],
];
