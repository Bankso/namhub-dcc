# `modify-org-access`

[← Back to docs index](../index.md)

Adds access for a user or team (a principal) to a Synapse JSON schema
organization. The command keeps the access types that the principal has
already. It adds only the access types that the principal does not have.

This command is the CLI wrapper for the Python function
`namhub_dcc.modify_org_access.modify_org_access`. To call the function
directly, see [Python API](#python-api).

## Usage

```bash
namhub-dcc modify-org-access \
    --org-name my.org.name \
    --principal-id 1234567 \
    --access-type CREATE READ UPDATE
```

## Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--org-name` | Yes | — | Name of the JSON schema organization. |
| `--principal-id` | Yes | — | Numeric principal ID of the user or team to give access to. |
| `--access-type` | Yes | — | One or more access types to add, separated by spaces. For the permitted values, see [ACCESS_TYPE](https://rest-docs.synapse.org/rest/org/sagebionetworks/repo/model/ACCESS_TYPE.html). |
| `--auth-token` | No | — | Synapse personal access token. If you do not give a token, the command uses `SYNAPSE_AUTH_TOKEN` or `~/.synapseConfig`. |

## Behavior notes

- The command adds access. It does not remove access. If the principal has
  `READ` and you give `--access-type CREATE`, the principal then has `READ`
  and `CREATE`.
- If the principal is not in the access control list (ACL) of the
  organization, the command adds a new entry for the principal.
- You must have `CHANGE_PERMISSIONS` access on the organization to use this
  command.
- The command writes the access types that the principal has after the
  update to the log.

## Python API

```python
import synapseclient
from namhub_dcc.modify_org_access import modify_org_access

syn = synapseclient.Synapse()
syn.login()

access = modify_org_access(
    org_name="my.org.name",
    principal_id=1234567,
    access_type=["CREATE", "READ", "UPDATE"],
    synapse_client=syn,
)
```

| Parameter | Default | Description |
| --- | --- | --- |
| `org_name` | — | Name of the JSON schema organization. |
| `principal_id` | — | Numeric principal ID of the user or team, as an integer or a string. |
| `access_type` | — | List of access types to add. |
| `synapse_client` | `None` | A `Synapse` client that is logged in. If you do not give a client, the function uses the last `Synapse` instance that you created. |

Returns a list of all the access types that the principal has after the
update.

## Source

[`src/namhub_dcc/modify_org_access.py`](../../src/namhub_dcc/modify_org_access.py) ·
[`src/namhub_dcc/commands/modify_org_access.py`](../../src/namhub_dcc/commands/modify_org_access.py)
