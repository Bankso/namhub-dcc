"""Subcommands for the ``namhub-dcc`` CLI.

Each module here exposes an ``add_parser(subparsers)`` function that
registers its subcommand (including a ``func`` default used to run it).
"""

from namhub_dcc.commands import (
    create_folders_from_recordset,
    create_landscape_task,
    update_recordset_schema,
)

COMMAND_MODULES = [
    create_folders_from_recordset,
    create_landscape_task,
    update_recordset_schema,
]
