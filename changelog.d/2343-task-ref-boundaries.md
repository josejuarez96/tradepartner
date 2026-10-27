- #2343: team.py reads a task reference only as a whole id (T266a12 is no longer T266a1).
### Added
- team.py's task-reference pattern has word boundaries, so a typo like T266a12 or xT241 is not read as a task (#2343).
