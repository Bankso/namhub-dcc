# namhub-dcc

Scripts to automate steps in NAMHub data coordination, packaged as an
installable command-line tool (`namhub-dcc`) so anyone with a Synapse
account can run them.

## Installation

Requires Python 3.10+ and a [Synapse](https://www.synapse.org) account.

```bash
pip install git+https://github.com/sagebio-ada/namhub-dcc.git
```

This installs the `namhub-dcc` command.

## Authentication

Every command needs Synapse credentials, resolved in this order:

1. An explicit `--auth-token` argument.
2. The `SYNAPSE_AUTH_TOKEN` environment variable.
3. Cached credentials in `~/.synapseConfig`.

You can generate a personal access token from your Synapse account settings
page ("Personal Access Tokens").

```bash
export SYNAPSE_AUTH_TOKEN=<your-personal-access-token>
```

## Commands

Run `namhub-dcc --help` to list all commands, or `namhub-dcc <command> --help`
for a command's options.

- [`create-folders-from-recordset`](commands/create-folders-from-recordset.md) —
  create one Synapse Folder per row of a Synapse RecordSet.
- [`create-landscape-task`](commands/create-landscape-task.md) — create a
  NAMHub Landscape Task (RecordSet + CurationTask + Grid) in a project folder.
- [`update-recordset-schema`](commands/update-recordset-schema.md) — move
  existing RecordSets to a newer JSON schema version.

## Development

Clone the repo and install it in editable mode:

```bash
git clone https://github.com/sagebio-ada/namhub-dcc.git
cd namhub-dcc
pip install -e .
```

New scripts should be added as a new module under `src/namhub_dcc/commands/`
and registered in `src/namhub_dcc/commands/__init__.py`. Add a matching page
under `docs/commands/` and link it from this index.
