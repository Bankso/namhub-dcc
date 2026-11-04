import re
import urllib.parse
import uuid

import streamlit as st
from synapseclient import Synapse
from synapseclient.core.exceptions import (
    SynapseAuthenticationError,
    SynapseHTTPError,
    SynapseNoCredentialsError,
)

from namhub_dcc.auth import login
from namhub_dcc.landscape import (
    DEFAULT_CURATION_TASK_NAME,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_RECORD_SET_NAME,
    DEFAULT_SCHEMA_URI,
    DEFAULT_UPSERT_KEY,
    create_landscape_task,
)

# -----------------------------------------------------------------------------
# App configuration
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Create Landscape Metadata Tasks",
    page_icon="🗂️",
    layout="wide",
)

st.title("🗂️ Create Landscape Metadata Tasks")
st.write(
    "Create one or more Landscape metadata tasks across different Synapse folders."
)


# -----------------------------------------------------------------------------
# Synapse login
# -----------------------------------------------------------------------------
auth_token = st.sidebar.text_input(
    "Synapse personal access token",
    type="password",
    help=(
        "Optional. If blank, falls back to the SYNAPSE_AUTH_TOKEN environment "
        "variable or ~/.synapseConfig."
    ),
)

if st.session_state.get("syn_token") != auth_token:
    # Token changed: drop any client from a previous login.
    st.session_state.pop("syn", None)
    st.session_state["syn_token"] = auth_token

if "syn" not in st.session_state:
    try:
        st.session_state["syn"] = login(auth_token or None)
    except (
        SynapseAuthenticationError,
        SynapseNoCredentialsError,
        SynapseHTTPError,
    ) as e:
        st.error(
            "Could not log into Synapse. Enter a personal access token in the "
            "sidebar or make sure valid Synapse credentials are configured."
        )
        st.exception(e)
        st.stop()
    except Exception as e:  # noqa: BLE001 - show any login failure to the user
        st.error("Unexpected error while logging into Synapse.")
        st.exception(e)
        st.stop()

syn = st.session_state["syn"]
st.success("Logged into Synapse.")


# -----------------------------------------------------------------------------
# Schema loading
# -----------------------------------------------------------------------------
# The leading underscore tells st.cache_data not to hash the client.
@st.cache_data(show_spinner=False)
def load_registered_schema(_syn: Synapse, schema_uri: str) -> dict:
    """
    Load a registered Synapse schema using the schema URI.

    Example schema URI: NAMhub-Landscape-1.0.0
    """
    schema_uri = schema_uri.strip()

    if not schema_uri:
        return {}

    encoded_schema_uri = urllib.parse.quote(schema_uri, safe="")

    return _syn.restGET(f"/schema/type/registered/{encoded_schema_uri}")


@st.cache_data(show_spinner=False)
def get_schema_properties(_syn: Synapse, schema_uri: str) -> list[str]:
    """
    Return the schema properties as column headers.
    """
    schema = load_registered_schema(_syn, schema_uri)

    properties = schema.get("properties", {})

    if not isinstance(properties, dict):
        return []

    return list(properties.keys())


def render_schema_properties(schema_uri: str, upsert_key: str):
    """
    Display the schema properties and mark the selected upsert key with an icon.
    """
    st.markdown("#### Schema Properties")

    if not schema_uri.strip():
        st.info("Enter a Schema URI to load schema properties.")
        return

    try:
        properties = get_schema_properties(syn, schema_uri)
    except (SynapseHTTPError, ValueError) as e:
        st.error(f"Could not load schema properties for `{schema_uri}`.")
        st.exception(e)
        return

    if not properties:
        st.warning(f"No properties found for schema `{schema_uri}`.")
        return

    rows = []

    for prop in properties:
        rows.append(
            {
                "Upsert Key": "🔑" if prop == upsert_key else "",
                "Column Header": prop,
            }
        )

    st.caption("The key icon marks the column currently selected as the upsert key.")

    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
    )


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------
def new_task() -> dict:
    return {
        "id": uuid.uuid4().hex,
        "pi_name": "",
        "project_id": "",
        "folder_id": "",
        "record_set_name": DEFAULT_RECORD_SET_NAME,
        "record_set_description": "",
        "curation_task_name": DEFAULT_CURATION_TASK_NAME,
        "instructions": DEFAULT_INSTRUCTIONS,
        "schema_uri": DEFAULT_SCHEMA_URI,
        "upsert_key": DEFAULT_UPSERT_KEY,
    }


if "landscapes" not in st.session_state:
    st.session_state.landscapes = [new_task()]


def add_landscape():
    st.session_state.landscapes.append(new_task())


def remove_landscape(task_id: str):
    st.session_state.landscapes = [
        task for task in st.session_state.landscapes if task["id"] != task_id
    ]


def effective_curation_task_name(task: dict) -> str:
    """
    Curation task names must be unique within a project, so when the user
    leaves the default name in place, make it unique by appending the PI name.
    """
    name = task["curation_task_name"].strip()
    pi_name = task["pi_name"].strip()

    if name == DEFAULT_CURATION_TASK_NAME and pi_name:
        return f"{DEFAULT_CURATION_TASK_NAME} - {pi_name}"

    return name


# -----------------------------------------------------------------------------
# Form rendering
# -----------------------------------------------------------------------------
st.subheader("Landscape Tasks")

for i, landscape in enumerate(st.session_state.landscapes):
    task_id = landscape["id"]

    with st.expander(f"Landscape Task {i + 1}", expanded=True):
        left_col, right_col = st.columns(2)

        with left_col:
            pi_name = st.text_input(
                "PI Name",
                value=landscape["pi_name"],
                key=f"pi_name_{task_id}",
                placeholder="Example: Jane Doe",
            )

            project_id = st.text_input(
                "Synapse Project ID",
                value=landscape["project_id"],
                key=f"project_id_{task_id}",
                placeholder="Example: syn12345678",
            )

            folder_id = st.text_input(
                "Synapse Folder ID",
                value=landscape["folder_id"],
                key=f"folder_id_{task_id}",
                placeholder="Example: syn12345678",
            )

            record_set_name = st.text_input(
                "Record Set Name",
                value=landscape["record_set_name"],
                key=f"record_set_name_{task_id}",
            )

            curation_task_name = st.text_input(
                "Curation Task Name",
                value=landscape["curation_task_name"],
                key=f"curation_task_name_{task_id}",
                help=(
                    "Must be unique within the project. If left at the "
                    f"default ({DEFAULT_CURATION_TASK_NAME!r}), the PI name is "
                    "appended automatically."
                ),
            )

        with right_col:
            schema_uri = st.text_input(
                "Schema URI",
                value=landscape["schema_uri"],
                key=f"schema_uri_{task_id}",
                placeholder="Example: NAMhub-Landscape-1.0.0",
            )

            try:
                schema_properties = get_schema_properties(syn, schema_uri)
            except (SynapseHTTPError, ValueError):
                schema_properties = []

            if schema_properties:
                previous_upsert_key = landscape.get("upsert_key", "")

                if previous_upsert_key in schema_properties:
                    selected_index = schema_properties.index(previous_upsert_key)
                elif DEFAULT_UPSERT_KEY in schema_properties:
                    selected_index = schema_properties.index(DEFAULT_UPSERT_KEY)
                else:
                    selected_index = 0

                upsert_key = st.selectbox(
                    "Upsert Key",
                    options=schema_properties,
                    index=selected_index,
                    key=f"upsert_key_{task_id}",
                    help="Select the schema column that uniquely identifies rows for upsert.",
                )
            else:
                upsert_key = st.text_input(
                    "Upsert Key",
                    value=landscape.get("upsert_key", DEFAULT_UPSERT_KEY),
                    key=f"upsert_key_fallback_{task_id}",
                    help="Schema properties could not be loaded, so this field is editable text.",
                )

            record_set_description = st.text_area(
                "Record Set Description",
                value=landscape["record_set_description"],
                key=f"record_set_description_{task_id}",
                placeholder="Example: Landscape task for Jane Doe",
            )

            instructions = st.text_area(
                "Instructions",
                value=landscape["instructions"],
                key=f"instructions_{task_id}",
            )

        # Save widget values back to session state
        landscape["pi_name"] = pi_name
        landscape["project_id"] = project_id
        landscape["folder_id"] = folder_id
        landscape["record_set_name"] = record_set_name
        landscape["curation_task_name"] = curation_task_name
        landscape["schema_uri"] = schema_uri
        landscape["upsert_key"] = upsert_key
        landscape["record_set_description"] = record_set_description
        landscape["instructions"] = instructions

        st.divider()

        render_schema_properties(
            schema_uri=landscape["schema_uri"],
            upsert_key=landscape["upsert_key"],
        )

        if len(st.session_state.landscapes) > 1:
            st.button(
                "Remove this task",
                key=f"remove_{task_id}",
                on_click=remove_landscape,
                args=(task_id,),
            )


st.button("➕ Add another Landscape task", on_click=add_landscape)

st.divider()


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------
SYNAPSE_ID_PATTERN = r"syn\d+"


def validate_landscape_task(task: dict, index: int) -> list[str]:
    errors = []

    task_label = f"Task {index + 1}"

    if not task["pi_name"].strip():
        errors.append(f"{task_label}: PI Name is required.")

    project_id = task["project_id"].strip()
    if not project_id:
        errors.append(f"{task_label}: Synapse Project ID is required.")
    elif not re.fullmatch(SYNAPSE_ID_PATTERN, project_id):
        errors.append(f"{task_label}: Project ID should look like syn12345678.")

    folder_id = task["folder_id"].strip()
    if not folder_id:
        errors.append(f"{task_label}: Synapse Folder ID is required.")
    elif not re.fullmatch(SYNAPSE_ID_PATTERN, folder_id):
        errors.append(f"{task_label}: Folder ID should look like syn12345678.")

    if not task["record_set_name"].strip():
        errors.append(f"{task_label}: Record Set Name is required.")

    if not task["curation_task_name"].strip():
        errors.append(f"{task_label}: Curation Task Name is required.")

    if not task["schema_uri"].strip():
        errors.append(f"{task_label}: Schema URI is required.")

    if not task["upsert_key"].strip():
        errors.append(f"{task_label}: Upsert Key is required.")

    return errors


def validate_unique_curation_task_names(tasks: list[dict]) -> list[str]:
    """
    Reject duplicate (project_id, curation_task_name) pairs within the batch,
    since the later task would otherwise update the earlier one.
    """
    errors = []
    seen: dict[tuple[str, str], int] = {}

    for i, task in enumerate(tasks):
        key = (task["project_id"].strip(), effective_curation_task_name(task))

        if not key[0] or not key[1]:
            continue

        if key in seen:
            errors.append(
                f"Task {i + 1}: Curation Task Name {key[1]!r} is already used by "
                f"Task {seen[key] + 1} in project {key[0]}. Names must be unique "
                "within a project."
            )
        else:
            seen[key] = i

    return errors


# -----------------------------------------------------------------------------
# Create tasks
# -----------------------------------------------------------------------------
if st.button("🚀 Create Landscape Tasks", type="primary"):
    all_errors = []

    for i, task in enumerate(st.session_state.landscapes):
        all_errors.extend(validate_landscape_task(task, i))

    all_errors.extend(validate_unique_curation_task_names(st.session_state.landscapes))

    if all_errors:
        st.error("Please fix the following issues before creating tasks:")
        for error in all_errors:
            st.write(f"- {error}")
        st.stop()

    results = []

    with st.spinner("Creating Landscape metadata tasks..."):
        for task in st.session_state.landscapes:
            pi_name = task["pi_name"].strip()
            project_id = task["project_id"].strip()
            folder_id = task["folder_id"].strip()
            schema_uri = task["schema_uri"].strip()
            upsert_key = task["upsert_key"].strip()

            try:
                record_set, curation_task, _data_grid = create_landscape_task(
                    project_id=project_id,
                    folder_id=folder_id,
                    pi_name=pi_name,
                    record_set_name=task["record_set_name"].strip(),
                    record_set_description=task["record_set_description"].strip()
                    or None,
                    curation_task_name=effective_curation_task_name(task),
                    instructions=task["instructions"].strip(),
                    schema_uri=schema_uri,
                    upsert_key=upsert_key,
                    synapse_client=syn,
                )

                results.append(
                    {
                        "PI Name": pi_name,
                        "Project ID": project_id,
                        "Folder ID": folder_id,
                        "Schema URI": schema_uri,
                        "Upsert Key": upsert_key,
                        "RecordSet ID": record_set.id,
                        "Curation Task ID": curation_task.task_id,
                        "Status": "Created",
                    }
                )

            except Exception as e:  # noqa: BLE001 - report any per-task failure and continue
                results.append(
                    {
                        "PI Name": pi_name,
                        "Project ID": project_id,
                        "Folder ID": folder_id,
                        "Schema URI": schema_uri,
                        "Upsert Key": upsert_key,
                        "RecordSet ID": "",
                        "Curation Task ID": "",
                        "Status": f"Failed: {e}",
                    }
                )

    succeeded = sum(1 for r in results if r["Status"] == "Created")
    failed = len(results) - succeeded

    if failed == 0:
        st.success("Finished creating Landscape tasks.")
    elif succeeded == 0:
        st.error(f"All {failed} Landscape task(s) failed.")
    else:
        st.warning(
            f"Finished with errors: {succeeded} task(s) created, {failed} failed."
        )

    st.subheader("Results")
    st.dataframe(
        results,
        width="stretch",
        hide_index=True,
    )

    st.subheader("Created Task Summary")

    for result in results:
        if result["Status"] == "Created":
            st.markdown(
                f"""
**PI:** {result["PI Name"]}  
**Project:** `{result["Project ID"]}`  
**Folder:** `{result["Folder ID"]}`  
**Schema URI:** `{result["Schema URI"]}`  
**Upsert Key:** `{result["Upsert Key"]}`  
**RecordSet:** `{result["RecordSet ID"]}`  
**Curation Task:** `{result["Curation Task ID"]}`
"""
            )
        else:
            st.error(
                f"{result['PI Name']} / {result['Folder ID']} failed: {result['Status']}"
            )
