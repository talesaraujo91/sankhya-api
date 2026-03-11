import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # Don't overwrite env already set by the shell
        os.environ.setdefault(k, v)


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise SystemExit(f"Missing env var: {name}")
    return val


def get_access_token(base_url: str, client_id: str, client_secret: str, erp_token: str) -> str:
    url = base_url.rstrip("/") + "/authenticate"
    headers = {"X-Token": erp_token}
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, headers=headers, data=data, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Auth failed: {resp.status_code} {resp.text[:500]}")

    # Spec says x-www-form-urlencoded, but many servers still return JSON; handle both.
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = resp.json()
        token = payload.get("access_token")
    else:
        token = None
        for part in resp.text.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k == "access_token":
                    token = v
                    break

    if not token:
        raise SystemExit("Auth succeeded but access_token was not found in response")
    return str(token)


def get_project_by_id(
    *,
    base_url: str,
    access_token: str,
    project_id: int,
    timeout_s: int = 30,
) -> Optional[Dict[str, Any]]:
    """Fetch a single project by its id (codigoProjeto).

    Returns:
      - dict with project fields when found
      - None when the API returns 404

    Notes:
      Sankhya's gateway commonly wraps responses as {"data": ...}. This helper
      accepts either a bare object or a {"data": {...}} wrapper.
    """
    print(access_token)
    url = base_url.rstrip("/") + f"/v1/precos/produto/{int(project_id)}"
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers, timeout=timeout_s)
    if resp.status_code == 404:
        return None
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"GET {url} failed: {resp.status_code} {resp.text[:500]}")

    payload = resp.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload, dict):
        return payload

    # Unexpected shape (e.g., list); keep it predictable.
    return {"data": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Sankhya project info by id (codigoProjeto).")
    parser.add_argument("project_id", type=int, help="Project id (codigoProjeto)")
    parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL (default: env SANKHYA_BASE_URL)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    base_url = args.base_url or require_env("SANKHYA_BASE_URL")

    access_token = os.getenv("SANKHYA_ACCESS_TOKEN")
    if not access_token:
        client_id = require_env("SANKHYA_CLIENT_ID")
        client_secret = require_env("SANKHYA_CLIENT_SECRET")
        erp_token = require_env("SANKHYA_ERP_TOKEN")
        access_token = get_access_token(base_url, client_id, client_secret, erp_token)

    project = get_project_by_id(base_url=base_url, access_token=access_token, project_id=args.project_id)
    if project is None:
        raise SystemExit(f"Project not found (404): {args.project_id}")

    if args.pretty:
        print(json.dumps(project, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(project, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
