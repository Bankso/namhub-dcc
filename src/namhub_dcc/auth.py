"""Shared Synapse login helper used by every command."""

import synapseclient


def login(auth_token: str | None = None) -> synapseclient.Synapse:
    """Log in to Synapse.

    Resolution order for credentials: an explicit ``auth_token``, then the
    ``SYNAPSE_AUTH_TOKEN`` environment variable, then cached credentials in
    ``~/.synapseConfig`` (all handled internally by ``synapseclient``).
    """
    syn = synapseclient.Synapse()
    syn.login(authToken=auth_token)
    return syn
