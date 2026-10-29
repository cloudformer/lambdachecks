# lambdachecks — kickoff brief

Read this together with CLAUDE.md. CLAUDE.md is the series template, written for infra-serverless projects
(an AWS service rebuilt behind its standard client); this project is **app-serverless**, so the differences
below override it where they conflict. Delete this file once its content lives in CLAUDE.md / README.

## What it is

[Healthchecks](https://github.com/healthchecks/healthchecks) (cron / scheduled-job monitoring, Django) running
**unchanged** on AWS Lambda, ≈ $0 when idle. Its database is **s3rds** (github.com/cloudformer/s3rds), the
series' serverless RDS, through s3rds' Django backend (`"ENGINE": "s3rds.sqlite"`, s3rds ≥ 0.2.1).
Users keep everything Healthchecks has: ping URLs (`curl https://<url>/ping/<uuid>`), the web UI, the API —
only the host changes.

Category in SERIES.md (template repo): app-serverless, next to lambdavault (the official Vault binary on Lambda).

## Decisions already made (with Frank)

- **Healthchecks pinned to v5.0.3** (latest release, 2026-10-27). It pins Django 6.1.2 — exactly the version
  s3rds' Django backend is verified with (s3rds tests/compat/test_django.py: 16 features identical to Django's
  SQLite backend, 5 refused by design).
- **No change to Healthchecks' code.** Configuration goes in `hc/local_settings.py` (Healthchecks imports it at
  the end of hc/settings.py if it exists).
- **The database is not deployed here.** Terraform takes an existing s3rds deployment as variables (Function URL
  host, user, password) and uses a database named `healthchecks` in it. One s3rds can serve several apps; its
  console shows Healthchecks' tables.
- **Repo name lambdachecks; nothing goes to PyPI yet** (the name is not final until Frank says so; a PyPI name can
  never be renamed). The template's `client/` is a placeholder: remove it for 0.1.0 (app-serverless ships no
  driver), and do not push a `v*` tag until publish.yml is adjusted accordingly.

## Source scan of Healthchecks (done; s3rds refuses none of it)

- No `__regex`, no Trunc / Extract / `__date`, no MD5 / LPad / StdDev / `order_by("?")`, no duration arithmetic in
  queries. Time zones are converted in Python (`astimezone(ZoneInfo(...))`), never in SQL.
- Raw SQL: only `SELECT 1` (health). Site search uses its own bundled `search.db` (local sqlite3, read-only) — fine.
- `hc/api/models.py` ping path: `transaction.atomic()` + `select_for_update()`. SQLite ignores the lock; on s3rds
  two concurrent pings to the same database can make one fail with a transaction conflict (HTTP 500) — ping
  clients retry (`curl --retry`). Same class of behaviour as Healthchecks on SQLite ("database is locked").
- 187 migrations: the first `manage.py migrate` over s3rds is the real stress test of the Django backend
  (every statement is one HTTPS call; expect minutes).
- Every ping is one write = one upload of the whole SQLite file (a few hundred ms). Fine for tens of jobs.

## What changes is the hosting, not the app

| Healthchecks normally | lambdachecks |
|---|---|
| gunicorn, always on | one Lambda + Function URL, a small WSGI adapter (Function URL event → WSGI environ → response) |
| `manage.py sendalerts` — a daemon polling the DB every 2 s | a second Lambda (same package) run by EventBridge once a minute: one pass of `handle_going_down()` / `process_one_flip()` from hc/api/management/commands/sendalerts.py, then exit. The only behavioural difference: alerts up to a minute later |
| PostgreSQL / a SQLite file | `"ENGINE": "s3rds.sqlite"` in `hc/local_settings.py` |
| static files | whitenoise, as Healthchecks does; `collectstatic` + `compress` (COMPRESS_OFFLINE) at build time |
| email (login links, alerts) | first version: password login (`createsuperuser`), alerts through webhook / Slack integrations. SES later |
| `sendreports` (monthly reports), pruning commands | later: one EventBridge schedule each |

## Deliverables of 0.1.0

1. `src/server/` (or the layout CLAUDE.md prescribes): Lambda entry points — web (WSGI adapter) and alerts (one
   sendalerts pass) — plus `local_settings.py` (ENGINE s3rds.sqlite, ALLOWED_HOSTS / SITE_ROOT from the Function
   URL, SECRET_KEY from the environment, REGISTRATION_OPEN false by default).
2. A build script (like s3rds' scripts/build_server.py): fetch Healthchecks v5.0.3, install its requirements for
   Linux (`--platform manylinux2014_x86_64` / aarch64 per `var.architecture`, `--only-binary=:all:`; pycurl,
   argon2-cffi, cryptography need wheels), add `s3rds`, run collectstatic + compress, zip. Check the 250 MB
   unzipped limit; fall back to a container image (ECR) only if it does not fit.
3. Terraform (`terraform -chdir=terraform …`): web Lambda + Function URL, alerts Lambda + EventBridge rule
   (rate 1 minute), IAM least privilege (logs only — the database is reached over HTTPS with s3rds credentials),
   variables for the s3rds endpoint / user / password and SECRET_KEY; `example.json` committed, real var files
   gitignored. Frank's var file name for this project: ask him (for s3rds it is `real-srds.json`, never assume
   `my.json`).
4. First run: `manage.py migrate` against s3rds (from the laptop or a one-off Lambda invocation), `createsuperuser`.
5. README / README.zh.md: architecture image on top (your code / cron jobs → Function URL → Lambda → s3rds;
   EventBridge → alerts Lambda), then a "switch" table (left: Healthchecks self-hosted, right: lambdachecks —
   the only change: the host), then "the server: deploy once, ≈ $0 when idle", cost comparison
   (self-hosted VM + database vs healthchecks.io plans vs lambdachecks), what differs (alerts ≤ 1 min later,
   concurrent pings may need a retry, email later), MIGRATION.md (back to a normal deployment = point
   DATABASES at PostgreSQL and run sendalerts as a daemon).

## Acceptance (Frank runs every command)

1. `terraform apply` → the UI opens at the Function URL, login works.
2. Create a check, `curl` its ping URL → the ping shows in the UI; s3rds' console shows the row.
3. Let a check go down → within ~1 minute the alerts Lambda flips it and the webhook fires.
4. Compatibility (the project's own method, CLAUDE.md principle 5): the same API calls (Healthchecks Management
   API v3 + ping endpoints) against Healthchecks v5.0.3 in Docker and against lambdachecks; results compared,
   REPORT.md generated.

## Working rules (from CLAUDE.md, repeated because they matter most)

- Discuss first; build only after "开始 / 建吧 / 可以了". Frank runs every command; give commands with absolute
  paths (`git -C /Users/frankzhang/repo-private/lambdachecks …`, `terraform -chdir=/Users/frankzhang/repo-private/lambdachecks/terraform …`).
- English everywhere except README.zh.md. Answer Frank in Chinese, short, conclusions first.
