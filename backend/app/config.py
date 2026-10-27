import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

# .env lives at the repo root, one level above backend/.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var {name}; copy .env.example to .env and fill it in.")
    return value


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def _db_config(user_var: str, password_var: str) -> DBConfig:
    return DBConfig(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=_require("MYSQL_DATABASE"),
        user=_require(user_var),
        password=_require(password_var),
    )


ADMIN_DB = _db_config("OPTIMIZER_ADMIN_USER", "OPTIMIZER_ADMIN_PASSWORD")
READONLY_DB = _db_config("OPTIMIZER_READONLY_USER", "OPTIMIZER_READONLY_PASSWORD")


# config/run.yaml holds execution settings shared by all arms (see the file for rationale).
RUN_CONFIG = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "run.yaml").read_text())
QUERY_TIMEOUT_S: int = RUN_CONFIG["query_timeout_s"]
