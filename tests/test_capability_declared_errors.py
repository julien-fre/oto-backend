"""Un refus DÉCLARÉ (`Capability.errors`) est un refus LEVÉ, et un refus PUBLIÉ.

`DeclaredError` décrit, ne fait rien (cf. `_types.py`) : rien n'empêche de déclarer un
409 que le handler ne lève jamais, et le document promettrait alors ce que le serveur
ne rend pas — pire qu'un document muet, parce qu'un client généré s'y branche. Deux
gardes, dans l'ordre de ce qui coûterait le plus cher :

1. **chaque code déclaré est ATTEIGNABLE par la capacité qui le déclare** — le graphe
   d'appel depuis son handler, pas la présence du code quelque part dans le fichier.
   ⚠️ La question était « existe-t-il dans le module ? » jusqu'au 2026-09-01 (#792), et
   deux capacités voisines partagent leurs homonymes : l'une pouvait promettre ce que
   seule l'autre rend. Le même axe manqué interdisait l'inverse — déclarer un refus
   réellement servi mais levé dans un autre module ;
2. **chaque déclaration atteint `/openapi.json`** : une réponse par statut, l'énuméré
   `error` qui porte le code, l'enveloppe `Erreur` en composant.

Le rejeu sur la route SERVIE (un vrai 409 sur un vrai PATCH) vit dans
`tests/api/test_rest_contract_front_tiers.py`, contre PostgreSQL.
"""
from __future__ import annotations

import pytest

from _refus_atteignables import atteignables

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
def test_un_refus_declare_est_ATTEIGNABLE_par_cette_capacite(paire):
    """L'axe corrigé le 2026-09-01 (#792) : ce test demandait qu'un code EXISTE dans
    le module du handler. Deux capacités du même fichier partagent leurs homonymes —
    donc l'une pouvait déclarer un refus que seule l'autre rend, et rester verte.
    C'est arrivé le jour même, sur la pose d'une clé : trois refus de palier déclarés
    sur une capacité qui n'a pas de palier, rattrapés par lecture et non par le banc.

    ⚠️ **Et l'axe manqué avait DEUX faces.** L'ancienne question refusait aussi
    l'inverse : un refus réellement servi mais levé dans un AUTRE module — les refus
    de saisie qu'un coffre remonte — restait indéclarable. Corriger un seul sens
    aurait laissé l'autre en donnant l'illusion d'avoir traité le sujet.

    La question est maintenant : *ce chemin-ci peut-il lever ce code ?* — cf.
    `tests/_refus_atteignables.py` pour ce que le parcours voit et ce qu'il ne voit
    pas.
    """
    cap, e = paire
    assert isinstance(e, DeclaredError)
    assert atteignables(cap.handler).accepte(e.status, e.code), (
        f"{cap.key} déclare {e.status} `{e.code}` mais son chemin ne peut pas le "
        "lever : déclaration décorative — le document promettrait un refus que le "
        "serveur ne rend jamais, et un client généré s'y brancherait.")


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


# ── Le cliquet du cliquet (#792) ──────────────────────────────────────────────
#
# ⚠️ Ce fichier a changé d'AXE le 2026-09-01, et le piège d'un tel lot est de se
# servir du garde-fou pour valider sa propre correction. Les deux tests ci-dessous
# exercent le parcours sur un banc FACTICE (`tests/_faux_refus.py`), dont on connaît
# les réponses d'avance : ils
# rougiraient si l'assouplissement devenait une passoire, ou si le durcissement
# redevenait une porte fermée. Éprouvés en cassant chacun de son côté avant d'être
# posés.

def test_le_parcours_refuse_un_refus_qui_vit_a_COTE_du_chemin():
    """Le sens qui protège le contrat : une capacité voisine lève ce code, pas
    celle-ci. L'ancienne question — « existe-t-il dans le module ? » — répondait oui."""
    import _faux_refus

    vu = atteignables(_faux_refus.handler)
    assert vu.accepte(404, "introuvable"), "le refus direct du chemin doit passer"
    assert not vu.accepte(403, "jamais_par_ce_chemin"), (
        "un code levé par une fonction que le handler n'appelle PAS doit être refusé")


def test_le_parcours_accepte_un_refus_RELAYE_depuis_ailleurs():
    """L'autre sens : le code voyage dans une exception métier et ressort par un
    relais. Le refuser rendait indéclarable un refus réellement servi."""
    import _faux_refus

    vu = atteignables(_faux_refus.handler)
    assert vu.accepte(400, "valeur_refusee"), (
        "un code porté par une exception du chemin et relayé doit être déclarable")
    assert not vu.accepte(400, "code_qui_nexiste_nulle_part"), (
        "le relais n'ouvre pas la porte à n'importe quel code : seulement à ceux que "
        "les exceptions du chemin savent porter")
