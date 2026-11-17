"""Modify access to a Synapse JSON schema organization.

Wraps ``SchemaOrganization.update_acl`` from ``synapseclient`` so that the
access of one principal (user or team) to an organization can be added with
a single function call.
"""

from synapseclient import Synapse
from synapseclient.models import SchemaOrganization


def modify_org_access(
    org_name: str,
    principal_id: int | str,
    access_type: list[str],
    *,
    synapse_client: Synapse | None = None,
) -> list[str]:
    """Add access for a principal to a JSON schema organization.

    If the principal already has an entry in the organization ACL, this
    adds the given access types to the access types it already has. If not,
    this adds a new entry.

    Arguments:
        org_name: Name of the JSON schema organization (for example,
            "my.org.name").
        principal_id: Numeric principal ID of the user or team to give access
            to.
        access_type: List of access types to give to the principal (for
            example, ["READ", "CREATE"]). See
            https://rest-docs.synapse.org/rest/org/sagebionetworks/repo/model/ACCESS_TYPE.html
        synapse_client: If not passed in and caching was not disabled by
            `Synapse.allow_client_caching(False)`, this will use the last
            created instance from the Synapse class constructor.

    Returns:
        All the access types the principal has after the update.
    """
    principal_id = int(principal_id)
    org = SchemaOrganization(org_name)
    acl = org.get_acl(synapse_client=synapse_client)

    current: list[str] = []
    for entry in acl["resourceAccess"]:
        if entry["principalId"] == principal_id:
            current = list(entry["accessType"])
    combined = list(current)
    for a in access_type:
        if a not in combined:
            combined.append(a)

    org.update_acl(
        principal_id=principal_id,
        access_type=combined,
        synapse_client=synapse_client,
    )
    return combined
