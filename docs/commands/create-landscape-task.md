# `create-landscape-task`

[← Back to docs index](../index.md)

Creates a NAMHub **Landscape Task** in a Synapse project folder: a RecordSet
bound to the latest version of the `NAMhub-Landscape` JSON schema, paired
with a CurationTask and a Grid view, via
`synapseclient.extensions.curator.create_record_based_metadata_task`.

This is the CLI wrapper around the `namhub_dcc.landscape.create_landscape_task`
Python function — see [Python API](#python-api) below to call it directly
(e.g. to create tasks across many folders in a loop).

## Usage

```bash
namhub-dcc create-landscape-task \
    --project-id syn12345678 \
    --folder-id syn74568176 \
    --pi-name "Jane Doe"
```

## Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--project-id` | Yes | — | Synapse ID of the project the folder lives in. |
| `--folder-id` | Yes | — | Synapse ID of the folder to create the Landscape Task RecordSet in. |
| `--pi-name` | Yes | — | Name of the PI this Landscape Task is for. Used to build the default RecordSet description and appended to default names. |
| `--record-set-name` | No | `Curation - <PI name>` | Name for the RecordSet; a name left at `Curation` gets ` - <PI name>` appended, any other name is used as given. Must not already exist in the folder — see [Behavior notes](#behavior-notes). |
| `--record-set-description` | No | `Landscape task for <pi-name>` | Description for the RecordSet. |
| `--curation-task-name` | No | `Landscape Task - <PI name>` | Name for the CurationTask; a name left at `Landscape Task` gets ` - <PI name>` appended, any other name is used as given. Must not already exist in the project — see [Behavior notes](#behavior-notes). |
| `--instructions` | No | `Please fill in each row with the dataset information.` | Instructions shown to the curator filling in the task. |
| `--schema-uri` | No | `NAMhub-Landscape` | JSON schema URI to bind the RecordSet to. A URI without a version uses the latest registered version; pass e.g. `NAMhub-Landscape-3.0.0` to pin one. |
| `--upsert-key` | No | `LandscapeId` | Column name that uniquely identifies rows for upsert. |
| `--auth-token` | No | — | Synapse personal access token. Falls back to `SYNAPSE_AUTH_TOKEN` or `~/.synapseConfig`. |

## What gets created

1. A **RecordSet** in `--folder-id`, pre-populated with an empty CSV template
   whose columns come from the `--schema-uri` schema's properties, bound to
   that schema.
2. A **CurationTask** in `--project-id` pointing at that RecordSet, with the
   given `--instructions`.
3. A **Grid** view exported back onto the RecordSet, so curators can start
   filling it in immediately.

## Behavior notes

- Every Landscape Task gets its **own** RecordSet and CurationTask. The
  underlying curator call would otherwise update an existing RecordSet with
  the same name in the folder, or an existing CurationTask with the same name
  in the folder's project, so the command checks for both first and exits
  with an error if either exists. Pick a different `--record-set-name` /
  `--curation-task-name` to create another task in the same folder or
  project.
- **Default names include the PI name.** A name left at its default gets
  ` - <PI name>` appended (`Curation - Jane Doe`, `Landscape Task - Jane Doe`)
  so tasks created with defaults don't collide. The rule lives in
  `namhub_dcc.landscape` (`effective_record_set_name`,
  `effective_curation_task_name`) and is shared by the CLI, the Python API and
  the GUI. An explicit non-default name is used unchanged.
- **Failed creation is cleaned up.** The curator stores the RecordSet, then the
  CurationTask, then the Grid. If a step fails, the RecordSet and CurationTask
  this call created are deleted (only if created by you after the call
  started; any Grid session on the RecordSet is deleted too) and the original
  error is re-raised, so the command can simply be re-run. If cleanup itself
  fails, the IDs left behind are logged at error level.
- **Schema version consistency.** For an unversioned `--schema-uri`, the
  command checks that Synapse's "latest" version has the same property names as
  the highest-numbered version it pins to (the RecordSet's columns come from the
  former, the binding from the latter), and errors before creating anything if
  not. Explicitly versioned URIs are not checked.
- Synapse errors (e.g. a missing folder or no permission) are reported as a
  clean error with exit status 1.

## Python API

```python
import synapseclient
from namhub_dcc.landscape import create_landscape_task

syn = synapseclient.Synapse()
syn.login()

record_set, curation_task, grid = create_landscape_task(
    folder_id="syn12345678",
    pi_name="Jane Doe",
    synapse_client=syn,
)
```

| Parameter | Default | Description |
| --- | --- | --- |
| `folder_id` | — | Synapse ID of the folder to create the Landscape Task RecordSet in. |
| `pi_name` | — | Name of the PI this Landscape Task is for. |
| `record_set_name` | `"Curation"` | Name for the RecordSet; left at the default, ` - <pi_name>` is appended (`effective_record_set_name`). |
| `record_set_description` | `None` | Defaults to `"Landscape task for <pi_name>"`. |
| `curation_task_name` | `"Landscape Task"` | Name for the CurationTask; left at the default, ` - <pi_name>` is appended (`effective_curation_task_name`). |
| `instructions` | `"Please fill in each row with the dataset information."` | Instructions for the curator. |
| `schema_uri` | `"NAMhub-Landscape"` | JSON schema URI the RecordSet is bound to. Unversioned URIs resolve to the latest version. |
| `upsert_key` | `"LandscapeId"` | Column name that uniquely identifies rows for upsert. |
| `project_id` | `None` | Keyword-only. Deprecated upstream; synapseclient infers the project from `folder_id`. |
| `synapse_client` | `None` | An already-logged-in `Synapse` client; uses the last-created instance if omitted. |

Returns a `(RecordSet, CurationTask, Grid)` tuple.

## Provenance

Ported from an internal Streamlit GUI (`CURATOR_landscape_gui.py`) that created
these one at a time through a form. A Streamlit app that creates several tasks
at once now lives in this repo as
[`apps/landscape_task_gui.py`](../../apps/landscape_task_gui.py) and wraps the
same `create_landscape_task` helper (see the Readme for how to run it).

That original GUI's code default for `schema_uri` was
`HTAN2Organization-scRNALevel1` (an unrelated HTAN scRNA-seq schema) — this was
a bug, inconsistent with the GUI's own docstring/placeholder text. It was
corrected here to the `NAMhub-Landscape` schema, whose properties actually
match real Landscape Task RecordSets in Synapse; the default now resolves to
its latest registered version.

## Source

[`src/namhub_dcc/landscape.py`](../../src/namhub_dcc/landscape.py) ·
[`src/namhub_dcc/commands/create_landscape_task.py`](../../src/namhub_dcc/commands/create_landscape_task.py)
