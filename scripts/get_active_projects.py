import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "output" / "active_projects.json"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing env var: {name}")
    return value


def get_access_token(base_url: str, client_id: str, client_secret: str, erp_token: str) -> str:
    url = base_url.rstrip("/") + "/authenticate"
    headers = {"X-Token": erp_token}
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }

    response = requests.post(url, headers=headers, data=data, timeout=30)
    if response.status_code != 200:
        raise SystemExit(f"Auth failed: {response.status_code} {response.text[:500]}")

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = response.json()
        token = payload.get("access_token")
    else:
        token = None
        for part in response.text.split("&"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key == "access_token":
                token = value
                break

    if not token:
        raise SystemExit("Auth succeeded but access_token was not found in response")
    return str(token)


def extract_project_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data", payload)
    else:
        data = payload

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def fetch_projects_page(base_url: str, access_token: str, page: int, timeout_s: int) -> List[Dict[str, Any]]:
    url = base_url.rstrip("/") + "/v1/projetos"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"page": page}

    response = requests.get(url, headers=headers, params=params, timeout=timeout_s)
    if not (200 <= response.status_code < 300):
        raise RuntimeError(
            f"GET {response.url} failed: {response.status_code} {response.text[:500]}"
        )

    return extract_project_list(response.json())


def fetch_all_projects(base_url: str, access_token: str, timeout_s: int, max_pages: int) -> List[Dict[str, Any]]:
    projects: List[Dict[str, Any]] = []

    for page in range(max_pages):
        page_items = fetch_projects_page(
            base_url=base_url,
            access_token=access_token,
            page=page,
            timeout_s=timeout_s,
        )
        if not page_items:
            break
        projects.extend(page_items)

    return projects


def is_active_project(project: Dict[str, Any]) -> bool:
    return project.get("ativo") is True


def save_output(output_path: Path, payload: Dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_output_payload(projects: List[Dict[str, Any]], active_projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "totalProjects": len(projects),
            "activeProjects": len(active_projects),
        },
        "data": active_projects,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch all Sankhya projects, keep only active ones, and save the response to disk."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL (default: env SANKHYA_BASE_URL)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output JSON file path (default: output/active_projects.json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="Safety limit for paginated fetches (default: 500)",
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

    projects = fetch_all_projects(
        base_url=base_url,
        access_token=access_token,
        timeout_s=args.timeout,
        max_pages=args.max_pages,
    )
    active_projects = [project for project in projects if is_active_project(project)]

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    payload = build_output_payload(projects, active_projects)
    save_output(output_path, payload)

    print(
        json.dumps(
            {
                "savedTo": str(output_path),
                "totalProjects": len(projects),
                "activeProjects": len(active_projects),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())