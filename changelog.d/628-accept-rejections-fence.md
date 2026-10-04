- accept_rejections static fence fails closed on unclassified Name uses; PEP 695 type params and the stale schema-version-4 ops wording fixed (#628)
### Fixed
- execution.resume's accept_rejections fence (test_accept_rejections_boundary.py) now rejects any Load of the flag outside four safe contexts, closing starred/double-starred dict and set displays, multiplied and generator displays, IfExp, name aliasing, another keyword, del, and PEP 695 type parameters; dashboard ops_page.py no longer claims a fixed schema version in its journal-not-initialised message.
