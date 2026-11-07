- #3076 (#3053 item 6) PR 2: universe.exclude_issuer_classes (live default bdc, foreign, mlp; table default [] so no stored registration moves) excludes tagged classes in rule 1
### Added
- `universe.exclude_issuer_classes`: rule 1 excludes BDCs, MLPs and foreign filers by `issuer_class` by default; `[]` is the old rule and every registration stored before it reads `[]` (#3076)
