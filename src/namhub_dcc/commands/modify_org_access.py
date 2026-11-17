"""Add access for a user or team to a Synapse JSON schema organization.

Access types the principal already has are kept.
"""

import logging

from namhub_dcc.auth import login
from namhub_dcc.modify_org_access import modify_org_access

log = logging.getLogger(__name__)

COMMAND_NAME = "modify-org-access"


def add_parser(subparsers):
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Add access for a user or team to a JSON schema organization.",
        description=__doc__,
    )
    parser.add_argument(
        "--org-name",
        required=True,
        help="Name of the JSON schema organization.",
    )
    parser.add_argument(
        "--principal-id",
        required=True,
        type=int,
        help="Numeric principal ID of the user or team to give access to.",
    )
    parser.add_argument(
        "--access-type",
        required=True,
        nargs="+",
        help="One or more access types to add (for example: READ CREATE UPDATE).",
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

    access = modify_org_access(
        org_name=args.org_name,
        principal_id=args.principal_id,
        access_type=args.access_type,
        synapse_client=syn,
    )

    log.info(
        "Principal %s now has %s on organization %r",
        args.principal_id,
        access,
        args.org_name,
    )
