- hypothesis/experiment/dataset register and experiment open refuse a missing store instead of creating one (#3332)
### Added
- hypothesis register, experiment register, experiment open and dataset register now exit 1 naming the path when the configured store does not exist, instead of creating a fresh empty one (#3332)
