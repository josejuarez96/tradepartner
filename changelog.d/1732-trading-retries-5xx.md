- Trading raw client now retries 500/502/503 and connection errors under the existing bounded retries (#1732)
### Fixed
- The paper trading client retries 500/502/503 and transport connection errors like 429/504 and timeouts, instead of halting the book on the first one; submit keeps its duplicate client_order_id read-back (#1732)
