### Fixed
- Secrets: ingestion run messages redact every configured secret (the paper keys and alert secrets were missed), the paper recorder scrubs its stderr error, and the SMTP login base64 forms are scrubbed (#334)
