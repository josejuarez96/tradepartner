- #1031: LLM boundary test (ii) bans urllib.request, http.client, socket, requests and aiohttp beside httpx; (c)'s host rule reads tests/.
### Changed
- LLM boundary test: (ii) refuses more network clients under tradepartner.research outside research.models, and (c) checks tests/ for the vendor host (#1031).
