- #3464: `decision data-release accept-unreceived --name <release> --count N` lets the owner accept the open release's residual NOT RECEIVED keys after the fill with his exact count; coverage then accepts exactly those.
### Added
- `decision data-release accept-unreceived`: after the fill, list the open release's residual unreceived retracted bar keys and, with the owner's exact `--count` (optionally per `--security-id`), record them as owner-approved orphans so `coverage --record` accepts exactly them (#3464).
