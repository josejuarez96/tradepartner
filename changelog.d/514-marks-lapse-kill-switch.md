- execution.marks.lapses reports a lapsed rebalance's reason as kill_switch when the switch was engaged on any session of the catch-up period (#366 Q19(b)), not only on the session checked (#532)
### Fixed
- a lapsed rebalance missed because the kill switch engaged mid-catch-up but was released before the lapse was detected used to report catch_up_lapsed; it now reports kill_switch, naming the real cause
