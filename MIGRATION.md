# Moving to a normal Healthchecks deployment

lambdachecks is for the long tail: tens of jobs, idle most of the time, ≈ $0. When you outgrow it (many pings per
second, alerts within seconds), it is the same Healthchecks release, so moving is a change of host:

1. Run Healthchecks v5.0.3 the usual way (its Docker image, or gunicorn on a VM) with PostgreSQL.
2. Copy the data: download `db/healthchecks.sqlite` from the stack's S3 bucket (a plain SQLite file) and load it with
   `manage.py dumpdata` / `loaddata`, or start fresh and recreate the checks.
3. Run `manage.py sendalerts` as a daemon (instead of the alerts Lambda), point your ping URLs at the new host.
4. Delete the stack: `terraform -chdir=terraform destroy -var-file=real-lambdachecks.json` (it deletes the bucket
   and the database with it: copy the file out first).

What you gain: alerts within seconds, no write conflicts between concurrent pings. What you lose: the ≈ $0 idle bill.
