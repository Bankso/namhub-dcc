"""Build a catalog of registered Synapse JSON schemas from target namespaces.

The catalog is used later to map Landscape assays to metadata schemas. For each
namespace in ``NAMESPACES`` this script lists the schemas registered under the
namespace's Synapse organization, picks one version per schema (the highest
semantic version, or an exact pinned version), fetches the registered body, and
records its title, description, processing levels, property names, required
properties and assay-related enum values.

Schemas whose body cannot be fetched, or that lack the pinned version, are
recorded under ``skipped`` with the reason.

Requires cached Synapse credentials (``synapseclient.login``).

Usage:
    python analysis/build_synapse_schema_catalog.py
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import synapseclient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

OUTPUT_JSON = "analysis/synapse_schema_catalog.json"

# namespace -> Synapse organization, optional schemaName prefix filter, and
# version ("latest" = highest semantic version, otherwise an exact version).
NAMESPACES: List[Dict[str, Optional[str]]] = [
    {"namespace": "HTAN2", "organization": "HTAN2Organization", "name_prefix": None, "version": "latest"},
    {"namespace": "MC2", "organization": "MC2Center", "name_prefix": None, "version": "14.1.0"},
    {"namespace": "NF", "organization": "org.synapse.nf", "name_prefix": None, "version": "latest"},
    {"namespace": "ADKP", "organization": "sage.schemas.ad", "name_prefix": None, "version": "latest"},
    {"namespace": "ELITE", "organization": "sage.schemas.elite", "name_prefix": None, "version": "latest"},
    {"namespace": "ALS", "organization": "ampals.schemas", "name_prefix": None, "version": "latest"},
]

_LEVEL = re.compile(r"Level(\d+)")


def paginate(syn: synapseclient.Synapse, uri: str, body: Dict[str, Any]) -> List[Dict]:
    """POST a paginated list request and return all `page` items."""
    items: List[Dict] = []
    body = dict(body)
    while True:
        resp = syn.restPOST(uri, body=json.dumps(body))
        items.extend(resp.get("page") or [])
        token = resp.get("nextPageToken")
        if not token:
            return items
        body["nextPageToken"] = token


def version_key(version: str) -> tuple:
    """Numeric sort key for a semantic version (prerelease suffix ignored)."""
    return tuple(int(p) for p in version.split("-")[0].split("."))


def parse_levels(schema_name: str) -> List[str]:
    """Parse processing levels, e.g. 'Level3and4' -> ['Level 3', 'Level 4']."""
    match = _LEVEL.search(schema_name)
    if not match:
        return []
    rest = schema_name[match.start() + len("Level"):]
    digits = re.match(r"\d+(?:and\d+)*", rest).group(0).replace("and", "")
    return [f"Level {d}" for d in digits]


def _values(node: Any) -> List[Any]:
    """Collect enum/const values from a property schema, including oneOf/anyOf."""
    out: List[Any] = []
    if not isinstance(node, dict):
        return out
    if "const" in node:
        out.append(node["const"])
    out.extend(node.get("enum") or [])
    for key in ("oneOf", "anyOf", "allOf"):
        for sub in node.get(key) or []:
            out.extend(_values(sub))
    if isinstance(node.get("items"), dict):
        out.extend(_values(node["items"]))
    return out


def collect_properties(body: Dict[str, Any]) -> tuple:
    """Return (properties dict, required list, unresolved_ref flag)."""
    props = dict(body.get("properties") or {})
    required = list(body.get("required") or [])
    unresolved = False
    if not props:
        for entry in body.get("allOf") or []:
            if not isinstance(entry, dict):
                continue
            if "$ref" in entry:
                unresolved = True
            props.update(entry.get("properties") or {})
            required.extend(entry.get("required") or [])
    return props, sorted(set(required)), unresolved


def describe(namespace: str, org: str, name: str, version: str, body: Dict) -> Dict:
    """Build the catalog entry for one schema body."""
    props, required, unresolved = collect_properties(body)
    assay_props = sorted(p for p in props if "assay" in p.lower())
    assay_values = set()
    for p in assay_props:
        for v in _values(props[p]):
            assay_values.add(v if isinstance(v, str) else json.dumps(v))
    entry = {
        "namespace": namespace,
        "organization": org,
        "schemaName": name,
        "version": version,
        "uri": f"{org}-{name}-{version}",
        "title": body.get("title"),
        "description": body.get("description"),
        "level": parse_levels(name),
        "properties": sorted(props),
        "required": required,
        "assay_values": sorted(assay_values),
        "assay_property": assay_props,
    }
    if unresolved:
        entry["properties_unresolved"] = True
    return entry


def build_catalog(syn: synapseclient.Synapse) -> Dict[str, Any]:
    """Walk every configured namespace and assemble the catalog."""
    schemas: List[Dict] = []
    skipped: List[Dict] = []
    for cfg in NAMESPACES:
        ns, org = cfg["namespace"], cfg["organization"]
        names = sorted(
            {s["schemaName"] for s in paginate(syn, "/schema/list", {"organizationName": org})}
        )
        if cfg["name_prefix"]:
            names = [n for n in names if n.startswith(cfg["name_prefix"])]
        log.info("%s: %d schemas in %s", ns, len(names), org)
        skipped_before = len(skipped)
        included = 0
        for name in names:
            try:
                infos = paginate(
                    syn,
                    "/schema/version/list",
                    {"organizationName": org, "schemaName": name},
                )
                versions = [i["semanticVersion"] for i in infos if i.get("semanticVersion")]
                if cfg["version"] == "latest":
                    if not versions:
                        skipped.append({"organization": org, "schemaName": name, "reason": "no semantic versions registered"})
                        continue
                    version = max(versions, key=version_key)
                elif cfg["version"] in versions:
                    version = cfg["version"]
                else:
                    skipped.append({
                        "organization": org,
                        "schemaName": name,
                        "reason": f"no {cfg['version']} version (available: {', '.join(sorted(versions, key=version_key)) or 'none'})",
                    })
                    continue
                uri = f"{org}-{name}-{version}"
                body = syn.restGET(
                    f"/schema/type/registered/{urllib.parse.quote(uri, safe='')}"
                )
                schemas.append(describe(ns, org, name, version, body or {}))
                included += 1
            except Exception as err:  # noqa: BLE001 - record and continue
                log.warning("%s %s failed: %s", ns, name, err)
                skipped.append({"organization": org, "schemaName": name, "reason": f"fetch failed: {err}"})
        log.info(
            "%s: %d included, %d skipped",
            ns, included, len(skipped) - skipped_before,
        )
    schemas.sort(key=lambda s: (s["namespace"], s["schemaName"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "namespaces": NAMESPACES,
        "schemas": schemas,
        "skipped": skipped,
    }


def main() -> None:
    syn = synapseclient.login(silent=True)
    catalog = build_catalog(syn)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=1, ensure_ascii=False)
    log.info("Wrote %d schemas (%d skipped) to %s", len(catalog["schemas"]), len(catalog["skipped"]), OUTPUT_JSON)


if __name__ == "__main__":
    main()
