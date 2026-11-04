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
| `--pi-name` | Yes | — | Name of the PI this Landscape Task is for. Used to build the default RecordSet description. |
| `--record-set-name` | No | `Curation` | Name for the RecordSet. |
| `--record-set-description` | No | `Landscape task for <pi-name>` | Description for the RecordSet. |
| `--curation-task-name` | No | `Landscape Task` | Name for the CurationTask. Must be unique within the project — see [Behavior notes](#behavior-notes). |
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

- `--curation-task-name` acts as a unique key **per project**. Re-running the
  command in the same project with the *same* curation task name updates the
  existing task (and its RecordSet, matched by name + parent folder) rather
  than creating a duplicate. Using a *different* name creates a brand-new,
  separate CurationTask even if it targets the same folder.
- The RecordSet name/parent pair is also how an existing RecordSet is
  detected and updated in place, independent of the curation task name.

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
| `record_set_name` | `"Curation"` | Name for the RecordSet. |
| `record_set_description` | `None` | Defaults to `"Landscape task for <pi_name>"`. |
| `curation_task_name` | `"Landscape Task"` | Name for the CurationTask. |
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
