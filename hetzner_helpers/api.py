"""Hetzner Robot Webservice API client."""

from __future__ import annotations

import time
from typing import Any

import requests

ROBOT_BASE = "https://robot-ws.your-server.de"


class RobotAPIError(Exception):
    """Robot API returned an error response."""

    def __init__(self, status: int, code: str | None, message: str, response: requests.Response):
        self.status = status
        self.code = code
        self.message = message
        self.response = response
        parts = [f"HTTP {status}"]
        if code:
            parts.append(code)
        parts.append(message)
        super().__init__(" — ".join(parts))


class RobotClient:
    def __init__(self, user: str, password: str, base_url: str = ROBOT_BASE, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (user, password)

    def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | list[tuple[str, Any]] | None = None,
        allow_404: bool = False,
        retries_on_rate_limit: int = 1,
    ) -> Any:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            resp = self.session.request(method, url, data=data, timeout=self.timeout)
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code == 204 or not resp.content:
                if 200 <= resp.status_code < 300:
                    return None
            if resp.status_code == 429 or self._is_rate_limited(resp):
                if attempt < retries_on_rate_limit:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
            if not (200 <= resp.status_code < 300):
                raise self._error_from_response(resp)
            if not resp.content:
                return None
            return resp.json()

    @staticmethod
    def _is_rate_limited(resp: requests.Response) -> bool:
        try:
            body = resp.json()
        except ValueError:
            return False
        err = body.get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            return False
        return err.get("code") == "RATE_LIMIT_EXCEEDED"

    @staticmethod
    def _error_from_response(resp: requests.Response) -> RobotAPIError:
        code = None
        message = resp.text.strip() or resp.reason
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                code = err.get("code")
                message = err.get("message") or message
        except ValueError:
            pass
        return RobotAPIError(resp.status_code, code, message, resp)

    # --- Servers -------------------------------------------------------------

    def list_servers(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/server")
        return data if isinstance(data, list) else []

    def get_server(self, server_number: int) -> dict[str, Any]:
        data = self.request("GET", f"/server/{server_number}")
        return data.get("server", data)

    def server_ip(self, server_number: int) -> str:
        srv = self.get_server(server_number)
        ip = srv.get("server_ip")
        if not ip:
            raise ValueError(f"No server_ip for server #{server_number}")
        return ip

    # --- SSH keys ------------------------------------------------------------

    def list_keys(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/key", allow_404=True)
        if data is None:
            return []
        return data if isinstance(data, list) else []

    def add_key(self, name: str, public_key: str) -> dict[str, Any]:
        data = self.request("POST", "/key", data={"name": name, "data": public_key})
        return data.get("key", data)

    def delete_key(self, fingerprint: str) -> None:
        self.request("DELETE", f"/key/{fingerprint}")

    def find_key_by_data(self, public_key: str) -> dict[str, Any] | None:
        needle = public_key.strip()
        for entry in self.list_keys():
            key = entry.get("key", entry)
            if key.get("data", "").strip() == needle:
                return key
            # Match on key type + material (ignore comment differences).
            parts = needle.split()
            existing = key.get("data", "").split()
            if len(parts) >= 2 and len(existing) >= 2 and parts[0] == existing[0] and parts[1] == existing[1]:
                return key
        return None

    # --- Rescue --------------------------------------------------------------

    def get_rescue(self, server_number: int) -> dict[str, Any]:
        data = self.request("GET", f"/boot/{server_number}/rescue")
        return data.get("rescue", data)

    def get_rescue_last(self, server_number: int) -> dict[str, Any]:
        data = self.request("GET", f"/boot/{server_number}/rescue/last")
        return data.get("rescue", data)

    def activate_rescue(
        self,
        server_number: int,
        *,
        os_name: str = "linux",
        arch: str = "64",
        ssh_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        # authorized_key[] form field per Robot docs when using fingerprints.
        form: list[tuple[str, Any]] = [("os", os_name), ("arch", arch)]
        if ssh_fingerprint:
            form.append(("authorized_key[]", ssh_fingerprint))
        data = self.request("POST", f"/boot/{server_number}/rescue", data=form)
        return data.get("rescue", data)

    def deactivate_rescue(self, server_number: int) -> dict[str, Any]:
        data = self.request("DELETE", f"/boot/{server_number}/rescue")
        return data.get("rescue", data)

    # --- Reset ---------------------------------------------------------------

    def reset(self, server_number: int, reset_type: str = "hw") -> dict[str, Any]:
        data = self.request("POST", f"/reset/{server_number}", data={"type": reset_type})
        return data.get("reset", data)

    # --- Firewall ------------------------------------------------------------

    def get_firewall(self, server_number: int) -> dict[str, Any] | None:
        data = self.request("GET", f"/firewall/{server_number}", allow_404=True)
        if data is None:
            return None
        return data.get("firewall", data)

    def firewall_may_block_ssh(self, server_number: int) -> str | None:
        """
        Return a warning string if the active firewall looks likely to block SSH,
        otherwise None. Best-effort heuristic only.
        """
        try:
            fw = self.get_firewall(server_number)
        except RobotAPIError as e:
            if e.status == 404:
                return None
            return f"Could not read firewall ({e}); check Robot UI if SSH fails."

        if not fw:
            return None
        status = (fw.get("status") or "").lower()
        if status not in ("active", "apply"):
            return None

        rules = (fw.get("rules") or {}).get("input") or []
        if not rules:
            # Empty input with active firewall often means default-deny.
            return (
                f"Firewall status is {status!r} with no input accept rules — "
                "SSH (port 22) may be blocked."
            )

        allows_ssh = False
        for rule in rules:
            if (rule.get("action") or "").lower() != "accept":
                continue
            dst = str(rule.get("dst_port") or "")
            if not dst or dst == "22" or dst.startswith("22-") or "-22" in dst or dst == "1-65535":
                allows_ssh = True
                break
            # Broad ranges that include 22
            if "-" in dst:
                try:
                    lo, hi = dst.split("-", 1)
                    if int(lo) <= 22 <= int(hi):
                        allows_ssh = True
                        break
                except ValueError:
                    pass

        if not allows_ssh:
            return (
                f"Firewall status is {status!r} and no input rule clearly allows port 22 — "
                "SSH to rescue may fail until the firewall is adjusted in Robot."
            )
        return None
