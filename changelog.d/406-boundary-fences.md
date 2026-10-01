- #406 #407 #418 order-path fences: backtest chains resolve import aliases, the fills SQL fence sees `FROM (fills)` and ignores comma prose, and the order fence refuses attrgetter, __getattribute__ and non-literal lookups
### Fixed
- Tests: the T50 boundary fences catch aliased-import chains into store.journal/execution (#406), parenthesised `FROM (fills)` without flagging comma prose (#407), and order calls via attrgetter, __getattribute__ or a non-literal name (#418)
