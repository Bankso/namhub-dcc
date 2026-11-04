"""Create NAMHub "Landscape Task" record-based metadata curation tasks.

A Landscape Task is a Synapse RecordSet bound to the ``NAMhub-Landscape``
JSON schema, paired with a CurationTask and a Grid view, all created via
``synapseclient.extensions.curator.create_record_based_metadata_task``. This
wraps that call with NAMHub's conventions so a Landscape Task can be created
in any project/folder with a single function call.
"""

import re
from typing import Optional, Tuple

from synapseclient import Synapse
from synapseclient.extensions.curator import create_record_based_metadata_task
from synapseclient.models import CurationTask, Grid, JSONSchema, RecordSet

DEFAULT_RECORD_SET_NAME = "Curation"
DEFAULT_CURATION_TASK_NAME = "Landscape Task"
DEFAULT_INSTRUCTIONS = "Please fill in each row with the dataset information."
# Unversioned, so it resolves to the latest registered version at call time.
DEFAULT_SCHEMA_URI = "NAMhub-Landscape"
DEFAULT_UPSERT_KEY = "LandscapeId"

_SEMANTIC_VERSION = re.compile(r"-(\d+)\.(\d+)\.(\d+)$")


def resolve_schema_uri(
    schema_uri: str,
    *,
    synapse_client: Optional[Synapse] = None,
) -> str:
    """Pin a JSON schema URI to a semantic version.

    A URI that already names a version (``<ORG>-<NAME>-<MAJOR>.<MINOR>.<PATCH>``)
    is returned unchanged. An unversioned URI (``<ORG>-<NAME>``) is pinned to
    the latest registered version.

    ``create_record_based_metadata_task`` builds the RecordSet's columns from
    the latest version of the schema whatever version the URI names, but binds
    the exact URI it is given, so pinning to the latest keeps the two in step.

    Arguments:
        schema_uri: JSON schema URI, with or without a semantic version.
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Returns:
        The schema URI with a semantic version.

    Raises:
        ValueError: If the URI is malformed or the schema has no versions.
    """
    schema_uri = schema_uri.strip()

    if _SEMANTIC_VERSION.search(schema_uri):
        return schema_uri

    schema = JSONSchema.from_uri(schema_uri)
    versions = [
        version_info.semantic_version
        for version_info in schema.get_versions(synapse_client=synapse_client)
    ]

    if not versions:
        raise ValueError(f"No versions are registered for schema {schema_uri!r}.")

    latest = max(
        versions, key=lambda v: tuple(int(part) for part in v.split("-")[0].split("."))
    )

    return f"{schema_uri}-{latest}"


def create_landscape_task(
    folder_id: str,
    pi_name: str,
    record_set_name: str = DEFAULT_RECORD_SET_NAME,
    record_set_description: Optional[str] = None,
    curation_task_name: str = DEFAULT_CURATION_TASK_NAME,
    instructions: str = DEFAULT_INSTRUCTIONS,
    schema_uri: str = DEFAULT_SCHEMA_URI,
    upsert_key: str = DEFAULT_UPSERT_KEY,
    *,
    project_id: Optional[str] = None,
    synapse_client: Optional[Synapse] = None,
) -> Tuple[RecordSet, CurationTask, Grid]:
    """Create a NAMHub Landscape Task in the given Synapse folder.

    Arguments:
        folder_id: Synapse ID of the folder to create the Landscape Task
            RecordSet in.
        pi_name: Name of the PI this Landscape Task is for. Used to build
            the default record_set_description if one isn't given.
        record_set_name: Name for the RecordSet.
        record_set_description: Description for the RecordSet. Defaults to
            "Landscape task for <pi_name>".
        curation_task_name: Name for the CurationTask. Must be unique within
            the project, otherwise an existing CurationTask with the same
            name will be updated instead of a new one being created.
        instructions: Instructions shown to the curator filling in the task.
        schema_uri: JSON schema URI the RecordSet is bound to. An unversioned
            URI is pinned to the latest version (see ``resolve_schema_uri``).
        upsert_key: Column name that uniquely identifies rows for upsert.
        project_id: Synapse ID of the project the folder lives in. Optional and
            deprecated upstream: synapseclient infers the project from
            ``folder_id`` and removes this parameter in v5.0.0.
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Returns:
        Tuple of the created (RecordSet, CurationTask, Grid).
    """
    description = record_set_description or f"Landscape task for {pi_name}"

    return create_record_based_metadata_task(
        synapse_client=synapse_client,
        project_id=project_id,
        folder_id=folder_id,
        record_set_name=record_set_name,
        record_set_description=description,
        curation_task_name=curation_task_name,
        upsert_keys=[upsert_key],
        instructions=instructions,
        schema_uri=resolve_schema_uri(schema_uri, synapse_client=synapse_client),
    )
