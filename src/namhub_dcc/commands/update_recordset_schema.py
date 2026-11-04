"""Update the JSON schema bound to existing Synapse RecordSets (and, through
them, the validation of their linked curation tasks). Missing schema columns
are appended to each RecordSet as empty columns; nothing is removed.
"""

import logging

from synapseclient.core.exceptions import SynapseError

from namhub_dcc.auth import login
from namhub_dcc.recordset_schema import update_recordset_schema

log = logging.getLogger(__name__)

COMMAND_NAME = "update-recordset-schema"


def add_parser(subparsers):
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Update the JSON schema bound to existing RecordSets.",
        description=__doc__,
    )
    parser.add_argument(
        "--record-set-id",
        required=True,
        nargs="+",
        help="Synapse ID(s) of the RecordSet(s) to update.",
    )
    parser.add_argument(
        "--schema-uri",
        default=None,
        help="JSON schema URI to move to. A URI without a version uses the "
        "latest registered version. Default: the latest version of each "
        "RecordSet's currently bound schema.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without changing anything.",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Synapse personal access token. If omitted, falls back to the "
        "SYNAPSE_AUTH_TOKEN environment variable or cached credentials.",
    )
    parser.set_defaults(func=run)
    return parser


def run(args):
    syn = login(args.auth_token)
    failed = 0

    for record_set_id in args.record_set_id:
        try:
            result = update_recordset_schema(
                record_set_id,
                args.schema_uri,
                dry_run=args.dry_run,
                synapse_client=syn,
            )
        except (ValueError, SynapseError) as e:
            # E.g. not a RecordSet, nothing bound, or upsert key not in schema,
            # or a Synapse error such as no edit permission.
            log.error("%s: %s", record_set_id, e)
            failed += 1
            continue

        log.info(
            "%s: %s -> %s",
            record_set_id,
            result.previous_schema_uri or "(no schema)",
            result.new_schema_uri,
        )
        log.info(
            "%s: columns to add (%d): %s",
            record_set_id,
            len(result.added_columns),
            ", ".join(result.added_columns) or "none",
        )
        if result.extra_columns:
            log.warning(
                "%s: columns not in the new schema, kept as-is (%d): %s",
                record_set_id,
                len(result.extra_columns),
                ", ".join(result.extra_columns),
            )
        for warning in result.warnings:
            log.warning("%s: %s", record_set_id, warning)

        if result.no_op:
            log.info("%s: already up to date; nothing to do.", record_set_id)
        elif args.dry_run:
            log.info("%s: dry run; nothing changed.", record_set_id)
        else:
            log.info(
                "%s: bound to %s%s.",
                record_set_id,
                result.new_schema_uri,
                " and stored a new version with the added columns"
                if result.new_version_created
                else "",
            )

    if failed:
        raise SystemExit(1)
