"""Corpus builds from EDGAR for research labeling, outside `tradepartner.research`.

A corpus module imports the EDGAR adapter and writes only under
`edgar.cache_dir/corpus/`; it imports nothing from `tradepartner.research`
and never reads the runtime store (ADR 0013 point 3, research-labeling spec
req 2 and amendment C11). `tradepartner.research.labeling.frame` reads the
corpus file by path.
"""
