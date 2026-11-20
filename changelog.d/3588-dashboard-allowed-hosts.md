- Dashboard and review page refuse to render without a loopback server.allowedHosts; launches pass it ([#3611](https://github.com/josejuarez96/tradepartner/pull/3611)).
### Fixed
- The dashboard and labeling review page now refuse to render unless the Host allow-list is loopback-only, closing a DNS-rebinding path to the dashboard's writes (#3588).
