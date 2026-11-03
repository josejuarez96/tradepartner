- Filename listings refuse a co-registrant's copy of a combined 10-K and a non-symbol prefix, both counted ([#2942](https://github.com/josejuarez96/tradepartner/pull/2942))
### Fixed
- Filename listings no longer give a combined 10-K's ticker to co-registrant CIKs (FSN keys the filing to another filer: `filename_refused_coregistrant`) or keep filename leftovers like `ETR-12312019X10K` (`filename_refused_symbol`) (#2941)
