- #471 Closed renamed-builtin, stored-getattribute, dict/vars-subscript and DELETE USING fence gaps in test_boundaries.py (PR #537)
### Added
- Execution: order-path fences now resolve renamed getattr/attrgetter imports, flag a stored bound `__getattribute__`, flag `__dict__`/`vars()` order-name subscripts, and catch `DELETE ... USING fills` (#471)
