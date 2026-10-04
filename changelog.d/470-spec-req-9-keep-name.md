- Documented req 9's keep_name-on-unheld behaviour and override precedence/consumption in the paper-trading spec (#470).
### Changed
- docs(paper-trading): req 9 documents keep_name on an unheld name (drifted weight 0, no buy) and override precedence (forced exit, then skip_delisted, then override) and non-consumption when a name is neither held nor targeted
