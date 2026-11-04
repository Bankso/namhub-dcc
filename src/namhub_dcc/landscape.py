"""Create NAMHub "Landscape Task" record-based metadata curation tasks.

A Landscape Task is a Synapse RecordSet bound to the ``NAMhub-Landscape``
JSON schema, paired with a CurationTask and a Grid view, all created via
``synapseclient.extensions.curator.create_record_based_metadata_task``. This
wraps that call with NAMHub's conventions so a Landscape Task can be created
in any project/folder with a single function call.
"""

import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
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

log = logging.getLogger(__name__)

# Allowance for clock skew between this machine and Synapse when deciding
# whether an entity was created after a call started.
_CLOCK_TOLERANCE = timedelta(minutes=2)

_SEMANTIC_VERSION = re.compile(r"-(\d+)\.(\d+)\.(\d+)$")


def _name_with_pi(name: str, default: str, pi_name: str) -> str:
    name = name.strip()
    pi_name = pi_name.strip()

    if name == default and pi_name:
        return f"{default} - {pi_name}"

    return name


def effective_record_set_name(record_set_name: str, pi_name: str) -> str:
    """The RecordSet name actually used for a Landscape Task.

    A name left at ``DEFAULT_RECORD_SET_NAME`` gets `` - <pi_name>`` appended, so
    tasks created with the defaults each get their own RecordSet. Any other
    name is used unchanged (apart from surrounding whitespace).
    """
    return _name_with_pi(record_set_name, DEFAULT_RECORD_SET_NAME, pi_name)


def effective_curation_task_name(curation_task_name: str, pi_name: str) -> str:
    """The CurationTask name actually used for a Landscape Task.

    A name left at ``DEFAULT_CURATION_TASK_NAME`` gets `` - <pi_name>`` appended,
    so tasks created with the defaults each get their own CurationTask. Any
    other name is used unchanged (apart from surrounding whitespace).
    """
    return _name_with_pi(curation_task_name, DEFAULT_CURATION_TASK_NAME, pi_name)


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


def check_latest_matches_highest(
    schema_uri: str,
    *,
    synapse_client: Optional[Synapse] = None,
) -> None:
    """Check that Synapse's "latest" schema matches the highest version.

    ``resolve_schema_uri`` pins an unversioned URI to the highest-numbered
    version, but ``create_record_based_metadata_task`` builds the RecordSet's
    columns from whatever Synapse considers the latest version (e.g. the most
    recently registered). If their property names differ, the RecordSet's
    columns would not match its bound schema. A URI that already names a version
    is not checked.

    Arguments:
        schema_uri: JSON schema URI, with or without a semantic version.
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Raises:
        ValueError: If the property names of the latest and highest versions
            differ.
    """
    schema_uri = schema_uri.strip()
    if _SEMANTIC_VERSION.search(schema_uri):
        return

    syn = Synapse.get_client(synapse_client=synapse_client)
    pinned = resolve_schema_uri(schema_uri, synapse_client=syn)

    latest_body = JSONSchema.from_uri(schema_uri).get_body(synapse_client=syn)
    latest_props = set((latest_body or {}).get("properties") or {})
    pinned_body = syn.restGET(
        f"/schema/type/registered/{urllib.parse.quote(pinned, safe='')}"
    )
    pinned_props = set((pinned_body or {}).get("properties") or {})

    if latest_props != pinned_props:
        raise ValueError(
            f"Synapse's latest version of {schema_uri} does not match the "
            f"highest-numbered version {pinned}; "
            "create_record_based_metadata_task builds columns from the former, "
            "so the RecordSet's columns would not match its bound schema. "
            "Pass an explicit schema version or fix the schema registry."
        )


def find_existing_task_entities(
    folder_id: str,
    record_set_name: str,
    curation_task_name: str,
    *,
    synapse_client: Optional[Synapse] = None,
) -> list[str]:
    """Find entities a new Landscape Task would update instead of creating.

    ``create_record_based_metadata_task`` upserts: a RecordSet with the same
    name in the folder, or a CurationTask with the same name in the folder's
    project, is updated rather than created. Every new Landscape Task needs its
    own RecordSet and CurationTask, so callers should treat any match as an
    error.

    Arguments:
        folder_id: Synapse ID of the folder the RecordSet would be created in.
        record_set_name: Name of the RecordSet to be created.
        curation_task_name: Name of the CurationTask to be created.
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Returns:
        A description of each existing entity that would be reused; empty if
        neither exists.
    """
    syn = Synapse.get_client(synapse_client=synapse_client)
    existing = []

    record_set_id = syn.findEntityId(record_set_name, parent=folder_id)
    if record_set_id:
        existing.append(
            f"Folder {folder_id} already has an entity named {record_set_name!r} "
            f"({record_set_id})."
        )

    # The path runs from the root down; the entry after the root is the project.
    project_id = syn.restGET(f"/entity/{folder_id}/path")["path"][1]["id"]
    for task in CurationTask.list(project_id=project_id, synapse_client=syn):
        if task.data_type == curation_task_name:
            existing.append(
                f"Project {project_id} already has a curation task named "
                f"{curation_task_name!r} ({task.task_id})."
            )
            break

    return existing


def _parse_time(value: str) -> datetime:
    """Parse a Synapse ISO-8601 timestamp into an aware datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _created_by_us_since(
    created_by: object, created_on: Optional[str], user_id: str, since: datetime
) -> bool:
    """Whether an entity was created by this user after ``since`` (minus a
    small tolerance for clock skew)."""
    if str(created_by) != str(user_id) or not created_on:
        return False
    return _parse_time(created_on) >= since - _CLOCK_TOLERANCE


def _cleanup_failed_task(
    syn: Synapse,
    folder_id: str,
    record_set_name: str,
    curation_task_name: str,
    started: datetime,
) -> None:
    """Delete the CurationTask and RecordSet a failed creation left behind.

    Only entities created by the current user after ``started`` are deleted.
    Never raises: failures are logged with the IDs of what was left behind.
    """
    orphans = []

    try:
        user_id = str(syn.getUserProfile()["ownerId"])
        project_id = syn.restGET(f"/entity/{folder_id}/path")["path"][1]["id"]
    except Exception:  # cleanup must not mask the original error
        log.exception(
            "Could not clean up after a failed Landscape Task creation; check "
            "folder %s for a RecordSet %r and project for a CurationTask %r.",
            folder_id,
            record_set_name,
            curation_task_name,
        )
        return

    task_id = None
    try:
        for task in CurationTask.list(project_id=project_id, synapse_client=syn):
            if task.data_type != curation_task_name:
                continue
            task_id = task.task_id
            if _created_by_us_since(task.created_by, task.created_on, user_id, started):
                task.delete(synapse_client=syn)
                log.info(
                    "Deleted CurationTask %r (%s) left by a failed creation.",
                    curation_task_name,
                    task.task_id,
                )
            else:
                log.warning(
                    "Not deleting CurationTask %s: not created by this call.",
                    task.task_id,
                )
            break
    except Exception:  # cleanup must not mask the original error
        orphans.append(f"CurationTask {task_id or repr(curation_task_name)}")
        log.exception("Could not delete the CurationTask left by a failed creation.")

    record_set_id = None
    try:
        record_set_id = syn.findEntityId(record_set_name, parent=folder_id)
        if record_set_id:
            entity = syn.restGET(f"/entity/{record_set_id}")
            if _created_by_us_since(
                entity.get("createdBy"), entity.get("createdOn"), user_id, started
            ):
                # A Grid session belongs to the RecordSet but outlives it.
                for grid in Grid.list(source_id=record_set_id, synapse_client=syn):
                    grid.delete(synapse_client=syn)
                RecordSet(id=record_set_id).delete(synapse_client=syn)
                log.info(
                    "Deleted RecordSet %r (%s) left by a failed creation.",
                    record_set_name,
                    record_set_id,
                )
            else:
                log.warning(
                    "Not deleting RecordSet %s: not created by this call.",
                    record_set_id,
                )
    except Exception:  # cleanup must not mask the original error
        orphans.append(
            f"RecordSet {record_set_id or repr(record_set_name) + ' in ' + folder_id}"
        )
        log.exception("Could not delete the RecordSet left by a failed creation.")

    if orphans:
        log.error("Entities left behind, delete them manually: %s", "; ".join(orphans))


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
        record_set_name: Name for the RecordSet. Must be unique within the
            folder. If left at the default, " - <pi_name>" is appended (see
            ``effective_record_set_name``); any other name is used as given.
        record_set_description: Description for the RecordSet. Defaults to
            "Landscape task for <pi_name>".
        curation_task_name: Name for the CurationTask. Must be unique within
            the folder's project. If left at the default, " - <pi_name>" is
            appended (see ``effective_curation_task_name``); any other name is
            used as given.
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

        If creation fails part-way, the RecordSet and CurationTask this call
        created are deleted (and any Grid session on the RecordSet) before the
        original exception is re-raised, so the call can simply be retried.

    Raises:
        ValueError: If a RecordSet or CurationTask with the given name already
            exists (see ``find_existing_task_entities``), since it would be
            updated instead of a new one being created; or if an unversioned
            ``schema_uri`` has a "latest" version that differs from its
            highest-numbered one (see ``check_latest_matches_highest``).
        SynapseError: For Synapse errors, e.g. no permission on the folder. Any
            entities this call already created are deleted first.
    """
    check_latest_matches_highest(schema_uri, synapse_client=synapse_client)

    record_set_name = effective_record_set_name(record_set_name, pi_name)
    curation_task_name = effective_curation_task_name(curation_task_name, pi_name)

    existing = find_existing_task_entities(
        folder_id, record_set_name, curation_task_name, synapse_client=synapse_client
    )
    if existing:
        raise ValueError(
            "A new Landscape Task needs its own RecordSet and CurationTask. "
            + " ".join(existing)
        )

    description = record_set_description or f"Landscape task for {pi_name}"

    syn = Synapse.get_client(synapse_client=synapse_client)
    started = datetime.now(timezone.utc)

    try:
        return create_record_based_metadata_task(
            synapse_client=syn,
            project_id=project_id,
            folder_id=folder_id,
            record_set_name=record_set_name,
            record_set_description=description,
            curation_task_name=curation_task_name,
            upsert_keys=[upsert_key],
            instructions=instructions,
            schema_uri=resolve_schema_uri(schema_uri, synapse_client=syn),
        )
    except Exception:
        # Entities stored before the failure would block a retry (the
        # duplicate check above), so remove them, then re-raise the original.
        _cleanup_failed_task(
            syn, folder_id, record_set_name, curation_task_name, started
        )
        raise
