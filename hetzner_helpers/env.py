"""Load Robot credentials and optional defaults from the environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load a .env file if present. Does not override existing env vars."""
    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)
        return
    # Prefer project-local .env when running from a clone; else cwd.
    here = Path(__file__).resolve().parent.parent / ".env"
    if here.is_file():
        load_dotenv(here, override=False)
    else:
        load_dotenv(override=False)


def require_credentials() -> tuple[str, str]:
    user = os.environ.get("ROBOT_USER", "").strip()
    password = os.environ.get("ROBOT_PASS", "").strip()
    if not user or not password:
        raise SystemExit(
            "Set ROBOT_USER and ROBOT_PASS (env or .env). "
            "These are the Robot webservice credentials, not your account login."
        )
    return user, password


def optional_server_number() -> int | None:
    raw = os.environ.get("SERVER_NUMBER", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise SystemExit(f"SERVER_NUMBER must be an integer, got {raw!r}") from e


def optional_server_ip() -> str | None:
    ip = os.environ.get("SERVER_IP", "").strip()
    return ip or None


def optional_pubkey_path() -> str | None:
    path = os.environ.get("SSH_PUBKEY", "").strip()
    return path or None


def optional_root_part() -> str | None:
    part = os.environ.get("ROOT_PART", "").strip()
    return part or None
