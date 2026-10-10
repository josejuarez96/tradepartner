# TradePartner web (prototype)

Design prototype for the end-user app. React + TypeScript + Vite on shadcn/ui (Tailwind v4, Radix), charts by Lightweight Charts, fed by two sample files (`sample/app-data.json`, `sample/lab.json`). Design notes: [docs/research/ui/HANDOFF.md](../docs/research/ui/HANDOFF.md).

```bash
npm install
npm run dev            # http://127.0.0.1:5173  (#today, #strategies, #books; ?state=alert|calm|stopped|loading|error|empty; ?theme=light)
npm run build          # typecheck + production build
node sample/generate.mjs   # regenerate sample/app-data.json (deterministic, SAMPLE values)
node shoot.mjs         # desktop + phone screenshots into shots/ (needs the dev server)
```
