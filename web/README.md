# TradePartner web (prototype)

Design prototype for the end-user app. React + TypeScript + Vite on shadcn/ui (Tailwind v4, Radix), charts by Lightweight Charts, fed by one sample file. Design notes: [docs/research/ui/](../docs/research/ui/README.md).

```bash
npm install
npm run dev            # http://127.0.0.1:5173  (#research for Research; ?state=alert|stopped|loading|error|empty; ?theme=light)
npm run build          # typecheck + production build
node sample/generate.mjs   # regenerate sample/app-data.json (deterministic, SAMPLE values)
node shoot.mjs         # desktop + phone screenshots into shots/ (needs the dev server)
```
