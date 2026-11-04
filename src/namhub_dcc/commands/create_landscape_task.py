"""Create a NAMHub Landscape Task (a record-based metadata curation task,
bound to the NAMhub-Landscape JSON schema) in a Synapse project folder.
"""

import logging

from namhub_dcc.auth import login
from namhub_dcc.landscape import (
    DEFAULT_CURATION_TASK_NAME,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_RECORD_SET_NAME,
    DEFAULT_SCHEMA_URI,
    DEFAULT_UPSERT_KEY,
    create_landscape_task,
)

log = logging.getLogger(__name__)

COMMAND_NAME = "create-landscape-task"


def add_parser(subparsers):
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Create a NAMHub Landscape Task in a Synapse project folder.",
        description=__doc__,
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Synapse ID of the project the folder lives in.",
    )
    parser.add_argument(
        "--folder-id",
        required=True,
        help="Synapse ID of the folder to create the Landscape Task RecordSet in.",
    )
    parser.add_argument(
        "--pi-name",
        required=True,
        help="Name of the PI this Landscape Task is for. Used to build the "
        "default RecordSet description.",
    )
    parser.add_argument(
        "--record-set-name",
        default=DEFAULT_RECORD_SET_NAME,
        help=f"Name for the RecordSet. Default: {DEFAULT_RECORD_SET_NAME!r}.",
    )
    parser.add_argument(
        "--record-set-description",
        default=None,
        help="Description for the RecordSet. Defaults to "
        "'Landscape task for <pi-name>'.",
    )
    parser.add_argument(
        "--curation-task-name",
        default=DEFAULT_CURATION_TASK_NAME,
        help="Name for the CurationTask. Must be unique within the project, "
        "otherwise a matching existing task will be updated instead of a "
        f"new one being created. Default: {DEFAULT_CURATION_TASK_NAME!r}.",
    )
    parser.add_argument(
        "--instructions",
        default=DEFAULT_INSTRUCTIONS,
        help="Instructions shown to the curator filling in the task.",
    )
    parser.add_argument(
        "--schema-uri",
        default=DEFAULT_SCHEMA_URI,
        help=f"JSON schema URI to bind the RecordSet to. Default: {DEFAULT_SCHEMA_URI!r}.",
    )
    parser.add_argument(
        "--upsert-key",
        default=DEFAULT_UPSERT_KEY,
        help=f"Column name that uniquely identifies rows for upsert. Default: {DEFAULT_UPSERT_KEY!r}.",
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

    record_set, curation_task, _grid = create_landscape_task(
        project_id=args.project_id,
        folder_id=args.folder_id,
        pi_name=args.pi_name,
        record_set_name=args.record_set_name,
        record_set_description=args.record_set_description,
        curation_task_name=args.curation_task_name,
        instructions=args.instructions,
        schema_uri=args.schema_uri,
        upsert_key=args.upsert_key,
        synapse_client=syn,
    )

    log.info("Created RecordSet %r (%s)", record_set.name, record_set.id)
    log.info(
        "Created CurationTask %r (%s)", args.curation_task_name, curation_task.task_id
    )
    log.info("Created Grid view for RecordSet %s", record_set.id)
