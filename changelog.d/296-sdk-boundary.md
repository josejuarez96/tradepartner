### Added
- Static boundary test `tests/execution/test_sdk_boundary.py`: no module outside the adapters and the paper recorder can import the alpaca-py trading client or order requests, or call `submit_order`/`cancel_order*`, so nothing bypasses the paper guard and the risk-gated wrapper (#296)
