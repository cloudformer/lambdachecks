"""lambdachecks server: Healthchecks, unchanged, in one Lambda behind a Function URL.

    lambda_handler   Function URL event (payload 2.0) -> WSGI environ -> hc.wsgi.application -> response
                     until the first account exists: the setup page instead (migrate, then createsuperuser)
    manage_handler   {"manage": ["migrate", "--noinput"]} -> manage.py migrate --noinput, output returned
                     (the manage function: no URL, 15 minutes; the setup page starts migrate there)

The package (scripts/build_server.py) puts Healthchecks' release at the root next to this file, so `hc` imports
as it does in Healthchecks' own checkout; its settings hook hc/local_settings.py is src/server/local_settings.py.
"""
import base64
import hmac
import html
import io
import json
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from urllib.parse import parse_qs, unquote_to_bytes

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")

_app = None


def _retry_throttled():
    """s3rds runs one writer (its Lambda's reserved concurrency is 1): a request that arrives while another runs is
    refused by Lambda with HTTP 429 before the function starts, so it provably did not execute and is safe to send
    again. s3rds 0.2.1's client raises it at once; this retries it with backoff (about 10 s in all).
    Remove once the s3rds client does it itself."""
    from s3rds import sqlite
    if getattr(sqlite.Connection._send, "retries_429", False):
        return
    send = sqlite.Connection._send

    def _send(self, path, body):
        for delay in (0.05, 0.1, 0.2, 0.4, 0.8, 1.2, 1.6, 2.0, 2.0, 2.0):
            try:
                return send(self, path, body)
            except sqlite.Error as e:
                if e.status != 429:
                    raise
            time.sleep(delay)
        return send(self, path, body)

    _send.retries_429 = True
    sqlite.Connection._send = _send


def _application(domain):
    """Healthchecks' WSGI app, loaded on the first request. Healthchecks derives ALLOWED_HOSTS, the ping URL
    and its links from SITE_ROOT; the Function URL cannot be passed in as an environment variable (it does not
    exist before the function), so it comes from the first request. AWS sets domainName, not the caller."""
    global _app
    if _app is None:
        _retry_throttled()
        os.environ.setdefault("SITE_ROOT", "https://" + domain)
        from django.core.wsgi import get_wsgi_application
        _app = get_wsgi_application()
    return _app


def _environ(event):
    ctx = event["requestContext"]
    http = ctx["http"]
    headers = event.get("headers") or {}
    body = event.get("body") or ""
    body = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
    environ = {
        "REQUEST_METHOD": http["method"],
        "SCRIPT_NAME": "",
        "PATH_INFO": unquote_to_bytes(event.get("rawPath", "/")).decode("latin-1"),   # WSGI: decoded, latin-1
        "QUERY_STRING": event.get("rawQueryString", ""),
        "CONTENT_TYPE": headers.get("content-type", ""),
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": ctx.get("domainName", "localhost"),
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": http.get("protocol", "HTTP/1.1"),
        "REMOTE_ADDR": http.get("sourceIp", ""),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": headers.get("x-forwarded-proto", "https"),   # Django's CSRF origin check needs https
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }
    for name, value in headers.items():
        key = name.upper().replace("-", "_")
        if key not in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ["HTTP_" + key] = value
    if event.get("cookies"):   # payload 2.0 moves Cookie out of the headers
        environ["HTTP_COOKIE"] = "; ".join(event["cookies"])
    return environ


def _call(app, environ):
    """Run the WSGI app; the response in the Function URL's shape (body always base64: fonts, gzip)."""
    started = {}

    def start_response(status, response_headers, exc_info=None):
        started["status"], started["headers"] = status, response_headers

    result = app(environ, start_response)
    try:
        body = b"".join(result)
    finally:
        if hasattr(result, "close"):
            result.close()
    headers, cookies = {}, []
    for name, value in started["headers"]:
        if name.lower() == "set-cookie":
            cookies.append(value)
        else:
            key = name.lower()
            headers[key] = f"{headers[key]},{value}" if key in headers else value
    return {"statusCode": int(started["status"].split(None, 1)[0]), "headers": headers, "cookies": cookies,
            "body": base64.b64encode(body).decode(), "isBase64Encoded": True}


def lambda_handler(event, context):
    app = _application(event["requestContext"].get("domainName", "localhost"))
    if not _set_up():
        return _setup(event)
    return _call(app, _environ(event))


# ---------------- first run, in the browser: migrate, then the first account ----------------
# Until a superuser exists every page is the setup page. Each step needs the setup key (the deployment's
# SECRET_KEY, from the Terraform variable file), so nobody else can claim a fresh deployment.

_ready = False   # once true, never checked again in this container


def _query(sql):
    """One query outside Django's request cycle, on a fresh connection (a failed one is not reused)."""
    from django.db import connection
    connection.close()
    try:
        with connection.cursor() as c:
            c.execute(sql)
            return c.fetchone()
    finally:
        connection.close()


def _set_up():
    global _ready
    if not _ready:
        try:
            _ready = _query("SELECT 1 FROM auth_user WHERE is_superuser = 1 LIMIT 1") is not None
        except Exception:  # noqa: BLE001 - no database / no tables yet: not set up
            pass
    return _ready


def _progress():
    """(applied, total) migrations."""
    from django.db.migrations.loader import MigrationLoader
    total = len(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    try:
        applied = _query("SELECT COUNT(*) FROM django_migrations")[0]
    except Exception:  # noqa: BLE001 - the table comes with the first migration
        applied = 0
    return applied, total


def _key_ok(key):
    secret = os.environ.get("SECRET_KEY", "")
    return bool(secret) and hmac.compare_digest(key.encode(), secret.encode())


def _setup(event):
    method, path = event["requestContext"]["http"]["method"], event.get("rawPath", "/")
    message, refresh = "", False
    if method == "POST":
        body = event.get("body") or ""
        body = base64.b64decode(body).decode() if event.get("isBase64Encoded") else body
        form = {k: v[0] for k, v in parse_qs(body).items()}
        if not _key_ok(form.get("key", "")):
            message = "Wrong setup key: it is secret_key in your Terraform variable file."
        elif path == "/setup/migrate":
            import boto3
            boto3.client("lambda").invoke(FunctionName=os.environ["MANAGE_FUNCTION"], InvocationType="Event",
                                          Payload=json.dumps({"manage": ["migrate", "--noinput"]}).encode())
            message, refresh = "Migrations started; this page refreshes every 10 seconds.", True
        elif path == "/setup/user":
            applied, total = _progress()
            if applied < total:
                message = "Run the migrations first."
            else:
                code, output = _manage(["createsuperuser", "--email", form.get("email", ""),
                                        "--password", form.get("password", "")])
                if code == 0 and _set_up():
                    return {"statusCode": 303, "headers": {"location": "/accounts/login/"}, "body": ""}
                message = output.strip() or f"createsuperuser failed (exit {code})"
    applied, total = _progress()
    refresh = refresh or 0 < applied < total
    return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8"},
            "body": SETUP_HTML.format(
                refresh='<meta http-equiv="refresh" content="10;url=/">' if refresh else "",
                message=f'<p class="msg">{html.escape(message)}</p>' if message else "",
                applied=applied, total=total, done="done" if applied >= total else "",
                user_disabled="" if applied >= total else "disabled")}


SETUP_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>lambdachecks setup</title>
<style>
  body {{ font: 15px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; max-width: 34em;
         margin: 3em auto; padding: 0 16px; color: #0f172a; background: #fff; }}
  h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.1em; margin-top: 2em; }}
  input {{ display: block; width: 100%; box-sizing: border-box; margin: .3em 0 .8em; padding: .5em; font: inherit; }}
  button {{ padding: .5em 1.2em; font: inherit; }}
  .msg {{ background: #fef3c7; padding: .6em .8em; white-space: pre-wrap; }}
  .done {{ color: #15803d; font-weight: 600; }} .muted {{ color: #64748b; }}
</style></head><body>
<h1>lambdachecks setup</h1>
<p class="muted">Healthchecks is deployed; its database is empty. Two steps, once. The setup key is
<code>secret_key</code> in your Terraform variable file.</p>
{message}
<h2>1. Database</h2>
<p class="{done}">{applied} / {total} migrations applied</p>
<form method="post" action="/setup/migrate">
  <input type="password" name="key" placeholder="setup key" required>
  <button>Run migrations</button>
  <span class="muted">a few minutes; safe to run again, it continues where it stopped</span>
</form>
<h2>2. First account</h2>
<form method="post" action="/setup/user">
  <input type="email" name="email" placeholder="email" required {user_disabled}>
  <input type="password" name="password" placeholder="password" required {user_disabled}>
  <input type="password" name="key" placeholder="setup key" required {user_disabled}>
  <button {user_disabled}>Create the account</button>
</form>
</body></html>
"""


def _create_database():
    """lambdachecks' database in s3rds, created through s3rds' own client (an existing one is left alone),
    so nothing has to be set up on the s3rds side first."""
    from s3rds import sqlite
    db = sqlite.connect(os.environ["DB_HOST"], os.environ["DB_USER"], os.environ["DB_PASSWORD"],
                        os.environ.get("DB_NAME", "healthchecks"))
    try:
        db.create_database()
    finally:
        db.close()


def manage_handler(event, context):
    """One manage.py command, e.g. {"manage": ["createsuperuser", "--email", "me@x.org", "--password", "..."]}.
    Returns {"exit": 0, "output": "..."}; stdin is not a terminal, so nothing prompts."""
    args = event.get("manage")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        return {"exit": 2, "output": 'expected {"manage": ["<command>", "<arg>", ...]}'}
    _retry_throttled()
    _create_database()
    code, output = _manage(args)
    print(output)   # CloudWatch keeps it too
    return {"exit": code, "output": output[-20000:]}


def _manage(args):
    """manage.py <args> in this process: (exit code, output). A warm container keeps Django's connection between
    invocations; a command that failed mid-transaction would leave it broken for the next one, so each command
    starts and ends with a fresh one."""
    from django.core.management import execute_from_command_line
    from django.db import connections
    out, code = io.StringIO(), 0
    with redirect_stdout(out), redirect_stderr(out):
        try:
            connections.close_all()
            execute_from_command_line(["manage.py", *args])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except Exception:  # noqa: BLE001 - a failed command is reported, not raised
            import traceback
            traceback.print_exc()
            code = 1
        finally:
            connections.close_all()
    return code, out.getvalue()
