"""The server Lambda, called in-process with stubs in place of Healthchecks and the database: the WSGI adapter
(Function URL event -> environ, WSGI response -> Function URL response), the setup page, the manage entry."""
import base64

import lambda_function as lf


def event(method="GET", path="/", query="", headers=None, body=None, cookies=None):
    e = {"requestContext": {"domainName": "abc.lambda-url.us-east-2.on.aws",
                            "http": {"method": method, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.9"}},
         "rawPath": path, "rawQueryString": query, "headers": headers or {}}
    if body is not None:
        e["body"], e["isBase64Encoded"] = base64.b64encode(body).decode(), True
    if cookies:
        e["cookies"] = cookies
    return e


def echo(environ, start_response):
    """Returns what it was given; sets two cookies and a repeated header."""
    start_response("201 Created", [("Content-Type", "text/plain"), ("Vary", "Cookie"), ("Vary", "Accept"),
                                   ("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
    seen = environ["wsgi.input"].read()
    return [f"{environ['REQUEST_METHOD']} {environ['PATH_INFO']}?{environ['QUERY_STRING']} "
            f"{environ['wsgi.url_scheme']} {environ.get('HTTP_COOKIE')} {environ.get('HTTP_X_CSRFTOKEN')} "
            f"{environ['CONTENT_TYPE']} {environ['CONTENT_LENGTH']} {environ['REMOTE_ADDR']} ".encode(), seen]


def test_request_becomes_wsgi_environ():
    resp = lf._call(echo, lf._environ(event(
        "POST", "/accounts/login/%C3%A9", "next=/projects/", body=b"email=a%40b.org",
        headers={"content-type": "application/x-www-form-urlencoded", "x-forwarded-proto": "https",
                 "x-csrftoken": "tok"}, cookies=["csrftoken=tok", "sessionid=s"])))
    text = base64.b64decode(resp["body"]).decode("utf-8")
    assert text == ("POST /accounts/login/Ã©?next=/projects/ https csrftoken=tok; sessionid=s tok "
                    "application/x-www-form-urlencoded 15 203.0.113.9 email=a%40b.org")


def test_wsgi_response_becomes_function_url_response():
    resp = lf._call(echo, lf._environ(event()))
    assert resp["statusCode"] == 201 and resp["isBase64Encoded"] is True
    assert resp["headers"] == {"content-type": "text/plain", "vary": "Cookie,Accept"}
    assert resp["cookies"] == ["a=1", "b=2"]


def test_binary_body_round_trips():
    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "font/woff2")])
        return [environ["wsgi.input"].read()]
    data = bytes(range(256))
    assert base64.b64decode(lf._call(app, lf._environ(event("PUT", body=data)))["body"]) == data


def test_manage_rejects_a_bad_payload():
    assert lf.manage_handler({}, None)["exit"] == 2
    assert lf.manage_handler({"manage": "migrate"}, None)["exit"] == 2


def setup_post(path, form):
    from urllib.parse import urlencode
    return lf._setup(event("POST", path, body=urlencode(form).encode()))


def test_setup_needs_the_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "the-key")
    monkeypatch.setattr(lf, "_progress", lambda: (0, 187))
    monkeypatch.setattr(lf, "_manage", lambda args: (_ for _ in ()).throw(AssertionError("must not run")))
    resp = setup_post("/setup/migrate", {"key": "wrong"})
    assert resp["statusCode"] == 200 and "Wrong setup key" in resp["body"] and "0 / 187" in resp["body"]
    assert "Wrong setup key" in setup_post("/setup/user", {"key": "", "email": "a@b.org", "password": "x"})["body"]
    monkeypatch.setenv("SECRET_KEY", "")
    assert not lf._key_ok("")   # no SECRET_KEY configured: nothing is accepted


def test_setup_creates_the_account_only_after_migrations(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "the-key")
    calls = []
    monkeypatch.setattr(lf, "_manage", lambda args: calls.append(args) or (0, ""))
    monkeypatch.setattr(lf, "_set_up", lambda: True)
    monkeypatch.setattr(lf, "_progress", lambda: (100, 187))
    resp = setup_post("/setup/user", {"key": "the-key", "email": "a@b.org", "password": "pw"})
    assert "Run the migrations first" in resp["body"] and not calls
    monkeypatch.setattr(lf, "_progress", lambda: (187, 187))
    resp = setup_post("/setup/user", {"key": "the-key", "email": "a@b.org", "password": "pw"})
    assert resp["statusCode"] == 303 and resp["headers"]["location"] == "/accounts/login/"
    assert calls == [["createsuperuser", "--email", "a@b.org", "--password", "pw"]]


def test_s3rds_429_is_retried_other_errors_are_not(monkeypatch):
    """A 429 (s3rds' one-writer Lambda busy) never ran: retried. Anything else is raised as before."""
    import sys
    import types

    class Error(Exception):
        def __init__(self, status):
            super().__init__(status)
            self.status = status

    calls = []

    class Connection:
        def _send(self, path, body):
            calls.append(path)
            if len(calls) < 3:
                raise Error(429)
            if path == "bad":
                raise Error(400)
            return {"ok": True}

    sqlite = types.SimpleNamespace(Connection=Connection, Error=Error)
    monkeypatch.setitem(sys.modules, "s3rds", types.SimpleNamespace(sqlite=sqlite))
    monkeypatch.setitem(sys.modules, "s3rds.sqlite", sqlite)
    monkeypatch.setattr(lf.time, "sleep", lambda s: None)
    lf._retry_throttled()
    lf._retry_throttled()   # twice: still one wrapper
    assert Connection()._send("q", "{}") == {"ok": True} and len(calls) == 3
    try:
        Connection()._send("bad", "{}")
    except Error as e:
        assert e.status == 400 and len(calls) == 4
    else:
        raise AssertionError("a 400 must not be swallowed")
