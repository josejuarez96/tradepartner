- #394/#403/#404 Email alerts: ALERT_SMTP_HOST's port is split off so STARTTLS verifies the bare host (default 587); a bad QUIT after the send no longer fails the delivery; optional ALERT_EMAIL_FROM sender
### Added
- Optional ALERT_EMAIL_FROM sender for the email alert channel, defaulting to the SMTP login (#404)
### Fixed
- Email alerts could never pass STARTTLS hostname verification when ALERT_SMTP_HOST carried a port; a non-221 QUIT reply after a sent message was recorded as a failed delivery (#394, #403)
