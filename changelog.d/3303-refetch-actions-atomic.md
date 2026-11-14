- refetch-actions: a real run refuses the whole run before writing if any month would refuse a collision (#3303, PR #3309)
### Added
- refetch-actions real run does a rolled-back dry pass over every month first; an uncovered collision in any month writes nothing in any month (#3303)
