from __future__ import annotations

from pathlib import Path

from spiderhub.core.settings import load_settings


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


def test_cli_overrides_env(tmp_path: Path) -> None:
    settings = load_settings(
        env={"SPIDERHUB_MYSQL_HOST": "from-env"},
        config_path=tmp_path / "missing.toml",
        cli_overrides={"mysql_host": "from-cli"},
    )
    assert settings.mysql_host == "from-cli"


def test_toml_file_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[mysql]\nhost = "toml-host"\nport = 3308\nuser = "tu"\n'
        'password = "tp"\ndatabase = "tdb"\n'
        "[crawl]\nobey_robots = false\nrequest_delay_seconds = 2.0\n",
        encoding="utf-8",
    )
    settings = load_settings(env={}, config_path=path)
    assert settings.mysql_host == "toml-host"
    assert settings.mysql_port == 3308
    assert settings.request_delay_seconds == 2.0
