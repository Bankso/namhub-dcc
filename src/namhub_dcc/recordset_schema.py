"""Update the JSON schema bound to an existing Synapse RecordSet.

A RecordSet is a CSV file entity whose columns are its CSV header. A RecordSet
bound to a JSON schema drives the validation of its linked CurationTask, so
moving a RecordSet to a newer schema version means both rebinding the schema
and making sure the CSV has a column for every property of the new version.
"""

import csv
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from synapseclient import Synapse
from synapseclient.core.exceptions import SynapseHTTPError
from synapseclient.models import Grid, RecordSet

from namhub_dcc.landscape import resolve_schema_uri

RECORD_SET_CONCRETE_TYPE = "org.sagebionetworks.repo.model.RecordSet"


@dataclass
class RecordSetSchemaUpdate:
    """The outcome (or, for a dry run, the plan) of a RecordSet schema update."""

    record_set_id: str
    previous_schema_uri: str | None
    new_schema_uri: str
    added_columns: list[str] = field(default_factory=list)
    """Schema properties missing from the CSV; appended as empty columns."""
    extra_columns: list[str] = field(default_factory=list)
    """CSV columns that are not properties of the new schema (kept as-is)."""
    warnings: list[str] = field(default_factory=list)
    new_version_created: bool = False
    no_op: bool = False
    dry_run: bool = False


def _get_bound_schema(syn: Synapse, record_set_id: str) -> dict | None:
    """The RecordSet's schema binding, or None if no schema is bound."""
    try:
        return syn.restGET(f"/entity/{record_set_id}/schema/binding")
    except SynapseHTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def _get_schema_properties(syn: Synapse, schema_uri: str) -> list[str]:
    """Property names of exactly the version named by ``schema_uri``."""
    encoded = urllib.parse.quote(schema_uri, safe="")
    try:
        schema = syn.restGET(f"/schema/type/registered/{encoded}")
    except SynapseHTTPError as e:
        raise ValueError(f"Could not load JSON schema {schema_uri!r}: {e}") from None
    properties = schema.get("properties", {})
    return list(properties) if isinstance(properties, dict) else []


def _read_header(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f), [])


def _append_empty_columns(src: str, dest: str, columns: list[str]) -> None:
    """Copy a CSV, appending empty columns; existing columns and rows are kept."""
    with (
        open(src, newline="", encoding="utf-8-sig") as fin,
        open(dest, "w", newline="", encoding="utf-8") as fout,
    ):
        reader = csv.reader(fin)
        writer = csv.writer(fout)
        header = next(reader, [])
        writer.writerow(header + columns)
        for row in reader:
            # Pad short rows so the new columns line up with the new header.
            writer.writerow(row + [""] * (len(header) - len(row)) + [""] * len(columns))


def _grid_session_warnings(syn: Synapse, record_set_id: str) -> list[str]:
    """Warn about the caller's open Grid sessions on the RecordSet.

    Synapse only lists the *calling user's* sessions, so a session another
    curator has open cannot be detected here.
    """
    try:
        sessions = list(Grid.list(source_id=record_set_id, synapse_client=syn))
    except Exception:  # noqa: BLE001 - detection is best-effort only
        return []
    if not sessions:
        return []
    message = (
        f"You have {len(sessions)} open Grid session(s) on {record_set_id}. Grid "
        "sessions keep the schema and columns they were started with, so close "
        "and restart them to pick up the update. Sessions opened by other "
        "users cannot be detected."
    )
    return [message]


def update_recordset_schema(
    record_set_id: str,
    schema_uri: str | None = None,
    *,
    dry_run: bool = False,
    synapse_client: Synapse | None = None,
) -> RecordSetSchemaUpdate:
    """Move a RecordSet to a (newer) JSON schema version.

    By default the target is the latest version of the schema the RecordSet is
    currently bound to. Any property of the target schema missing from the
    RecordSet's CSV header is appended as an empty column (stored as a new file
    version); existing columns and rows are never changed or removed. The
    RecordSet is then bound to the target schema, which updates the validation
    of its linked CurationTask.

    Arguments:
        record_set_id: Synapse ID of the RecordSet.
        schema_uri: JSON schema URI to move to. An unversioned URI resolves to
            the latest version. Defaults to the latest version of the
            currently bound schema.
        dry_run: If True, compute and return the plan without changing anything.
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Returns:
        A ``RecordSetSchemaUpdate`` describing what was (or would be) done.

    Raises:
        ValueError: If the ID is not a RecordSet, no schema is bound and none
            was given, the schema cannot be loaded, or the RecordSet's upsert
            keys are not properties of the target schema. Nothing is changed.
    """
    syn = Synapse.get_client(synapse_client=synapse_client)
    record_set_id = record_set_id.strip()

    try:
        entity = syn.restGET(f"/entity/{record_set_id}")
    except SynapseHTTPError as e:
        raise ValueError(f"Could not read {record_set_id}: {e}") from None
    if entity.get("concreteType") != RECORD_SET_CONCRETE_TYPE:
        kind = entity.get("concreteType", "unknown").rsplit(".", 1)[-1]
        raise ValueError(f"{record_set_id} is a {kind}, not a RecordSet.")
    upsert_keys = entity.get("upsertKey") or []

    binding = _get_bound_schema(syn, record_set_id)
    previous_uri = None
    enable_derived = False
    if binding:
        previous_uri = binding["jsonSchemaVersionInfo"]["$id"]
        enable_derived = bool(binding.get("enableDerivedAnnotations", False))

    if schema_uri and schema_uri.strip():
        target = resolve_schema_uri(schema_uri, synapse_client=syn)
    elif binding:
        info = binding["jsonSchemaVersionInfo"]
        target = resolve_schema_uri(
            f"{info['organizationName']}-{info['schemaName']}", synapse_client=syn
        )
    else:
        raise ValueError(
            f"{record_set_id} has no JSON schema bound; pass a schema URI."
        )

    schema_properties = _get_schema_properties(syn, target)
    missing_keys = [key for key in upsert_keys if key not in schema_properties]
    if missing_keys:
        raise ValueError(
            f"Upsert key(s) {missing_keys} of {record_set_id} are not properties "
            f"of {target}."
        )

    with tempfile.TemporaryDirectory() as tmp:
        record_set = RecordSet(id=record_set_id, path=tmp).get(synapse_client=syn)
        header = _read_header(record_set.path)
        added = [p for p in schema_properties if p not in header]
        extra = [c for c in header if c not in schema_properties]

        result = RecordSetSchemaUpdate(
            record_set_id=record_set_id,
            previous_schema_uri=previous_uri,
            new_schema_uri=target,
            added_columns=added,
            extra_columns=extra,
            no_op=(previous_uri == target and not added),
            dry_run=dry_run,
        )
        result.warnings = _grid_session_warnings(syn, record_set_id)
        if dry_run or result.no_op:
            return result

        if added:
            # Keep the original file name for the new version.
            new_dir = Path(tmp) / "updated"
            new_dir.mkdir()
            new_path = str(new_dir / Path(record_set.path).name)
            _append_empty_columns(record_set.path, new_path, added)
            record_set.path = new_path
            record_set.store(synapse_client=syn)
            result.new_version_created = True

    # Binding an already-bound entity replaces the binding.
    RecordSet(id=record_set_id).bind_schema(
        target, enable_derived_annotations=enable_derived, synapse_client=syn
    )
    return result
