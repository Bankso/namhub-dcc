"""Create folders and Curator curation tasks in a Synapse project from a CSV.

Each CSV row names a folder, a registered JSON schema, and the task type:

- ``record``: a RecordSet bound to the schema, a CurationTask and a Grid.
- ``file``: the schema is bound to the folder, plus a file view of the
  folder, a CurationTask and a wiki that shows the view.

The folder is created under the parent if it does not exist.
"""

import argparse
import csv
import logging
import sys

from synapseclient.extensions.curator import (
    create_file_based_metadata_task,
    create_record_based_metadata_task,
)
from synapseclient.models import CurationTask, Folder

from namhub_dcc.auth import login

log = logging.getLogger(__name__)

COMMAND_NAME = "create-curation-tasks"
REQUIRED_COLUMNS = ("folder_name", "schema_uri", "record_or_file")
TASK_TYPES = ("record", "file")


def add_parser(subparsers):
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Create folders and curation tasks in a Synapse project from a CSV.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Synapse ID of the project that holds the curation tasks.",
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="CSV with the columns folder_name, schema_uri and record_or_file, "
        "plus upsert_keys (required for record rows; names separated by ';') "
        "and an optional task_name.",
    )
    parser.add_argument(
        "--parent-id",
        default=None,
        help="Synapse ID of the container for the folders. Default: the project.",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Synapse personal access token. If omitted, falls back to the "
        "SYNAPSE_AUTH_TOKEN environment variable or cached credentials.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check the CSV and the schemas, and print what would be created, "
        "without creating anything.",
    )
    parser.set_defaults(func=run)
    return parser


def entity_prefix(schema_uri):
    """Return ``<organization>_<name>`` for a URI such as ``NAMhub-Biospecimen-4.0.0``.

    It is the start of the RecordSet, file view and CurationTask names when a
    row has no ``task_name``.
    """
    parts = schema_uri.rsplit("-", 2)
    if len(parts) < 3 or not all(parts):
        raise ValueError(f"schema_uri {schema_uri!r} is not <organization>-<name>-<version>")
    return f"{parts[0]}_{parts[1]}"


def read_rows(path):
    """Read and check the CSV. Return a list of row dicts, or raise ValueError."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing the column(s): {', '.join(missing)}")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            row["record_or_file"] = row["record_or_file"].lower()
            row["upsert_keys"] = [k.strip() for k in row.get("upsert_keys", "").split(";") if k.strip()]
            if not row["folder_name"] or not row["schema_uri"]:
                raise ValueError(f"line {line}: folder_name and schema_uri must have a value")
            if row["record_or_file"] not in TASK_TYPES:
                raise ValueError(f"line {line}: record_or_file must be 'record' or 'file'")
            if row["record_or_file"] == "record" and not row["upsert_keys"]:
                raise ValueError(f"line {line}: a record row needs upsert_keys")
            try:
                prefix = entity_prefix(row["schema_uri"])
            except ValueError as error:
                raise ValueError(f"line {line}: {error}") from None
            row["prefix"] = row.get("task_name") or prefix
            rows.append(row)
    for column, label in (("folder_name", "folder_name"), ("prefix", "task name")):
        values = [r[column] for r in rows]
        repeated = sorted({v for v in values if values.count(v) > 1})
        if repeated:
            hint = " (set task_name on those rows)" if column == "prefix" else ""
            raise ValueError(f"{label} is repeated: {', '.join(repeated)}{hint}")
    return rows


def run(args):
    try:
        rows = read_rows(args.csv)
    except (OSError, ValueError) as error:
        sys.exit(f"CSV error: {error}")

    syn = login(args.auth_token)
    parent_id = args.parent_id or args.project_id

    # Check every schema before anything is created.
    descriptions = {}
    for uri in sorted({r["schema_uri"] for r in rows}):
        try:
            schema = syn.restGET(f"/schema/type/registered/{uri}")
        except Exception as error:  # noqa: BLE001 - report every unknown schema the same way
            sys.exit(f"Schema {uri} is not registered: {error}")
        descriptions[uri] = schema.get("description") or uri

    folders = {c["name"]: c["id"] for c in syn.getChildren(parent_id, includeTypes=["folder"])}
    tasks = {t.data_type for t in CurationTask.list(project_id=args.project_id, synapse_client=syn)}

    for row in rows:
        uri, kind = row["schema_uri"], row["record_or_file"]
        task_name = f"{row['prefix']}_CurationTask"
        if task_name in tasks:
            log.info("%s: skipped, task %r already exists", row["folder_name"], task_name)
            continue
        folder_id = folders.get(row["folder_name"])
        if args.dry_run:
            where = folder_id or f"new folder under {parent_id}"
            keys = f" keys={row['upsert_keys']}" if kind == "record" else ""
            log.info("%s: would create %s task %r in %s%s", row["folder_name"], kind, task_name, where, keys)
            continue
        if folder_id is None:
            folder_id = Folder(name=row["folder_name"], parent_id=parent_id).store(synapse_client=syn).id
            log.info("%s: created folder %s", row["folder_name"], folder_id)

        instructions = f"{descriptions[uri]} Schema: {uri}."
        if kind == "record":
            result = create_record_based_metadata_task(
                synapse_client=syn,
                project_id=args.project_id,
                folder_id=folder_id,
                record_set_name=f"{row['prefix']}_RecordSet",
                record_set_description=descriptions[uri][:1000],
                curation_task_name=task_name,
                upsert_keys=row["upsert_keys"],
                instructions=instructions,
                schema_uri=uri,
                bind_schema_to_record_set=True,
            )
            record_set, task = result[0], result[1]
            log.info(
                "%s: folder=%s recordset=%s task=%s keys=%s",
                row["folder_name"], folder_id, record_set.id, task.task_id, row["upsert_keys"],
            )
        else:
            view_id, task_id = create_file_based_metadata_task(
                synapse_client=syn,
                folder_id=folder_id,
                curation_task_name=task_name,
                instructions=instructions,
                attach_wiki=True,
                entity_view_name=f"{row['prefix']}_FileView",
                schema_uri=uri,
            )
            log.info("%s: folder=%s fileview=%s task=%s", row["folder_name"], folder_id, view_id, task_id)
