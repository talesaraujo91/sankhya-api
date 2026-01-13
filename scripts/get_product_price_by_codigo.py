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


def get_product_price(
    *,
    base_url: str,
    access_token: str,
    codigo_produto: int,
    page: int = 1,
    timeout_s: int = 30,
) -> Optional[Dict[str, Any]]:
    """Fetch product prices for a given product code.

    Endpoint: GET /v1/precos/produto/{codigoProduto}?page={page}

    Returns:
      - dict (parsed JSON) on success
      - None when API returns 404
    """

    url = base_url.rstrip("/") + f"/v1/precos/produto/{int(codigo_produto)}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"page": int(page)}

    resp = requests.get(url, headers=headers, params=params, timeout=timeout_s)
    if resp.status_code == 404:
        return None
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"GET {resp.url} failed: {resp.status_code} {resp.text[:500]}")

    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch product price info by codigoProduto.")
    parser.add_argument("codigoProduto", type=int, help="Product code (codigoProduto)")
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="Page number (required by API; default: 1)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL (default: env SANKHYA_BASE_URL)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")

    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    base_url = args.base_url or require_env("SANKHYA_BASE_URL")

    access_token = os.getenv("SANKHYA_ACCESS_TOKEN")
    if not access_token:
        client_id = require_env("SANKHYA_CLIENT_ID")
        client_secret = require_env("SANKHYA_CLIENT_SECRET")
        erp_token = require_env("SANKHYA_ERP_TOKEN")
        access_token = get_access_token(base_url, client_id, client_secret, erp_token)

    payload = get_product_price(
        base_url=base_url,
        access_token=access_token,
        codigo_produto=args.codigoProduto,
        page=args.page,
    )
    if payload is None:
        raise SystemExit(f"No price data found (404) for codigoProduto={args.codigoProduto}")

    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
