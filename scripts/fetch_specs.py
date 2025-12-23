import argparse
import hashlib
import os
from pathlib import Path

import requests

DEFAULT_SPECS = {
    "sankhya": "https://api.sankhya.com.br/docs/openapi/api.yaml",
    "legada": "https://api.sankhya.com.br/docs/legado/openapi/api-legada.yaml",
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def fetch(url: str, out_path: Path, *, timeout_s: int = 60) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, timeout=timeout_s)
    resp.raise_for_status()
    content = resp.content

    out_path.write_bytes(content)
    print(f"wrote {out_path} ({len(content)} bytes, sha256={_sha256_bytes(content)[:12]})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Sankhya OpenAPI specs (YAML).")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "data"),
        help="Output directory (default: ./data)",
    )
    parser.add_argument(
        "--only",
        choices=["sankhya", "legada", "all"],
        default="all",
        help="Which spec(s) to download.",
    )

    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    targets: dict[str, str]
    if args.only == "all":
        targets = DEFAULT_SPECS
    else:
        targets = {args.only: DEFAULT_SPECS[args.only]}

    for name, url in targets.items():
        out_name = "api.yaml" if name == "sankhya" else "api-legada.yaml"
        fetch(url, out_dir / out_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
