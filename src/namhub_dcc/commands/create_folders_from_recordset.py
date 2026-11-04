"""Create one Synapse Folder per row of a Synapse RecordSet under a fixed
parent container, using a column value from each row as the folder name.

A RecordSet is backed by a CSV file, so this downloads that file and reads
it with pandas to get the rows.
"""

import logging
import os
import tempfile

import pandas as pd
from synapseclient import Folder
from synapseclient.models import RecordSet

from namhub_dcc.auth import login

log = logging.getLogger(__name__)

COMMAND_NAME = "create-folders-from-recordset"


def add_parser(subparsers):
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Create one Synapse Folder per row of a Synapse RecordSet.",
        description=__doc__,
    )
    parser.add_argument(
        "--record-set-id",
        required=True,
        help="Synapse ID of the RecordSet to read rows from (e.g. syn76265683).",
    )
    parser.add_argument(
        "--name-column",
        required=True,
        help="Name of the RecordSet column whose value becomes each folder's name.",
    )
    parser.add_argument(
        "--parent-id",
        required=True,
        help="Synapse ID of the container that new folders are created under.",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Synapse personal access token. If omitted, falls back to the "
        "SYNAPSE_AUTH_TOKEN environment variable or cached credentials.",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Directory to download the RecordSet's underlying CSV to. "
        "Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without creating anything.",
    )
    parser.set_defaults(func=run)
    return parser


def _get_existing_folder_names(syn, parent_id):
    return {
        child["name"]
        for child in syn.getChildren(parent_id, includeTypes=["folder"])
    }


def _load_record_set_rows(syn, record_set_id, download_dir):
    record_set = RecordSet(id=record_set_id, path=download_dir).get(synapse_client=syn)
    if not record_set.path or not os.path.isfile(record_set.path):
        raise RuntimeError(f"RecordSet {record_set_id} did not download to a readable file.")
    return pd.read_csv(record_set.path)


def run(args):
    syn = login(args.auth_token)

    download_dir = args.download_dir or tempfile.mkdtemp(prefix="recordset_")
    log.info("Downloading RecordSet %s to %s", args.record_set_id, download_dir)
    rows = _load_record_set_rows(syn, args.record_set_id, download_dir)

    if args.name_column not in rows.columns:
        raise SystemExit(
            f"Column {args.name_column!r} not found in RecordSet "
            f"{args.record_set_id}. Available columns: {list(rows.columns)}"
        )

    existing_names = _get_existing_folder_names(syn, args.parent_id)
    log.info("Found %d existing folder(s) under %s", len(existing_names), args.parent_id)

    created, skipped_blank, skipped_existing, failed = 0, 0, 0, 0

    for _, row in rows.iterrows():
        raw_name = row[args.name_column]
        folder_name = str(raw_name).strip() if raw_name is not None else ""

        if not folder_name or folder_name.lower() == "nan":
            skipped_blank += 1
            continue

        if folder_name in existing_names:
            log.info("Skipping %r: folder already exists under %s", folder_name, args.parent_id)
            skipped_existing += 1
            continue

        if args.dry_run:
            log.info("[dry-run] Would create folder %r under %s", folder_name, args.parent_id)
            created += 1
            continue

        try:
            folder = syn.store(Folder(name=folder_name, parent=args.parent_id))
            log.info("Created folder %r (%s)", folder_name, folder.id)
            existing_names.add(folder_name)
            created += 1
        except Exception as e:  # noqa: BLE001 - log and continue; one failure must not abort the batch
            log.error("Failed to create folder %r: %s", folder_name, e)
            failed += 1

    log.info(
        "Done. created=%d skipped_existing=%d skipped_blank=%d failed=%d",
        created,
        skipped_existing,
        skipped_blank,
        failed,
    )
