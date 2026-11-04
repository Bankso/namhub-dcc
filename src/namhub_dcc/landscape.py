"""Create NAMHub "Landscape Task" record-based metadata curation tasks.

A Landscape Task is a Synapse RecordSet bound to the ``NAMhub-Landscape``
JSON schema, paired with a CurationTask and a Grid view, all created via
``synapseclient.extensions.curator.create_record_based_metadata_task``. This
wraps that call with NAMHub's conventions so a Landscape Task can be created
in any project/folder with a single function call.
"""

from synapseclient import Synapse
from synapseclient.extensions.curator import create_record_based_metadata_task
from synapseclient.models import CurationTask, Grid, RecordSet

DEFAULT_RECORD_SET_NAME = "Curation"
DEFAULT_CURATION_TASK_NAME = "Landscape Task"
DEFAULT_INSTRUCTIONS = "Please fill in each row with the dataset information."
DEFAULT_SCHEMA_URI = "NAMhub-Landscape-1.0.0"
DEFAULT_UPSERT_KEY = "LandscapeId"


def create_landscape_task(
    project_id: str,
    folder_id: str,
    pi_name: str,
    record_set_name: str = DEFAULT_RECORD_SET_NAME,
    record_set_description: str | None = None,
    curation_task_name: str = DEFAULT_CURATION_TASK_NAME,
    instructions: str = DEFAULT_INSTRUCTIONS,
    schema_uri: str = DEFAULT_SCHEMA_URI,
    upsert_key: str = DEFAULT_UPSERT_KEY,
    *,
    synapse_client: Synapse | None = None,
) -> tuple[RecordSet, CurationTask, Grid]:
    """Create a NAMHub Landscape Task in the given Synapse project/folder.

    Arguments:
        project_id: Synapse ID of the project the folder lives in.
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
        schema_uri: JSON schema URI the RecordSet is bound to.
        upsert_key: Column name that uniquely identifies rows for upsert.
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
        schema_uri=schema_uri,
    )
