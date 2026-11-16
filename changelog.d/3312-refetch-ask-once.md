- refetch-actions asks each month once: the write reuses the dry pass's fetched answers (#3312)
### Fixed
- A real refetch-actions run no longer fetches every month twice: the refuse-before-write pass keeps its answers and the write uses them (#3312)
