"""Stage the two Lambda packages, one folder per architecture:

    server-<arch>   Healthchecks' release (unchanged) + src/server + its Linux dependencies
    db-<arch>       s3rds' server (src/db of the s3rds release) + its pinned SQLite engine: lambdachecks' database

    python3 scripts/build_server.py   -> terraform/.build/{server,db}-{arm64,x86_64}/
                                         (Terraform runs it itself, at plan time)

Needs Docker: dependencies are installed, and Healthchecks' static files collected + compressed (COMPRESS_OFFLINE),
inside AWS's Lambda Python images, so every package matches its runtime (oncalendar ships only as an sdist; pip on
the host cannot build it for Linux). Both architectures are built, so switching var.architecture needs no rebuild.
Prints {"server-arm64": "<dir>", ...} for Terraform's external data source. Everything is cached: a later run only
re-copies src/server, unless a release, a requirements file or local_settings.py changes.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HEALTHCHECKS = "5.0.3"   # the release lambdachecks runs; its requirements.txt pins Django 6.1.2
S3RDS = "0.2.1"          # the s3rds release: its server here, its client in src/server/requirements.txt (same version)
IMAGES = {"server": "public.ecr.aws/lambda/python:3.13",   # the runtimes in terraform/main.tf
          "db": "public.ecr.aws/lambda/python:3.12"}       # s3rds' own, the one its compatibility report verified
ARCHS = {"arm64": "linux/arm64", "x86_64": "linux/amd64"}
LIMIT = 250 * 1024 * 1024   # Lambda: unzipped package size

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "server")
BUILD = os.path.join(ROOT, "terraform", ".build")
APP = os.path.join(BUILD, f"healthchecks-{HEALTHCHECKS}")   # the release + hc/local_settings.py + static-collected
DB = os.path.join(BUILD, f"s3rds-{S3RDS}", "src", "db")     # s3rds' server, as released


def log(msg):
    print(f"build_server: {msg}", file=sys.stderr)   # stdout is Terraform's JSON


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"build_server: {' '.join(cmd[:4])} ... failed:\n{(r.stdout + r.stderr).strip()[-1500:]}")


def docker(image, arch, script, env=()):
    """script in a Lambda image for arch, terraform/.build at /build, src/server at /src (read-only)."""
    run(["docker", "run", "--rm", "--platform", ARCHS[arch], "--entrypoint", "/bin/sh",
         "-v", f"{BUILD}:/build", "-v", f"{SRC}:/src:ro", *[a for e in env for a in ("-e", e)],
         image, "-c", script])


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def stale(stamp, wanted):
    return not (os.path.isfile(stamp) and read(stamp) == wanted)


def write(stamp, wanted):
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(wanted)


def fetch(repo, tag, out):
    """A release tarball from GitHub, extracted to out."""
    if os.path.isdir(out):
        return
    log(f"downloading {repo} {tag}")
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with urllib.request.urlopen(f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz") as r, \
            tarfile.open(fileobj=r, mode="r|gz") as tar:
        tar.extractall(tmp)
    os.rename(os.path.join(tmp, os.listdir(tmp)[0]), out)
    shutil.rmtree(tmp)


def deps(kind, arch, requirements):
    """pip inside the Lambda image for kind; requirements are paths under /build or /src."""
    out = os.path.join(BUILD, f"{kind}-deps-{arch}")
    stamp = out + ".stamp"
    mounts = {"/build/": BUILD + "/", "/src/": SRC + "/"}   # container path prefix -> host path
    host = [next(h + p[len(c):] for c, h in mounts.items() if p.startswith(c)) for p in requirements]
    wanted = IMAGES[kind] + "\n" + "".join(read(p) for p in host)
    if stale(stamp, wanted):
        log(f"installing {kind} dependencies for {arch} (Docker, first time takes minutes)")
        shutil.rmtree(out, ignore_errors=True)
        # --no-compile: .pyc files would push the server zip over Lambda's 50 MB direct-upload limit
        docker(IMAGES[kind], arch, f"pip install -q --no-cache-dir --no-compile --target /build/{kind}-deps-{arch} "
                                   + " ".join(f"-r {p}" for p in requirements))
        write(stamp, wanted)
    return out


def static(arch):
    """collectstatic + compress, as Healthchecks' Dockerfile does, with lambdachecks' settings in place."""
    settings = read(os.path.join(SRC, "local_settings.py"))
    shutil.copy(os.path.join(SRC, "local_settings.py"), os.path.join(APP, "hc", "local_settings.py"))
    stamp = os.path.join(BUILD, "static.stamp")
    if stale(stamp, HEALTHCHECKS + "\n" + settings):
        log("collecting static files (Docker)")
        docker(IMAGES["server"], arch,
               f"cd /build/healthchecks-{HEALTHCHECKS} && python manage.py collectstatic --noinput -v0 "
               f"&& python manage.py compress -v0",
               env=[f"PYTHONPATH=/build/server-deps-{arch}", "SECRET_KEY=build-key"])
        write(stamp, HEALTHCHECKS + "\n" + settings)


def size(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(path) for f in files)


def stage(name, parts):
    """terraform/.build/<name>: the given folders merged, in order."""
    out = os.path.join(BUILD, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for part in parts:
        shutil.copytree(part, out, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("bin", "__pycache__", ".DS_Store", "requirements.txt"))
    if size(out) > LIMIT:
        sys.exit(f"build_server: {name} is {size(out) // 2**20} MB unzipped, over Lambda's 250 MB: "
                 "switch to a container image (ECR)")
    return out


def main():
    os.makedirs(BUILD, exist_ok=True)
    fetch("healthchecks/healthchecks", f"v{HEALTHCHECKS}", APP)
    for d in (".github", "docker", "stuff"):   # what Healthchecks' own image drops too
        shutil.rmtree(os.path.join(APP, d), ignore_errors=True)
    fetch("cloudformer/s3rds", f"v{S3RDS}", os.path.join(BUILD, f"s3rds-{S3RDS}"))
    server = {a: deps("server", a, [f"/build/healthchecks-{HEALTHCHECKS}/requirements.txt", "/src/requirements.txt"])
              for a in ARCHS}
    db = {a: deps("db", a, [f"/build/s3rds-{S3RDS}/src/db/requirements.txt"]) for a in ARCHS}
    static("arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64")   # the host's own, no emulation
    app = os.path.join(BUILD, "server-src")   # src/server without local_settings.py (it lives in hc/)
    shutil.rmtree(app, ignore_errors=True)
    os.makedirs(app)
    shutil.copy(os.path.join(SRC, "lambda_function.py"), app)
    result = {}
    for a in ARCHS:
        result[f"server-{a}"] = stage(f"server-{a}", [server[a], APP, app])
        result[f"db-{a}"] = stage(f"db-{a}", [db[a], DB])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
