from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "spiderhub"
    obey_robots: bool = True
    request_delay_seconds: float = 1.0
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3
    allow_fetcher_upgrade: bool = True
    allow_browser: bool = True
    impersonate_target: str = "chrome"
    browser_challenge_wait_seconds: float = 15.0
    browser_headless: bool = True
    browser_storage_state: str = ".spiderhub/storage_state.json"
    browser_cdp_url: str = ""
    browser_user_data_dir: str = ".spiderhub/chrome-profile"


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_settings(
    *,
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    cli_overrides: Mapping[str, object] | None = None,
) -> Settings:
    if env is None:
        load_dotenv()
    environ: Mapping[str, str] = env if env is not None else os.environ
    path = config_path if config_path is not None else Path("config.local.toml")
    raw = _load_toml(path)
    mysql = raw.get("mysql", {}) if isinstance(raw.get("mysql"), dict) else {}
    crawl = raw.get("crawl", {}) if isinstance(raw.get("crawl"), dict) else {}

    data: dict[str, object] = {
        "mysql_host": mysql.get("host", "127.0.0.1"),
        "mysql_port": int(mysql.get("port", 3306)),
        "mysql_user": str(mysql.get("user", "")),
        "mysql_password": str(mysql.get("password", "")),
        "mysql_database": str(mysql.get("database", "spiderhub")),
        "obey_robots": _as_bool(crawl.get("obey_robots"), True),
        "request_delay_seconds": float(crawl.get("request_delay_seconds", 1.0)),
        "http_timeout_seconds": 30.0,
        "http_max_retries": 3,
        "allow_fetcher_upgrade": _as_bool(crawl.get("allow_fetcher_upgrade"), True),
        "allow_browser": _as_bool(crawl.get("allow_browser"), True),
        "impersonate_target": str(crawl.get("impersonate_target", "chrome")),
        "browser_challenge_wait_seconds": float(
            crawl.get("browser_challenge_wait_seconds", 15.0)
        ),
        "browser_headless": _as_bool(crawl.get("browser_headless"), True),
        "browser_storage_state": str(
            crawl.get("browser_storage_state", ".spiderhub/storage_state.json")
        ),
        "browser_cdp_url": str(crawl.get("browser_cdp_url", "")),
        "browser_user_data_dir": str(
            crawl.get("browser_user_data_dir", ".spiderhub/chrome-profile")
        ),
    }

    env_map = {
        "mysql_host": "SPIDERHUB_MYSQL_HOST",
        "mysql_port": "SPIDERHUB_MYSQL_PORT",
        "mysql_user": "SPIDERHUB_MYSQL_USER",
        "mysql_password": "SPIDERHUB_MYSQL_PASSWORD",
        "mysql_database": "SPIDERHUB_MYSQL_DATABASE",
        "obey_robots": "SPIDERHUB_OBEY_ROBOTS",
        "request_delay_seconds": "SPIDERHUB_REQUEST_DELAY_SECONDS",
        "allow_fetcher_upgrade": "SPIDERHUB_ALLOW_FETCHER_UPGRADE",
        "allow_browser": "SPIDERHUB_ALLOW_BROWSER",
        "impersonate_target": "SPIDERHUB_IMPERSONATE_TARGET",
        "browser_challenge_wait_seconds": "SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS",
        "browser_headless": "SPIDERHUB_BROWSER_HEADLESS",
        "browser_storage_state": "SPIDERHUB_BROWSER_STORAGE_STATE",
        "browser_cdp_url": "SPIDERHUB_BROWSER_CDP_URL",
        "browser_user_data_dir": "SPIDERHUB_BROWSER_USER_DATA_DIR",
    }
    for field, key in env_map.items():
        if key in environ and environ[key] != "":
            if field in {"mysql_port", "http_max_retries"}:
                data[field] = int(environ[key])
            elif field in {
                "request_delay_seconds",
                "http_timeout_seconds",
                "browser_challenge_wait_seconds",
            }:
                data[field] = float(environ[key])
            elif field in {
                "obey_robots",
                "allow_fetcher_upgrade",
                "allow_browser",
                "browser_headless",
            }:
                data[field] = _as_bool(environ[key], True)
            else:
                data[field] = environ[key]

    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None and key in data:
                data[key] = value

    return Settings(**data)  # type: ignore[arg-type]
