"""Un refus DÉCLARÉ (`Capability.errors`) est un refus LEVÉ, et un refus PUBLIÉ.

`DeclaredError` décrit, ne fait rien (cf. `_types.py`) : rien n'empêche de déclarer un
409 que le handler ne lève jamais, et le document promettrait alors ce que le serveur
ne rend pas — pire qu'un document muet, parce qu'un client généré s'y branche. Deux
gardes, dans l'ordre de ce qui coûterait le plus cher :

1. **chaque code déclaré est levé dans le module du handler**, avec ce statut — lu à
   la source (`AuthzDenied(<status>, "<code>"`), la forme sous laquelle tous les refus
   de capacité s'écrivent ;
2. **chaque déclaration atteint `/openapi.json`** : une réponse par statut, l'énuméré
   `error` qui porte le code, l'enveloppe `Erreur` en composant.

Le rejeu sur la route SERVIE (un vrai 409 sur un vrai PATCH) vit dans
`tests/api/test_rest_contract_front_tiers.py`, contre PostgreSQL.
"""
from __future__ import annotations

import inspect
import re

import pytest

from oto_mcp import openapi
from oto_mcp.capabilities import registry
from oto_mcp.capabilities._types import DeclaredError

_ADMIN = "/api/admin/"


def _declarations() -> list[tuple]:
    return [(cap, e) for cap in registry.CAPABILITIES for e in cap.errors]


def _ids(paire) -> str:
    cap, e = paire
    return f"{cap.key}:{e.status}:{e.code}"


def test_il_y_a_des_refus_declares():
    """Le garde-fou ne vaut que s'il garde quelque chose (ceux du front tiers, #618/#622)."""
    cles = {(cap.key, e.status, e.code) for cap, e in _declarations()}
    assert {("group.update", 409, "group_exists"),
            ("me.guides.set", 400, "body_too_large"),
            ("me.leave_org", 409, "personal_org"),
            ("me.leave_org", 409, "last_org_admin"),
            ("me.leave_org", 404, "not_a_member"),
            ("org.invite.create", 409, "already_member"),
            ("org.invite.create", 409, "already_invited")} <= cles


@pytest.mark.parametrize("paire", _declarations(), ids=_ids)
def test_un_refus_declare_est_leve_dans_le_module_du_handler(paire):
    cap, e = paire
    assert isinstance(e, DeclaredError)
    src = inspect.getsource(inspect.getmodule(cap.handler))
    motif = rf'AuthzDenied\(\s*{e.status},\s*"{re.escape(e.code)}"'
    assert re.search(motif, src), (
        f"{cap.key} déclare {e.status} `{e.code}` mais son module ne lève pas "
        f"`AuthzDenied({e.status}, \"{e.code}\"…)` : déclaration décorative — le "
        "document promettrait un refus que le serveur ne rend pas.")


@pytest.mark.parametrize("paire", _declarations(), ids=_ids)
def test_un_refus_declare_atteint_le_document(paire):
    cap, e = paire
    doc = openapi.build()
    for b in cap.rest_bindings():
        if b.path.startswith(_ADMIN):
            continue
        rep = doc["paths"][openapi._openapi_path(b.path)][b.verb.lower()]["responses"]
        assert str(e.status) in rep, f"{b.verb} {b.path} : pas de réponse {e.status}"
        assert f"`{e.code}`" in rep[str(e.status)]["description"]
        schema = rep[str(e.status)]["content"]["application/json"]["schema"]
        if "allOf" in schema:
            assert e.code in schema["allOf"][1]["properties"]["error"]["enum"]
            assert schema["allOf"][0] == {"$ref": "#/components/schemas/Erreur"}
        else:
            # Statut partagé avec un refus générique (403) : l'enveloppe seule, sans
            # énuméré — le `forbidden` de l'autz reste possible à côté.
            assert schema == {"$ref": "#/components/schemas/Erreur"}


def test_l_enveloppe_est_un_composant_toujours_present():
    """Même sans aucune déclaration, 401 et 403 la référencent : un `$ref` vers un
    composant absent fait échouer la génération ENTIÈRE d'un client."""
    doc = openapi.build()
    assert "Erreur" in doc["components"]["schemas"]
    for item in doc["paths"].values():
        for verbe, op in item.items():
            if op.get("tags") in (["_legacy"], ["_deprecated"]):
                continue
            for statut in ("401", "403"):
                assert op["responses"][statut]["content"]["application/json"]["schema"] \
                    == {"$ref": "#/components/schemas/Erreur"}, (verbe, op["operationId"])
