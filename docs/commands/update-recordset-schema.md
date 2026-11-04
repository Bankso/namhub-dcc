# `update-recordset-schema`

[← Back to docs index](../index.md)

Moves existing Synapse RecordSets to a newer version of their JSON schema. A
RecordSet's bound schema drives the validation of its linked CurationTask, so
rebinding the RecordSet updates that validation too.

This is the CLI wrapper around the
`namhub_dcc.recordset_schema.update_recordset_schema` Python function — see
[Python API](#python-api) below to call it directly. A Streamlit form for the
same operation is the "Update RecordSet schema" tab of
[`apps/landscape_task_gui.py`](../../apps/landscape_task_gui.py).

## Usage

```bash
# Preview what would change
namhub-dcc update-recordset-schema --record-set-id syn12345678 --dry-run

# Move to the latest version of the currently bound schema
namhub-dcc update-recordset-schema --record-set-id syn12345678 syn23456789

# Move to a specific schema (an unversioned URI means its latest version)
namhub-dcc update-recordset-schema --record-set-id syn12345678 \
    --schema-uri NAMhub-Landscape-3.0.0
```

## Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--record-set-id` | Yes | — | One or more Synapse IDs of the RecordSets to update. |
| `--schema-uri` | No | latest version of each RecordSet's currently bound schema | JSON schema URI to move to. A URI without a version uses the latest registered version. |
| `--dry-run` | No | off | Show what would change without changing anything. |
| `--auth-token` | No | — | Synapse personal access token. Falls back to `SYNAPSE_AUTH_TOKEN` or `~/.synapseConfig`. |

## What happens to each RecordSet

1. The target schema version is chosen (see `--schema-uri`). If nothing is
   bound and no `--schema-uri` is given, the RecordSet is reported as an error.
2. The RecordSet's upsert keys must all be properties of the target schema;
   otherwise it is reported as an error and left unchanged.
3. Target-schema properties missing from the RecordSet's CSV header are
   appended as **empty columns** and stored as a new file version of the
   RecordSet. The file is not re-serialized: existing columns, their order
   and all row data are kept byte-for-byte, and only the new header names and
   one empty field per new column are added to each row (following the
   RecordSet's CSV separator, quote and escape settings). Name, parent,
   description and upsert keys are preserved.
4. The RecordSet is bound to the target schema (replacing the previous
   binding; the "derived annotations" setting is carried over).

If the RecordSet is already bound to the target version and has every column,
nothing is done.

## Behavior notes

- Columns in the CSV that are not in the new schema are **kept** and listed in
  the output, never dropped or renamed (e.g. `ContextofUse` stays next to the
  new `ContextOfUse`). Clean these up in Synapse if needed.
- Each RecordSet is processed independently. A failure (bad ID, not a
  RecordSet, upsert key not in the schema, a Synapse error such as no edit
  permission, ...) is logged, the remaining IDs
  continue, and the command exits with status 1 at the end.
- A dry run (and the GUI's Preview) does **not** check edit permission, so an
  update can still fail with a permission error when applied.
- If a run fails after the new file version is stored but before the schema is
  re-bound, simply re-run it: the columns are already present so none are added
  twice, and the binding is then applied.
- The CSV must be rectangular: every non-empty record needs as many fields as
  the header (a quote only opens a quoted field at the start of a field). If
  not, the RecordSet is reported as an error (also in a dry run, when columns
  would be added) and nothing is changed.
- **Open Grid sessions:** a Grid session keeps the schema and columns it was
  started with. The command warns when *you* have an open Grid session on the
  RecordSet (Synapse only lists your own sessions, so sessions opened by other
  users cannot be detected); close and restart the Grid to pick up the update.

## Python API

```python
import synapseclient
from namhub_dcc.recordset_schema import update_recordset_schema

syn = synapseclient.Synapse()
syn.login()

plan = update_recordset_schema("syn12345678", dry_run=True, synapse_client=syn)
print(plan.new_schema_uri, plan.added_columns)

result = update_recordset_schema("syn12345678", synapse_client=syn)
```

| Parameter | Default | Description |
| --- | --- | --- |
| `record_set_id` | — | Synapse ID of the RecordSet. |
| `schema_uri` | `None` | Target schema URI; defaults to the latest version of the currently bound schema. |
| `dry_run` | `False` | Keyword-only. Compute the plan without changing anything. |
| `synapse_client` | `None` | An already-logged-in `Synapse` client; uses the last-created instance if omitted. |

Returns a `RecordSetSchemaUpdate` with `record_set_id`, `previous_schema_uri`,
`new_schema_uri`, `added_columns`, `extra_columns`, `warnings`,
`new_version_created`, `no_op` and `dry_run`. Raises `ValueError` for the
error cases above (nothing is changed); Synapse HTTP errors (e.g. no edit
permission) can also be raised, and the CLI reports both and continues.

## Source

[`src/namhub_dcc/recordset_schema.py`](../../src/namhub_dcc/recordset_schema.py) ·
[`src/namhub_dcc/commands/update_recordset_schema.py`](../../src/namhub_dcc/commands/update_recordset_schema.py)
