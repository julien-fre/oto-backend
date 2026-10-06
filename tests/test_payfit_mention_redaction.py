"""PayFit — la mention de rédaction dit ce que la politique EFFECTIVE masque.

Signal oto #1269 : `payfit_collaborator op=get` a servi le NIR et l'IBAN en clair
alors que la réponse affirmait « NIR, IBAN/BIC masqués ». La mention était une
constante, posée quelle que soit la politique de l'org : quand un org_admin lève la
règle, la donnée sort en clair (c'est son droit) mais l'agent croit, et dit, qu'elle
est protégée.

Ce fichier verrouille deux choses, par le chemin réel de la sortie
(`redaction.redact_payload`, celui du middleware et d'`oto_call`) :
- la mention suit la politique effective : défaut serveur, levée totale, levée
  partielle ;
- la réponse `op=get`, sous la forme exacte que l'API PayFit documente pour un
  collaborateur (NIR, NTT, IBAN, BIC à la racine), sort masquée sous le défaut.

Toutes les valeurs sont factices.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from oto_mcp import redaction

K = "000000000000000000000a0a"
NIR = "SENTINELLE-NIR-0000000000000"
NTT = "SENTINELLE-NTT-000000000"
IBAN = "SENTINELLE-IBAN-FR0000000000"
BIC = "SENTINELLE-BIC-0000"


def _politique(monkeypatch, filtres: dict) -> None:
    monkeypatch.setattr("oto_mcp.access.current_user_sub_from_token",
                        lambda: "sub-test")
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.access.rbac.org_store.get_org_field_filters",
                        lambda org_id: filtres)


@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("pf-test", False))
    return inst


def _collaborateur() -> dict:
    """La forme de `GET /companies/{id}/collaborators/{id}` selon la référence de
    l'API Partner PayFit : les champs sensibles sont à la RACINE."""
    return {
        "id": K, "matricule": "0001", "firstName": "Ada", "lastName": "Factice",
        "birthDate": "1990-01-01", "nationality": "FR", "gender": "FEMALE",
        "socialSecurityNumber": NIR, "temporaryTechnicalNumber": NTT,
        "iban": IBAN, "bic": BIC,
        "emails": [{"email": "ada@exemple.test", "type": "professional"}],
        "contracts": [{"id": "k1", "status": "active"}],
    }


def _get() -> dict:
    from fastmcp import FastMCP

    from oto_mcp.tools import payfit as P

    m = FastMCP("t")
    P.register(m)
    return asyncio.run(m.get_tool("payfit_collaborator")).fn(
        op="get", collaborator_id=K)


def test_under_the_server_default_the_get_answer_leaks_nothing(monkeypatch, client):
    _politique(monkeypatch, {})
    client.get_collaborator.return_value = _collaborateur()
    rendu = json.dumps(redaction.redact_payload("payfit", _get()), ensure_ascii=False)
    for sentinelle in (NIR, NTT, IBAN, BIC):
        assert sentinelle not in rendu


def test_under_the_server_default_the_notice_says_masked(monkeypatch, client):
    _politique(monkeypatch, {})
    client.get_collaborator.return_value = _collaborateur()
    mention = _get()["redaction"]
    assert "EN CLAIR" not in mention
    for champ in ("socialSecurityNumber", "temporaryTechnicalNumber", "iban", "bic"):
        assert champ in mention


def test_a_lifted_policy_is_said_and_never_called_masked(monkeypatch, client):
    """L'org a nommé tout le plancher dans `unmask` : tout sort en clair, et la mention
    le DIT."""
    _politique(monkeypatch, {"payfit": {"rules": [], "unmask": ["socialSecurityNumber", "numeroSecuriteSociale", "temporaryTechnicalNumber", "numeroTechniqueTemporaire", "iban", "bic", "absence_type"]}})
    client.get_collaborator.return_value = _collaborateur()
    out = _get()
    assert redaction.redact_payload("payfit", out) is redaction.PASSTHROUGH
    assert NIR in json.dumps(out)
    assert "EN CLAIR" in out["redaction"]
    assert "socialSecurityNumber" in out["redaction"]
    assert "Aucun champ sensible n'est masqué" in out["redaction"]


def test_a_partial_policy_names_what_stays_clear(monkeypatch, client):
    """L'org ne lève que le NIR : il sort, et la mention le nomme ; l'IBAN et le BIC
    restent masqués par le plancher."""
    _politique(monkeypatch, {"payfit": {"rules": [], "unmask": ["socialSecurityNumber"]}})
    client.get_collaborator.return_value = _collaborateur()
    mention = _get()["redaction"]
    clair, _, masque = mention.partition("Restent masqués")
    assert "socialSecurityNumber" in clair and "iban" not in clair
    assert "iban" in masque and "bic" in masque
