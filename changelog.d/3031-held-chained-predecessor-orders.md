- Order path: a held chained predecessor (#821) is sent under its successor's symbol only when the reconciliation on S recorded that mapping, else skipped (#3031); the successor's dividend is the predecessor's due
### Fixed
- Paper: a held chained predecessor's sell goes out under its successor's broker symbol once the reconciliation on S recorded the mapping, instead of its old ticker; an ambiguous mapping skips the name; a successor dividend credited under the new symbol is explained on the held predecessor (#3031).
