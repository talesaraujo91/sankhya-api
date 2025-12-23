import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import html as html_lib

import requests
import yaml


def _load_openapi(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "paths" not in data:
        raise ValueError(f"Not an OpenAPI document: {path}")
    return data


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


def _pick_response_example(response: Dict[str, Any]) -> Optional[Any]:
    content = response.get("content")
    if not isinstance(content, dict):
        return None

    # Prefer JSON if present
    media = None
    for key in ("application/json", "application/*+json"):
        if key in content:
            media = content[key]
            break
    if media is None:
        # fall back to first media
        for _, v in content.items():
            media = v
            break

    if not isinstance(media, dict):
        return None

    if "example" in media:
        return media.get("example")

    examples = media.get("examples")
    if isinstance(examples, dict) and examples:
        # take the first example's value
        first = next(iter(examples.values()))
        if isinstance(first, dict) and "value" in first:
            return first.get("value")

    return None


def _get_media_schema(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = response.get("content")
    if not isinstance(content, dict):
        return None

    # Prefer JSON if present
    media = None
    for key in ("application/json", "application/*+json"):
        if key in content:
            media = content[key]
            break
    if media is None:
        for _, v in content.items():
            media = v
            break
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    return schema if isinstance(schema, dict) else None


_REF_RE = re.compile(r"#/components/schemas/([^/]+)$")


def _collect_schema_refs(schema: Any) -> Set[str]:
    refs: Set[str] = set()
    stack: List[Any] = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                m = _REF_RE.search(ref)
                if m:
                    refs.add(m.group(1))
            for v in node.values():
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return refs


class _ExampleBuilder:
    def __init__(self, components_schemas: Dict[str, Any]):
        self._schemas = components_schemas

    def _resolve_ref(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            return schema
        m = _REF_RE.search(ref)
        if not m:
            return schema
        name = m.group(1)
        resolved = self._schemas.get(name)
        return resolved if isinstance(resolved, dict) else schema

    def build(self, schema: Dict[str, Any], *, depth: int = 0) -> Any:
        if depth > 6:
            return None

        schema = self._resolve_ref(schema)

        if "example" in schema:
            return schema.get("example")

        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]

        for combiner in ("oneOf", "anyOf"):
            options = schema.get(combiner)
            if isinstance(options, list) and options:
                first = options[0]
                if isinstance(first, dict):
                    return self.build(first, depth=depth + 1)

        all_of = schema.get("allOf")
        if isinstance(all_of, list) and all_of:
            merged: Dict[str, Any] = {}
            for part in all_of:
                if not isinstance(part, dict):
                    continue
                ex = self.build(part, depth=depth + 1)
                if isinstance(ex, dict):
                    merged.update(ex)
            if merged:
                return merged
            first = all_of[0]
            if isinstance(first, dict):
                return self.build(first, depth=depth + 1)

        schema_type = schema.get("type")
        properties = schema.get("properties")
        if schema_type == "object" or isinstance(properties, dict):
            result: Dict[str, Any] = {}
            if isinstance(properties, dict):
                for prop, prop_schema in properties.items():
                    if not isinstance(prop, str) or not isinstance(prop_schema, dict):
                        continue
                    val = self.build(prop_schema, depth=depth + 1)
                    if val is not None:
                        result[prop] = val
            # If no properties exist, keep it minimal
            return result

        if schema_type == "array":
            items = schema.get("items")
            if isinstance(items, dict):
                item_ex = self.build(items, depth=depth + 1)
                return [item_ex] if item_ex is not None else []
            return []

        if schema_type == "integer":
            return 0
        if schema_type == "number":
            return 0
        if schema_type == "boolean":
            return True
        # default string/null
        if schema_type == "string" or schema_type is None:
            return ""
        return None


def _slug_for_endpoint(method: str, path: str) -> str:
    parts = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            parts.append(seg[1:-1].strip().lower())
        else:
            parts.append(seg.lower())
    return f"{method.lower()}_{'-'.join(parts)}"


def _load_readme_schema(seed_url: str, *, timeout_s: int = 30) -> Optional[Dict[str, Any]]:
    # ReadMe embeds a large JSON state in the `ssr-props` script tag.
    resp = requests.get(seed_url, timeout=timeout_s)
    if resp.status_code != 200:
        return None

    text = resp.text
    m = re.search(r'data-initial-props="(.*?)"', text, flags=re.S)
    if not m:
        return None

    try:
        props = json.loads(html_lib.unescape(m.group(1)))
    except Exception:
        return None

    schema = props.get("document", {}).get("api", {}).get("schema")
    return schema if isinstance(schema, dict) else None


def _iter_params(op: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    params = op.get("parameters")
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict):
                yield p


def build_dataset(openapi: Dict[str, Any], *, readme_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI has no paths")

    readme_paths = readme_schema.get("paths") if isinstance(readme_schema, dict) else None
    readme_components = (
        readme_schema.get("components", {}).get("schemas", {})
        if isinstance(readme_schema, dict)
        else {}
    )
    example_builder = _ExampleBuilder(readme_components) if isinstance(readme_components, dict) else None

    endpoints: List[Dict[str, Any]] = []

    # For relationships
    schema_to_endpoints: defaultdict[str, Set[str]] = defaultdict(set)
    tag_to_endpoints: defaultdict[str, Set[str]] = defaultdict(set)

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        common_params = list(_iter_params(path_item))

        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue

            operation_id = op.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                operation_id = f"{method.upper()} {path}"

            summary = op.get("summary") if isinstance(op.get("summary"), str) else ""
            description = op.get("description") if isinstance(op.get("description"), str) else ""

            tags = [t for t in op.get("tags", []) if isinstance(t, str)] if isinstance(op.get("tags"), list) else []

            params = common_params + list(_iter_params(op))

            query_params: List[Dict[str, Any]] = []
            path_params: List[Dict[str, Any]] = []
            header_params: List[Dict[str, Any]] = []

            for p in params:
                where = p.get("in")
                name = p.get("name")
                if not isinstance(where, str) or not isinstance(name, str):
                    continue

                item = {
                    "name": name,
                    "in": where,
                    "required": bool(p.get("required", False)),
                    "description": p.get("description") if isinstance(p.get("description"), str) else "",
                    "schema": _jsonable(p.get("schema")) if "schema" in p else None,
                }

                if where == "query":
                    query_params.append(item)
                elif where == "path":
                    path_params.append(item)
                elif where == "header":
                    header_params.append(item)

            responses = op.get("responses")
            response_examples: List[Dict[str, Any]] = []
            response_schema_refs: Set[str] = set()

            if isinstance(responses, dict):
                for status, resp in responses.items():
                    if not isinstance(resp, dict):
                        continue

                    example = _pick_response_example(resp)

                    # collect schema refs in responses
                    content = resp.get("content")
                    if isinstance(content, dict):
                        for media in content.values():
                            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                                response_schema_refs |= _collect_schema_refs(media["schema"])

                    if example is not None:
                        response_examples.append(
                            {
                                "status": str(status),
                                "description": resp.get("description")
                                if isinstance(resp.get("description"), str)
                                else "",
                                "example": _jsonable(example),
                            }
                        )

            # If the published OpenAPI lacks examples, synthesize one from the ReadMe schema (which includes per-field examples).
            if not response_examples and isinstance(readme_paths, dict) and example_builder is not None:
                readme_pi = readme_paths.get(path)
                if isinstance(readme_pi, dict):
                    readme_op = readme_pi.get(method)
                    if isinstance(readme_op, dict):
                        readme_resps = readme_op.get("responses")
                        chosen_status = None
                        chosen_resp = None
                        if isinstance(readme_resps, dict):
                            for st in ("200", "201", "202", "default"):
                                if st in readme_resps and isinstance(readme_resps[st], dict):
                                    chosen_status = st
                                    chosen_resp = readme_resps[st]
                                    break
                            if chosen_resp is None:
                                for st, r in readme_resps.items():
                                    if isinstance(r, dict):
                                        chosen_status = str(st)
                                        chosen_resp = r
                                        break

                        if isinstance(chosen_resp, dict):
                            schema = _get_media_schema(chosen_resp)
                            if isinstance(schema, dict):
                                ex = example_builder.build(schema)
                                if ex is not None and ex != {} and ex != []:
                                    response_examples.append(
                                        {
                                            "status": str(chosen_status or "200"),
                                            "description": chosen_resp.get("description")
                                            if isinstance(chosen_resp.get("description"), str)
                                            else "",
                                            "example": _jsonable(ex),
                                        }
                                    )

            endpoint = {
                "id": operation_id,
                "method": method.upper(),
                "path": path,
                "summary": summary,
                "description": description,
                "tags": tags,
                "queryParams": query_params,
                "pathParams": path_params,
                "headerParams": header_params,
                "responseExamples": response_examples,
                "schemaRefs": sorted(response_schema_refs),
            }
            endpoints.append(endpoint)

            for tag in tags:
                tag_to_endpoints[tag].add(operation_id)
            for schema_name in response_schema_refs:
                schema_to_endpoints[schema_name].add(operation_id)

    # Build edges
    edges: List[Dict[str, Any]] = []

    def add_edges_from_groups(groups: Dict[str, Set[str]], edge_type: str) -> None:
        for key, ids in groups.items():
            ids_list = sorted(ids)
            if len(ids_list) < 2:
                continue
            # connect in a chain to avoid O(n^2) explosion
            for a, b in zip(ids_list, ids_list[1:]):
                edges.append({"source": a, "target": b, "type": edge_type, "key": key})

    add_edges_from_groups(tag_to_endpoints, "tag")
    add_edges_from_groups(schema_to_endpoints, "schema")

    return {
        "info": _jsonable(openapi.get("info", {})),
        "endpoints": endpoints,
        "edges": edges,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a normalized endpoints dataset from an OpenAPI YAML.")
    parser.add_argument(
        "--spec",
        default=str(Path(__file__).resolve().parents[1] / "data" / "api.yaml"),
        help="Path to OpenAPI YAML (default: ./data/api.yaml)",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "data" / "endpoints.json"),
        help="Output JSON path (default: ./data/endpoints.json)",
    )
    parser.add_argument(
        "--no-docs-examples",
        action="store_true",
        help="Do not fetch ReadMe docs to synthesize response examples.",
    )
    parser.add_argument(
        "--docs-base",
        default="https://developer.sankhya.com.br/reference",
        help="ReadMe reference base URL (default: https://developer.sankhya.com.br/reference)",
    )

    args = parser.parse_args()

    spec_path = Path(args.spec)
    out_path = Path(args.out)

    openapi = _load_openapi(spec_path)

    readme_schema = None
    if not args.no_docs_examples:
        # Fetch the ReadMe-embedded schema once using the first endpoint as a seed.
        seed_slug = None
        for p in sorted((openapi.get("paths") or {}).keys()):
            pi = (openapi.get("paths") or {}).get(p)
            if not isinstance(pi, dict):
                continue
            for m in ("get", "post", "put", "patch", "delete"):
                if isinstance(pi.get(m), dict):
                    seed_slug = _slug_for_endpoint(m, p)
                    break
            if seed_slug:
                break

        if seed_slug:
            seed_url = f"{args.docs_base.rstrip('/')}/{seed_slug}"
            readme_schema = _load_readme_schema(seed_url)
            if readme_schema is None:
                print(f"warning: failed to load ReadMe schema from {seed_url}; response examples may be empty")
        else:
            print("warning: could not find a seed endpoint to load ReadMe schema")

    dataset = build_dataset(openapi, readme_schema=readme_schema)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} (endpoints={len(dataset['endpoints'])}, edges={len(dataset['edges'])})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
