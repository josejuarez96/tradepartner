- Paper collect's fill read reaches back to the broker's own submitted_at, so a fast Mac clock no longer hides fills ([#2186](https://github.com/josejuarez96/tradepartner/issues/2186)).
### Fixed
- paper collect: the fill read's lower bound now also reaches back to the broker's own `submitted_at` of the orders it collects, so a Mac clock running ahead of Alpaca's no longer hides a run's fills (#2186).
