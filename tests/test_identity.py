import pytest

from frontend.identity import authentication_required, owner_id


@pytest.mark.parametrize("cluster,flag,required", [
    (None, None, False), (None, "false", False), (None, "true", True),
    (None, "TRUE", True), ("prod-gcp", None, True), ("prod-gcp", "false", True),
])
def test_deployment_requires_entra_even_when_local_flag_disables_it(monkeypatch, cluster, flag, required):
    for name, value in (("NAIS_CLUSTER_NAME", cluster), ("AUTH_REQUIRED", flag)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert authentication_required() is required


@pytest.mark.parametrize("user", [None, {}, [], "employee", {"oid": 42}, {"sub": ""}])
def test_invalid_authenticated_identity_never_receives_local_access(user):
    assert owner_id(user, auth_required=True, local_owner="local-cookie-owner") is None


def test_local_identifiers_are_preserved_only_for_missing_unauthenticated_identity():
    assert owner_id(None, auth_required=False) == "local"
    assert owner_id(None, auth_required=False, local_owner="local-cookie-owner") == "local-cookie-owner"
    assert owner_id({}, auth_required=False, local_owner="local-cookie-owner") is None


def test_oid_precedence_sub_fallback_and_tenant_isolation():
    user = {"tid": "tenant-one", "oid": "employee", "sub": "alternate-id"}
    owner = owner_id(user, auth_required=True)
    assert owner == owner_id({"tid": "tenant-one", "sub": "employee"}, auth_required=True)
    assert owner == owner_id(user, auth_required=False)
    assert owner != owner_id({**user, "tid": "tenant-two"}, auth_required=True)
    assert owner != owner_id({**user, "oid": "other-employee"}, auth_required=True)
