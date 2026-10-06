"""La sonde d'un connecteur multi-comptes nomme le compte à tester — ou refuse en le
disant, jamais en 500.

Vécu le 01/10/2026 : un membre ajoute un 2ᵉ, puis un 3ᵉ compte nommé d'un connecteur
à clé. La pose réussit ; la sonde qui la suit (`level="auto"`, sans `account`) tombait
sur l'ambiguïté de la cascade (« plusieurs comptes … sans défaut unique »), levée en
`McpError` que la face REST ne savait pas rendre : 500 nu, sans en-têtes CORS. Le
navigateur n'en lisait que « Failed to fetch », alors que la clé était au coffre — et
l'utilisateur recliquait.
"""
from __future__ import annotations

import pytest
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import access, credentials_store
from oto_mcp.access import cascade
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.capabilities.connectors import verify as cv


class _Ctx:
    sub, org_id = "sub-x", 2


class _RC:
    mode, entity_type, entity_id, account = "user", "member", "2:sub-x", "compte-a"

    def __init__(self):
        self.fields, self.config = {"api_key": "k"}, {}


def _inp(account=""):
    return cv.VerifyInput(provider="pennylane", level="auto", account=account)


def test_auto_transmet_le_compte_nomme_a_la_resolution(monkeypatch):
    vu = {}

    def _resolve(provider, want="auto", sub=None, **k):
        vu.update(k, provider=provider, want=want, sub=sub)
        return _RC()

    monkeypatch.setattr(access, "resolve_credential", _resolve)
    _, _, scope, instance, _ = cv._fields_config_scope(_Ctx(), _inp("compte-a"))
    assert vu["account"] == "compte-a" and vu["want"] == "auto"
    assert scope == ("member", "2:sub-x", "compte-a")
    assert instance["level"] == "member"


def test_auto_sans_compte_laisse_la_cascade_choisir(monkeypatch):
    vu = {}
    monkeypatch.setattr(access, "resolve_credential",
                        lambda *a, **k: vu.update(k) or _RC())
    cv._fields_config_scope(_Ctx(), _inp())
    assert vu["account"] is None


def test_lambiguite_devient_un_refus_nomme(monkeypatch):
    def _ambigu(*a, **k):
        raise cascade.CompteAmbigu(ErrorData(code=INVALID_PARAMS,
                                             message="Multiple accounts …"))

    monkeypatch.setattr(access, "resolve_credential", _ambigu)
    with pytest.raises(AuthzDenied) as exc:
        cv._fields_config_scope(_Ctx(), _inp())
    assert (exc.value.status, exc.value.code) == (400, "account_required")
    assert "Multiple accounts" in exc.value.message


def test_le_refus_est_declare_sur_la_capacite():
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == "connectors.verify")
    assert ("account_required", 400) in {(e.code, e.status) for e in cap.errors}


def test_lambiguite_de_la_cascade_est_typee(monkeypatch):
    """Reconnue par sa CLASSE (une face REST la rend en refus nommé), jamais par son
    texte — et toujours une `McpError` pour la face MCP, qui la rend telle quelle."""
    from oto_mcp.mcp_errors import McpError
    monkeypatch.setattr(credentials_store, "list_accounts", lambda *a, **k: [
        {"account": "compte-a", "meta": {}}, {"account": "compte-b", "meta": {}}])
    with pytest.raises(cascade.CompteAmbigu) as exc:
        cascade._shared_auto_account("member", "2:sub-x", "pennylane", "pour toi")
    assert isinstance(exc.value, McpError)
    assert "`compte-a`" in exc.value.error.message
