# namhub-dcc

![CI](https://github.com/sagebio-ada/namhub-dcc/actions/workflows/ci.yml/badge.svg)
![Deploy docs](https://github.com/sagebio-ada/namhub-dcc/actions/workflows/deploy-docs.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

Scripts to automate steps in NAMHub data coordination, packaged as an
installable command-line tool (`namhub-dcc`) so anyone with a Synapse
account can run them.

**[Full documentation →](https://sagebio-ada.github.io/namhub-dcc/)**

---

## Quickstart

```bash
pip install git+https://github.com/sagebio-ada/namhub-dcc.git
export SYNAPSE_AUTH_TOKEN=<your-personal-access-token>
namhub-dcc --help
```

Requires Python 3.10+ and a [Synapse](https://www.synapse.org) account. You
can generate a personal access token from your Synapse account settings page
("Personal Access Tokens").

> Credentials are resolved in order: an explicit `--auth-token` argument, the
> `SYNAPSE_AUTH_TOKEN` environment variable, then cached credentials in
> `~/.synapseConfig`.

---

## Commands

| Command | Description |
| --- | --- |
| [`create-folders-from-recordset`](https://sagebio-ada.github.io/namhub-dcc/commands/create-folders-from-recordset.html) | Create one Synapse Folder per row of a Synapse RecordSet. |
| [`create-landscape-task`](https://sagebio-ada.github.io/namhub-dcc/commands/create-landscape-task.html) | Create a NAMHub Landscape Task (RecordSet + CurationTask + Grid) in a project folder. |
| [`update-recordset-schema`](https://sagebio-ada.github.io/namhub-dcc/commands/update-recordset-schema.html) | Move existing RecordSets to a newer JSON schema version (adds missing columns, rebinds the schema). |

Run `namhub-dcc --help` to list all commands, or `namhub-dcc <command> --help`
for a command's options. See the docs linked above for full option tables,
behavior notes, and the Python API.

### `create-folders-from-recordset`

```bash
namhub-dcc create-folders-from-recordset \
    --record-set-id syn76265683 \
    --name-column DatasetName \
    --parent-id syn87654321
```

Add `--dry-run` to preview without creating anything.

### `create-landscape-task`

```bash
namhub-dcc create-landscape-task \
    --project-id syn12345678 \
    --folder-id syn74568176 \
    --pi-name "Jane Doe"
```

Also available as a plain Python function,
`namhub_dcc.landscape.create_landscape_task`, for scripting (e.g. creating
tasks across many folders in a loop).

### `update-recordset-schema`

```bash
namhub-dcc update-recordset-schema --record-set-id syn12345678 --dry-run
namhub-dcc update-recordset-schema --record-set-id syn12345678
```

Moves RecordSets to the latest version of their bound schema (or `--schema-uri`),
adding any missing columns as empty columns.

---

## GUI

A Streamlit app for creating several Landscape tasks at once from a form is
included in `apps/landscape_task_gui.py`. It wraps the same
`namhub_dcc.landscape.create_landscape_task` helper as the CLI command. Its
"Update RecordSet schema" tab previews and applies
`namhub_dcc.recordset_schema.update_recordset_schema` to one or more existing
RecordSets.

```bash
pip install -e ".[gui]"
streamlit run apps/landscape_task_gui.py
```

Credentials are resolved the same way as the CLI: a personal access token
entered in the app's sidebar, then the `SYNAPSE_AUTH_TOKEN` environment
variable, then `~/.synapseConfig`. The app is intended for local use only; do
not deploy it as a shared service, since it acts with the credentials of
whoever is logged in.

---

## Development

Clone the repo and install it in editable mode:

```bash
git clone https://github.com/sagebio-ada/namhub-dcc.git
cd namhub-dcc
pip install -e .
```

New scripts should be added as a new module under `src/namhub_dcc/commands/`
and registered in `src/namhub_dcc/commands/__init__.py`. Add a matching page
under `docs/commands/` and link it from `docs/index.md`.
