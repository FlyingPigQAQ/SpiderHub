from __future__ import annotations

from pathlib import Path

import pytest

from spiderhub.core.settings import Settings, cdp_mode_active, load_settings


def test_defaults_and_env_override(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_MYSQL_HOST": "db.local",
            "SPIDERHUB_MYSQL_PORT": "3307",
            "SPIDERHUB_MYSQL_USER": "u",
            "SPIDERHUB_MYSQL_PASSWORD": "p",
            "SPIDERHUB_MYSQL_DATABASE": "hub",
            "SPIDERHUB_OBEY_ROBOTS": "false",
            "SPIDERHUB_REQUEST_DELAY_SECONDS": "0.5",
            "SPIDERHUB_BROWSER_CDP_URL": "http://127.0.0.1:9222",
            "SPIDERHUB_BROWSER_ENGINE": "camoufox",
            "SPIDERHUB_ALLOW_EXTERNAL_SOLVER": "true",
            "SPIDERHUB_EXTERNAL_SOLVER_URL": "http://127.0.0.1:8191/v1",
            "SPIDERHUB_EXTERNAL_SOLVER_SKIP_BROWSER": "true",
            "SPIDERHUB_EXTERNAL_SOLVER_TIMEOUT_MS": "45000",
            "SPIDERHUB_EXTERNAL_SOLVER_SESSION": "sess-a",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.mysql_host == "db.local"
    assert settings.mysql_port == 3307
    assert settings.mysql_user == "u"
    assert settings.mysql_password == "p"
    assert settings.mysql_database == "hub"
    assert settings.obey_robots is False
    assert settings.request_delay_seconds == 0.5
    assert settings.browser_cdp_url == "http://127.0.0.1:9222"
    assert settings.browser_engine == "camoufox"
    assert settings.allow_external_solver is True
    assert settings.external_solver_url == "http://127.0.0.1:8191/v1"
    assert settings.external_solver_skip_browser is True
    assert settings.external_solver_timeout_ms == 45000
    assert settings.external_solver_session == "sess-a"
    defaults = load_settings(env={}, config_path=tmp_path / "missing.toml")
    assert defaults.browser_engine == "playwright"
    assert defaults.allow_external_solver is False


def test_cli_overrides_env(tmp_path: Path) -> None:
    settings = load_settings(
        env={"SPIDERHUB_MYSQL_HOST": "from-env"},
        config_path=tmp_path / "missing.toml",
        cli_overrides={"mysql_host": "from-cli"},
    )
    assert settings.mysql_host == "from-cli"


def test_env_overrides_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mysql]\nhost = "toml-host"\n', encoding="utf-8")
    settings = load_settings(
        env={"SPIDERHUB_MYSQL_HOST": "env-host"},
        config_path=path,
    )
    assert settings.mysql_host == "env-host"


def test_toml_file_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[mysql]\nhost = "toml-host"\nport = 3308\nuser = "tu"\n'
        'password = "tp"\ndatabase = "tdb"\n'
        "[crawl]\nobey_robots = false\nrequest_delay_seconds = 2.0\n"
        'browser_engine = "patchright"\nallow_external_solver = true\n',
        encoding="utf-8",
    )
    settings = load_settings(env={}, config_path=path)
    assert settings.mysql_host == "toml-host"
    assert settings.mysql_port == 3308
    assert settings.request_delay_seconds == 2.0
    assert settings.browser_engine == "patchright"
    assert settings.allow_external_solver is True


def test_invalid_browser_engine_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="browser_engine"):
        load_settings(
            env={"SPIDERHUB_BROWSER_ENGINE": "selenium"},
            config_path=tmp_path / "missing.toml",
        )


def test_feishu_env_and_defaults(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_FEISHU_APP_ID": "cli_xxx",
            "SPIDERHUB_FEISHU_APP_SECRET": "sec",
            "SPIDERHUB_FEISHU_RECEIVE_ID_TYPE": "open_id",
            "SPIDERHUB_FEISHU_RECEIVE_ID": "ou_xxx",
            "SPIDERHUB_FEISHU_NOTIFY_COOLDOWN_SECONDS": "120",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.feishu_app_id == "cli_xxx"
    assert settings.feishu_app_secret == "sec"
    assert settings.feishu_receive_id_type == "open_id"
    assert settings.feishu_receive_id == "ou_xxx"
    assert settings.feishu_notify_cooldown_seconds == 120.0
    defaults = load_settings(env={}, config_path=tmp_path / "missing.toml")
    assert defaults.feishu_app_id == ""
    assert defaults.feishu_notify_cooldown_seconds == 600.0


def test_invalid_feishu_receive_id_type_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="feishu_receive_id_type"):
        load_settings(
            env={"SPIDERHUB_FEISHU_RECEIVE_ID_TYPE": "email"},
            config_path=tmp_path / "missing.toml",
        )


def test_browser_cdp_enabled_and_keep_alive_env(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_BROWSER_CDP_ENABLED": "true",
            "SPIDERHUB_BROWSER_CDP_KEEP_ALIVE": "true",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.browser_cdp_enabled is True
    assert settings.browser_cdp_keep_alive is True
    assert settings.browser_cdp_url == ""
    defaults = load_settings(env={}, config_path=tmp_path / "missing.toml")
    assert defaults.browser_cdp_enabled is False
    assert defaults.browser_cdp_keep_alive is False


def test_browser_cdp_flags_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[crawl]\nbrowser_cdp_enabled = true\nbrowser_cdp_keep_alive = true\n",
        encoding="utf-8",
    )
    settings = load_settings(env={}, config_path=path)
    assert settings.browser_cdp_enabled is True
    assert settings.browser_cdp_keep_alive is True


def test_cdp_mode_active() -> None:
    assert cdp_mode_active(Settings(browser_cdp_enabled=True)) is True
    assert cdp_mode_active(
        Settings(browser_cdp_url="http://127.0.0.1:9222")
    ) is True
    assert cdp_mode_active(Settings()) is False
