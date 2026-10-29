# Changelog

## 0.1.0 — 2026-10-29

Healthchecks v5.0.3, unchanged, on AWS Lambda: the web Lambda + Function URL (WSGI adapter), the database s3rds
v0.2.1 deployed in the same stack (its server as a Lambda + a private S3 bucket, `"ENGINE": "s3rds.sqlite"`), first-run setup in the browser (migrate, first account;
guarded by the deployment's secret key), `manage.py` through the manage function, password login. Built for arm64 and x86_64 in AWS's Lambda Python 3.13 image.

Known limits: small scale (tens of checks; every write uploads the database file, s3rds has one writer). A busy
s3rds writer (HTTP 429) is retried for ~10 s by a small wrapper around s3rds 0.2.1's client, until s3rds does it
itself. Not yet: the alerts Lambda (`sendalerts` from EventBridge), email, `sendreports` and pruning schedules,
the comparison suite (tests/compat/TASKS.md 1–4).
