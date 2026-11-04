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


@dataclass
class _CsvFormat:
    """The parts of a RecordSet's ``csvDescriptor`` needed to edit its CSV."""

    separator: str = ","
    quote: str = '"'
    escape: str = "\\"

    @classmethod
    def from_entity(cls, entity: dict) -> "_CsvFormat":
        descriptor = entity.get("csvDescriptor") or {}
        if descriptor.get("isFirstLineHeader") is False:
            raise ValueError(
                f"{entity.get('id')} has no header line, so its columns cannot be "
                "matched to schema properties."
            )
        return cls(
            separator=descriptor.get("separator") or cls.separator,
            quote=descriptor.get("quoteCharacter") or cls.quote,
            escape=descriptor.get("escapeCharacter") or cls.escape,
        )


def _read_header(path: str, fmt: _CsvFormat) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(
            f, delimiter=fmt.separator, quotechar=fmt.quote, escapechar=fmt.escape
        )
        return next(reader, [])


def _scan_csv(text: str, fmt: _CsvFormat, columns: list[str] | None = None):
    """Scan CSV text record by record.

    A quote character only opens a quoted field at the start of a field (the
    beginning of a record or just after a separator); anywhere else it is an
    ordinary character. Inside a quoted field the escape character protects a
    following quote or escape character, and a doubled quote is a literal
    quote. After the closing quote, characters up to the next separator or line
    break are ordinary.

    Returns ``(output, field_counts)``. ``field_counts`` has one entry per
    record (``None`` for an empty record). If ``columns`` is given, ``output``
    is the text with those columns appended (header names on the first record,
    one separator per column on each other non-empty record); otherwise it is
    the text unchanged.
    """
    header_suffix = "".join(fmt.separator + _quote_field(c, fmt) for c in columns or [])
    row_suffix = fmt.separator * len(columns or [])

    out = []
    counts = []
    in_quotes = False
    at_field_start = True
    fields = 1
    record_is_empty = True
    i = 0

    def end_record():
        nonlocal fields, record_is_empty, at_field_start
        if not counts:
            out.append(header_suffix)
        elif not record_is_empty:
            out.append(row_suffix)
        counts.append(None if record_is_empty else fields)
        fields = 1
        record_is_empty = True
        at_field_start = True

    while i < len(text):
        c = text[i]
        if in_quotes:
            if c == fmt.escape and text[i + 1 : i + 2] in (fmt.quote, fmt.escape):
                out.append(text[i : i + 2])
                i += 2
                continue
            if c == fmt.quote:
                if text[i + 1 : i + 2] == fmt.quote:
                    out.append(c * 2)
                    i += 2
                    continue
                in_quotes = False
            out.append(c)
            i += 1
            continue
        if c in "\r\n":
            line_end = "\r\n" if text.startswith("\r\n", i) else c
            end_record()
            out.append(line_end)
            i += len(line_end)
            continue
        if c == fmt.separator:
            fields += 1
            at_field_start = True
            record_is_empty = False
        elif c == fmt.quote and at_field_start:
            in_quotes = True
            at_field_start = False
            record_is_empty = False
        elif c != "﻿":
            at_field_start = False
            record_is_empty = False
        out.append(c)
        i += 1

    if text and not text.endswith(("\r", "\n")):
        end_record()

    return "".join(out), counts


def _check_field_counts(counts: list[int | None], expected: int, what: str) -> None:
    bad = [n for n, c in enumerate(counts, start=1) if c is not None and c != expected]
    if bad:
        shown = ", ".join(str(n) for n in bad[:5])
        more = f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
        raise ValueError(
            f"{what}: record(s) {shown}{more} do not have {expected} fields "
            "(record 1 is the header), so columns cannot be added safely. "
            "Nothing was changed."
        )


def _validate_csv_shape(text: str, fmt: _CsvFormat) -> int:
    """Check every non-empty record has as many fields as the header.

    Returns the header's field count.
    """
    _, counts = _scan_csv(text, fmt)
    if not counts or not counts[0]:
        raise ValueError("The CSV has no header line, so columns cannot be added.")
    _check_field_counts(counts, counts[0], "The CSV")
    return counts[0]


def _append_empty_columns(
    src: str, dest: str, columns: list[str], fmt: _CsvFormat
) -> None:
    """Copy a CSV, appending columns to the header and an empty field per row.

    The file is not re-serialized: every existing byte is kept, and only the new
    header names and one separator per new column are inserted at the end of
    each record (see ``_scan_csv`` for how records are found). Values
    (including multi-line ones) are never re-quoted or re-escaped.

    Raises:
        ValueError: If a non-empty record of the original does not have as many
            fields as the header, or the result does not have header fields +
            new columns in every record. Nothing is written.
    """
    with open(src, newline="", encoding="utf-8") as f:
        text = f.read()

    header_fields = _validate_csv_shape(text, fmt)
    new_text, _ = _scan_csv(text, fmt, columns)
    _, counts = _scan_csv(new_text, fmt)
    _check_field_counts(counts, header_fields + len(columns), "The updated CSV")

    with open(dest, "w", newline="", encoding="utf-8") as f:
        f.write(new_text)


def _quote_field(value: str, fmt: _CsvFormat) -> str:
    """Quote a header name if it contains a separator, quote or line break."""
    if any(ch in value for ch in (fmt.separator, fmt.quote, "\r", "\n")):
        return fmt.quote + value.replace(fmt.quote, fmt.quote * 2) + fmt.quote
    return value


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
            keys are not properties of the target schema, or the CSV cannot
            safely have columns added (no header, or a record whose field count
            differs from the header's). Nothing is changed.
        SynapseHTTPError: For Synapse errors other than those above, e.g. no
            permission to edit the RecordSet when applying the update. A dry
            run does not check edit permission.
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
    csv_format = _CsvFormat.from_entity(entity)

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
        header = _read_header(record_set.path, csv_format)
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
        if added:
            # Also in a dry run, so a preview reports a CSV that cannot be
            # updated. Without added columns the CSV is left untouched.
            with open(record_set.path, newline="", encoding="utf-8") as f:
                _validate_csv_shape(f.read(), csv_format)
        if dry_run or result.no_op:
            return result

        if added:
            # Keep the original file name for the new version.
            new_dir = Path(tmp) / "updated"
            new_dir.mkdir()
            new_path = str(new_dir / Path(record_set.path).name)
            _append_empty_columns(record_set.path, new_path, added, csv_format)
            record_set.path = new_path
            record_set.store(synapse_client=syn)
            result.new_version_created = True

    # Binding an already-bound entity replaces the binding.
    RecordSet(id=record_set_id).bind_schema(
        target, enable_derived_annotations=enable_derived, synapse_client=syn
    )
    return result
