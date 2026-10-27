"""Fetch Sakila + Employees and generate TPC-H SF1 into data/raw/ (gitignored).

Safety properties (audited, see docs in the repo history):
- Only data/raw/ is ever mounted into a container (source read-only, output rw).
- GitHub sources are pinned to full commit hashes, not branches or movable tags.
  Sakila has no versioned URL, so only its SHA-256 can be recorded.
- dbgen is compiled AND run with `--network none`: its source is mounted in, so
  no step needs the network. (Only the image pull, done by the Docker daemon
  outside the container, touches the network.)
- Nothing is piped into a shell; downloads are plain HTTPS via urllib.
- data/raw/SOURCES.txt records every URL/ref, the SHA-256 of each archive, the
  dbgen version, the build image id and the SHA-256 of each generated .tbl.

dbgen determinism: dbgen takes no seed option here and none is passed. Its
random streams use seeds compiled into the source, so output for a given scale
factor and source commit should be byte-identical across runs. That is stated
from knowledge of dbgen, NOT verified: the .tbl hashes in SOURCES.txt are what
let a second run prove it.

Idempotent. Usage: python scripts/fetch_data.py
"""
import hashlib
import io
import re
import subprocess
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
ARCHIVES = RAW / "archives"
SCALE = 1
# Pinned by immutable digest (what `gcc:latest` resolved to on 2026-10-27). If absent: docker pull <this ref>.
IMAGE = "gcc@sha256:07280eb2e744f1b7a2d8d4a3894c089ea07266c109758afc388a634bd098e4eb"
# name -> (url, human-readable ref note). Hashes below are the commits those refs resolved to on 2026-10-27.
SOURCES = {
    "sakila": ("https://downloads.mysql.com/docs/sakila-db.tar.gz", "unversioned URL; SHA-256 recorded only"),
    "employees": ("https://github.com/datacharmer/test_db/archive/e5f310ac7786a2a181a7fc124973725d7aa4ce7c.zip",
                  "datacharmer/test_db tag v1.0.7 = commit e5f310ac7786a2a181a7fc124973725d7aa4ce7c"),
    "dbgen": ("https://github.com/electrum/tpch-dbgen/archive/32f1c1b92d1664dba542e927d23d86ffa57aa253.zip",
              "electrum/tpch-dbgen master = commit 32f1c1b92d1664dba542e927d23d86ffa57aa253 (2011-11-09)"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive(name: str) -> Path:
    url = SOURCES[name][0]
    path = ARCHIVES / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"GET {url}")
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    return path


def under_raw(p: Path) -> Path:
    assert p.resolve().is_relative_to(RAW.resolve()), f"refusing to mount outside data/raw: {p}"
    return p


def main() -> None:
    ARCHIVES.mkdir(parents=True, exist_ok=True)

    sakila = archive("sakila")
    if not (RAW / "sakila-db" / "sakila-data.sql").exists():
        with tarfile.open(sakila) as t:
            t.extractall(RAW, filter="data")

    employees = archive("employees")
    if not (RAW / "test_db" / "employees.sql").exists():
        with zipfile.ZipFile(employees) as z:
            z.extractall(RAW / "_emp")
        next((RAW / "_emp").iterdir()).rename(RAW / "test_db")
        (RAW / "_emp").rmdir()

    dbgen = archive("dbgen")
    src = RAW / "tpch-dbgen-src"
    if not src.exists():
        with zipfile.ZipFile(dbgen) as z:
            z.extractall(RAW / "_dbgen")
        next((RAW / "_dbgen").iterdir()).rename(src)
        (RAW / "_dbgen").rmdir()

    tpch = RAW / "tpch"
    if not (tpch / "lineitem.tbl").exists():
        tpch.mkdir(exist_ok=True)
        # Build in a copy inside the container so the source mount can be read-only.
        # 1990s K&R C: relax modern gcc. DSS_PATH makes dbgen write straight to /out.
        cmd = (f"cp -r /src /build && cd /build && make -s CC='gcc -std=gnu89 -fcommon -w' "
               f"&& DSS_PATH=/out ./dbgen -s {SCALE} -f")
        subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--security-opt", "no-new-privileges",
                        "--mount", f"type=bind,source={under_raw(src)},target=/src,readonly",
                        "--mount", f"type=bind,source={under_raw(tpch)},target=/out",
                        IMAGE, "bash", "-c", cmd], check=True)
        if not (tpch / "lineitem.tbl").exists():
            raise SystemExit("dbgen finished but /out/lineitem.tbl is missing")

    write_sources(sakila, employees, dbgen, src, tpch)
    print("data/raw ready:", sorted(p.name for p in RAW.iterdir()))


def write_sources(sakila: Path, employees: Path, dbgen: Path, src: Path, tpch: Path) -> None:
    release = (src / "release.h").read_text(errors="replace") if (src / "release.h").exists() else ""
    versions = re.findall(r"#define\s+(VERSION|RELEASE|PATCH|BUILD)\s+(\S+)", release)
    image_id = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                              capture_output=True, text=True).stdout.strip() or "unknown"
    lines = [f"# generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by scripts/fetch_data.py",
             "[archives]"]
    for name, path in (("sakila", sakila), ("employees", employees), ("dbgen", dbgen)):
        lines += [f"{name}: {SOURCES[name][0]}", f"  ref: {SOURCES[name][1]}", f"  sha256: {sha256(path)}"]
    lines += ["[dbgen]", f"  release.h: {versions or 'not found'}", f"  build image: {IMAGE} id={image_id}",
              f"  invocation: dbgen -s {SCALE} -f   (no seed option; compiled-in seeds; container --network none)",
              "[generated tpch .tbl sha256]"]
    lines += [f"  {p.name}: {sha256(p)}" for p in sorted(tpch.glob("*.tbl"))]
    (RAW / "SOURCES.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
