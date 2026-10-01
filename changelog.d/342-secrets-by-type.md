- #342 Secrets: `config.secret_values` finds every `SecretStr` by type and `cli_record._configured_secrets` starts from it; paper construction errors and a failed trial's registry message are scrubbed
### Fixed
- Secrets: CLI output and fixtures scrub every `SecretStr` setting found by type; the paper recorder's construction errors and a failed backtest's registry message are scrubbed (#342)
