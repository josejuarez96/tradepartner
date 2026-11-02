- Form 4 bulk reader unquotes the data set's CSV-quoted security titles; 2017q4, 2019q1, 2019q3 and 2020q3 now pass their order sample, 2024q2 still fails on SEC owner-role misassignment (#2706)
### Fixed
- Form 4 bulk reader reads a security title the SEC wrote in CSV quoting (`"Common Stock (""Shares"")"`) as the XML's plain text; order-check markers re-key at bulk reader version 3 (#2706)
