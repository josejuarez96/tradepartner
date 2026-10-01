- #397 collect: the halt path's read (halt_read=True) writes no fill cursor, so the next run judges the rejections it journaled, as the docstring says
### Fixed
- Execution: rejections journaled by the halt path's read are judged again by the next run (#397)
