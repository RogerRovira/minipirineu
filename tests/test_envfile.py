"""Local .env loader: shell-exported variables always win, quotes and
comments are tolerated, and a missing file is a no-op (CI has no .env)."""

import os

from minipirineu.envfile import load_env


def test_loads_key_values_and_skips_comments_and_blanks(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\nMETEOCAT_API_KEY=abc123\nQUOTED='hola'\nNOISE_LINE\n")
    monkeypatch.delenv("METEOCAT_API_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    loaded = load_env(env)
    assert loaded == {"METEOCAT_API_KEY": "abc123", "QUOTED": "hola"}
    assert os.environ["METEOCAT_API_KEY"] == "abc123"


def test_existing_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("METEOCAT_API_KEY=from_file\n")
    monkeypatch.setenv("METEOCAT_API_KEY", "from_shell")
    assert load_env(env) == {}
    assert os.environ["METEOCAT_API_KEY"] == "from_shell"


def test_missing_file_is_a_noop(tmp_path):
    assert load_env(tmp_path / "absent.env") == {}


def test_empty_value_is_not_exported(tmp_path, monkeypatch):
    # the .env.example placeholder shape: KEY= with no value
    env = tmp_path / ".env"
    env.write_text("METEOCAT_API_KEY=\n")
    monkeypatch.delenv("METEOCAT_API_KEY", raising=False)
    assert load_env(env) == {}
    assert "METEOCAT_API_KEY" not in os.environ
