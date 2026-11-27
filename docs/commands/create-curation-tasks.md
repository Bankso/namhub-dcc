# `create-curation-tasks`

[← Back to docs index](../index.md)

Creates folders and Curator curation tasks in a Synapse project. A CSV file
gives one task per row. Each schema must be registered in Synapse before you
run the command.

## Usage

```bash
namhub-dcc create-curation-tasks \
    --project-id syn78815407 \
    --csv tasks.csv
```

Run with `--dry-run` first. It checks the CSV and the schemas, and shows
what the command will create. It creates nothing.

## Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--project-id` | Yes | — | Synapse ID of the project that holds the curation tasks. |
| `--csv` | Yes | — | Path to the input CSV. See [CSV format](#csv-format). |
| `--parent-id` | No | the project | Synapse ID of the container for the folders. It must be in `--project-id`. |
| `--dry-run` | No | off | Check the input and show the plan. Create nothing. |
| `--auth-token` | No | — | Synapse personal access token. Falls back to `SYNAPSE_AUTH_TOKEN` or `~/.synapseConfig`. |

## CSV format

| Column | Required | Description |
| --- | --- | --- |
| `folder_name` | Yes | Name of the folder for the task. The command uses an existing folder with this name under the parent, or creates it. |
| `schema_uri` | Yes | Registered JSON schema ID, in the form `<organization>-<name>-<version>`. |
| `record_or_file` | Yes | `record` for a RecordSet task, `file` for a file task. |
| `upsert_keys` | For `record` rows | Columns that identify one record. Separate names with `;`. Leave blank for `file` rows. |
| `task_name` | No | Start of the RecordSet, file view and CurationTask names. Default: `<organization>_<name>` from the schema ID. Set it when two rows use the same schema. |

Example:

```csv
folder_name,schema_uri,record_or_file,upsert_keys
biospecimen_records,NAMhub-Biospecimen-4.0.0,record,StudyId;BiospecimenId
diagnosis_records,NAMhub-Diagnosis-4.0.0,record,StudyId;IndividualId;Diagnosis
landscapeId-scRNAseq-level1,NAMhub-SingleCellRNASeqLevel1-4.0.0,file,
```

Two rows that use the same schema need a `task_name` on at least one of them:

```csv
folder_name,schema_uri,record_or_file,upsert_keys,task_name
landscapeA-scRNAseq-level1,NAMhub-SingleCellRNASeqLevel1-4.0.0,file,,landscapeA_scRNAseqLevel1
landscapeB-scRNAseq-level1,NAMhub-SingleCellRNASeqLevel1-4.0.0,file,,landscapeB_scRNAseqLevel1
```

## What gets created

In the names below, `<prefix>` is the row's `task_name`, or
`<organization>_<name>` from the schema ID (for example `NAMhub_Biospecimen`).
For each row, the command uses or creates the folder, then creates:

- **`record` rows:** a RecordSet `<prefix>_RecordSet` in the
  folder. Its columns come from the schema, and the schema is bound to it. The
  command also creates a CurationTask `<prefix>_CurationTask` with
  the upsert keys, and a Grid for the RecordSet.
- **`file` rows:** the schema is bound to the folder. The command creates a
  file view `<prefix>_FileView` of the folder, a CurationTask
  `<prefix>_CurationTask`, and a wiki page on the folder that
  shows the view.

The task instructions are the schema description, followed by the schema ID.

## Behavior notes

- The command checks the full CSV and every schema before it creates
  anything. If a check fails, it stops and creates nothing. The CSV must not
  repeat a folder name or a task name.
- If a CurationTask with the same name is already in the project, the command
  skips that row. It does not change the existing task.
- A file view shows no rows until the folder contains files.
- Use a `--parent-id` that is in `--project-id`. With synapseclient 4.13 or
  later, a task goes to the project of its folder. If the folder is in a
  different project, the command does not find the task when you run it
  again, and it creates a second one.
