task: T283b (PR #2806)
- T283b owner command built: `paper true-up --break --reason` adopts one open break's broker figures under the run lock after a fresh broker read; reversal split to #2804
### Added
- `tradepartner paper true-up --break <id> --reason`: the owner adopts the broker's figures for one open reconciliation break (fresh broker read, no-write refusals, one write chunk of `owner_true_up` rows and a `trued_up` event); an acknowledged finding is not reopened while its key is unchanged (#2266 item 9)
