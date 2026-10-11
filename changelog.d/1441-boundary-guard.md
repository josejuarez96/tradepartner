- #1441: `sweep register` and `sweep run` refuse (DefaultWindowOverlapError) a sweep whose default in-sample window reads another non-oracle family's holdout months; B4 now needs the development boundary row first.
### Fixed
- `sweep register` and `sweep run` refuse a sweep whose default in-sample window reads another family's holdout months (no development boundary row), so B4 cannot practise on momentum's and profitability's exams by mistake (#1441).
