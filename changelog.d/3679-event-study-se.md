- Event-study SE: [study].se_method (hansen_hodrick default; quarter_cluster_t for new designs: quarter-of-entry cluster, CR1, t(G-1), z_eq vs floor); past studies unchanged
### Fixed
- New event studies can use a calendar-quarter cluster SE with a t(G-1) reference (null size .05 to .07 where the flat Hansen-Hodrick SE gave .08 and sometimes NaN); an absent key keeps the old statistic (#3679)
