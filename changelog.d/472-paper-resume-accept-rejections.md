- #472 paper resume accept_rejections (the owner's --accept-rejections; CLI is T67): releases past #451's rejection-cap refusal only, journaled in resume_acceptances before reconciling; schema v8
### Added
- resume(..., accept_rejections=) for the owner's paper resume --accept-rejections: accepts only rejection-cap verdicts on runs the release would clear, records them (or []) on a new resume_acceptances row before reconciliation, and lifts no other refusal; schema version 8 adds resume_invocations.accept_rejections (#472)
