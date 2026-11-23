"""Shared employee-authentication policy and stable, tenant-scoped owner IDs."""

import hashlib
import os


def authentication_required() -> bool:
    """A deployed app cannot disable employee authentication with a local flag."""
    return bool(os.getenv("NAIS_CLUSTER_NAME")) or os.getenv("AUTH_REQUIRED", "").lower() == "true"


def owner_id(user: object, *, auth_required: bool, local_owner: str = "local") -> str | None:
    """Resolve validated claims; local fallback is allowed only without auth.

    Callers choose their existing local identifier to preserve job and cookie
    compatibility. Missing or malformed authenticated identities fail closed.
    """
    if user is None and not auth_required:
        return local_owner
    if not isinstance(user, dict):
        return None
    principal = user.get("oid") or user.get("sub")
    if not isinstance(principal, str) or not principal:
        return None
    return hashlib.sha256(f"{user.get('tid', '')}:{principal}".encode()).hexdigest()
