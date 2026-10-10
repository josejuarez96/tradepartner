/** Reads design tokens from CSS so charts use exactly what the stylesheet defines. */
export function token(name: string, el: Element = document.documentElement): string {
  return getComputedStyle(el).getPropertyValue(`--${name}`).trim();
}

/** Calls back whenever the colour scheme flips, so charts can repaint. */
export function onSchemeChange(cb: () => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  mq.addEventListener("change", cb);
  return () => { obs.disconnect(); mq.removeEventListener("change", cb); };
}
