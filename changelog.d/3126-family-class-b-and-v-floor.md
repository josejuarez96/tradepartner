- Lab: FAMILIES/FAMILY_PARENTS changes are class B; N counts distinct return series (identical reruns once) and V is floored only where undefined, at the window null (#3126, #3127)
### Changed
- Lab: `config_backtest.py` (FAMILIES, FAMILY_PARENTS) is class B on the merge train and the PR template asks for family relatedness; N counts an identical rerun once, and only an undefined V is floored, at the larger of 0.04 and the window's null variance 12/T (#3126, #3127).
