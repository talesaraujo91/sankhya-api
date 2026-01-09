import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "data" / "endpoints.json"
RESPONSES_DIR = REPO_ROOT / "data" / "responses"


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
        # parse form-encoded
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


def iter_call_targets(endpoints: List[Dict[str, Any]]) -> Iterable[Tuple[str, str, str, Dict[str, Any]]]:
    """Yield (id, method, path, endpoint) for endpoints we can call automatically.

    Rules (conservative):
    - GET/HEAD/OPTIONS only
    - no path params
    - no required query params
    - no request body (not included in dataset; so just avoid non-GET)
    """
    for ep in endpoints:
        method = str(ep.get("method", "")).upper()
        path = str(ep.get("path", ""))
        if method not in {"GET", "HEAD", "OPTIONS"}:
            continue
        if ep.get("pathParams"):
            continue
        required_q = [p for p in (ep.get("queryParams") or []) if p.get("required")]
        if required_q:
            continue
        yield str(ep.get("id")), method, path, ep


def safe_filename_for_endpoint(method: str, path: str) -> str:
    # Example: GET /v1/naturezas/{codigoNatureza} -> GET_v1_naturezas_codigoNatureza.json
    name = f"{method.upper()}_{path.strip('/')}"
    name = name.replace("/", "_")
    name = name.replace("{", "").replace("}", "")
    # keep it filesystem-friendly
    keep = []
    for ch in name:
        if ch.isalnum() or ch in {"_", "-", "."}:
            keep.append(ch)
        else:
            keep.append("_")
    cleaned = "".join(keep)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned + ".json"


def write_success_response(method: str, path: str, resp: requests.Response) -> Path:
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESPONSES_DIR / safe_filename_for_endpoint(method, path)

    payload: Dict[str, Any] = {
        "request": {"method": method, "path": path, "url": resp.url},
        "response": {
            "status": resp.status_code,
            "headers": dict(resp.headers),
        },
    }

    try:
        payload["response"]["json"] = resp.json()
    except ValueError:
        # Not JSON; still save as JSON file by wrapping text.
        payload["response"]["text"] = resp.text

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    base_url = require_env("SANKHYA_BASE_URL")
    client_id = require_env("SANKHYA_CLIENT_ID")
    client_secret = require_env("SANKHYA_CLIENT_SECRET")
    erp_token = require_env("SANKHYA_ERP_TOKEN")

    access_token = os.getenv("SANKHYA_ACCESS_TOKEN")
    if not access_token:
        access_token = get_access_token(base_url, client_id, client_secret, erp_token)

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    endpoints: List[Dict[str, Any]] = dataset.get("endpoints") or []

    headers = {"Authorization": f"Bearer {access_token}"}

    targets = list(iter_call_targets(endpoints))
    print(f"Will call {len(targets)} endpoints (safe auto-call subset)")

    ok = 0
    fail = 0

    for idx, (eid, method, path, ep) in enumerate(targets, start=1):
        url = base_url.rstrip("/") + path
        try:
            resp = requests.request(method, url, headers=headers, timeout=30)
            if 200 <= resp.status_code < 300:
                ok += 1
                out_path = write_success_response(method, path, resp)
                print(f"[{idx}/{len(targets)}] {method} {path} -> {resp.status_code} (saved {out_path.relative_to(REPO_ROOT)})")
            else:
                fail += 1
                print(f"[{idx}/{len(targets)}] {method} {path} -> {resp.status_code}")
        except requests.RequestException as e:
            fail += 1
            print(f"[{idx}/{len(targets)}] {method} {path} -> ERROR: {e}")

        time.sleep(0.15)

    print(f"Done. ok={ok} fail={fail}")
    print("Note: endpoints requiring path params or bodies were skipped.")
    if ok:
        print(f"Saved responses to: {RESPONSES_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
