- Spec #2435: SEC ownership forms (13D, Form 4) in their own tables apart from master.issuer_forms; Form 4 field contract settled; builds T305 to T308, store parts held until the shakedown span ends.
### Changed
- Specify separate point-in-time SC 13D and Form 4 event records with a Form 4 field contract, and a post-span owner migration gate (build held until the span ends) (#2435).
