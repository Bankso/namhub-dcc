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
| [`modify-org-access`](https://sagebio-ada.github.io/namhub-dcc/commands/modify-org-access.html) | Add access for a user or team to a JSON schema organization. |

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

### `modify-org-access`

```bash
namhub-dcc modify-org-access \
    --org-name my.org.name \
    --principal-id 1234567 \
    --access-type CREATE READ UPDATE
```

The command adds access. It keeps the access types that the principal has
already. The command is also available as a Python function,
`namhub_dcc.modify_org_access.modify_org_access`.

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
