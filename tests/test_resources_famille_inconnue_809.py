"""Une famille de ressource inconnue est REFUSÉE, pas plantée (retour d'outil #809).

**Ce qui a été mesuré en production**, le 2026-09-08 sur `mcp.oto.cx` (v1.240.0) :

    POST /api/resources {"op":"get","resource_type":"procedure","resource_id":"1246"}
    → HTTP 500 « Internal Server Error »
    POST /api/resources {"op":"list","resource_type":"procedure"}
    → HTTP 400 {"error":"unsupported_resource_type",
                "detail":"type `procedure` non supporté
                          (['datastore','project','doctrine'])."}

Deux réponses pour une même saisie fautive, et c'est la mauvaise qui sort sur les op
qui comptent. Le mécanisme : `RESOURCE_GOVERN` s'exécute AVANT le handler, appelle
`ownership.can_govern` → `ownership._kind`, qui lève un `ValueError` nu que
l'adaptateur REST ne rattrape pas. Le `_check_type` du handler — celui qui rend le
400 nommé — n'est jamais atteint dès qu'un `resource_id` est présent.

**Pourquoi ça mord ici précisément.** Le retour #809 conclut que les procédures n'ont
aucun chemin vers une équipe. Elles en ont un (`op=transfer` + `new_owner_group`,
`_guide_reparent` déplace org ↔ équipe), mais il se nomme `doctrine` — vocabulaire
retiré du produit par #519. L'agent qui écrit `procedure` ou `guide`, les deux mots
que le produit lui apprend, reçoit « erreur interne du serveur » : il en déduit une
panne ou une absence, jamais « essaie l'autre nom ». Le refus nommé, lui, ÉNUMÈRE les
familles acceptées — il suffisait qu'il sorte.

⚠️ Ce banc regarde ce que la règle SERT, pas ce que la capacité DÉCLARE.
`test_resources_deux_surfaces.py::test_seule_l_heritee_declare_le_refus_du_type_inconnu`
vérifie que le code `unsupported_resource_type` figure dans `Capability.errors` et
affirme en prose que l'héritée « refuse dans le handler » : il était vert pendant que
la production rendait 500. Un refus déclaré n'est pas un refus servi.

Le correctif ne durcit RIEN : seuls des appels qui échouent déjà changent de réponse
(500 → 400 nommé). Le schéma d'entrée gelé par le cliquet de #774 n'est pas touché.
"""
from __future__ import annotations

import pytest

from oto_mcp import access, ownership, roles
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities import resources as R
from oto_mcp.capabilities._types import AuthzDenied, RawCtx
from oto_mcp.capabilities.resources_contract import KIND_OF

RAW = RawCtx(sub="u1")
REGLE = _authz.RESOURCE_GOVERN()

# Ce qu'un agent écrit spontanément et qui n'est PAS une famille de gouvernance.
# `procedure` en a été retiré le 23/09/2026 : c'est désormais le nom de la famille
# (otomata-tech/oto#65) — le refus mesuré en #809 est devenu la réponse attendue.
INCONNUES = ["guide", "tableau", "banana"]
OPS_AVEC_ID = ["get", "transfer", "share", "unshare"]


@pytest.fixture(autouse=True)
def identite(monkeypatch):
    monkeypatch.setattr(access, "current_org", lambda sub: 2)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)


@pytest.mark.parametrize("op", OPS_AVEC_ID)
@pytest.mark.parametrize("famille", INCONNUES)
def test_famille_inconnue_refusee_en_400_nomme(op, famille):
    inp = R.ResourceInput(op=op, resource_type=famille, resource_id="1246")
    with pytest.raises(AuthzDenied) as ei:
        REGLE(RAW, inp)
    err = ei.value
    assert err.status == 400, (
        f"op={op} famille={famille!r} : refus servi en {err.status}. Un `ValueError` "
        "sorti de `ownership._kind` devient un 500 — l'appelant lit « erreur interne » "
        "là où le contrat déclare `unsupported_resource_type`.")
    assert err.code == "unsupported_resource_type"
    # Le refus doit ÉNUMÉRER ce qui est accepté : c'est la seule chose qui répare
    # l'appelant, et c'est ce qui manquait au signal #809.
    for connue in ("datastore_namespace", "project", "procedure", "doc"):
        assert connue in err.message, f"le refus ne nomme pas `{connue}`"
    # Et il n'enseigne jamais la valeur STOCKÉE d'une procédure (oto#65).
    assert KIND_OF["procedure"] not in err.message


def test_une_famille_vide_reste_un_refus_de_saisie():
    """La borne : `resource_type=""` n'est pas une famille inconnue, c'est un champ
    absent — `missing_resource` continue de sortir en premier. Le garde-fou ajouté
    ne recouvre pas ce refus-là, qui dit mieux ce qui manque."""
    with pytest.raises(AuthzDenied) as ei:
        REGLE(RAW, R.ResourceInput(op="get", resource_type="", resource_id="1246"))
    assert ei.value.status == 400 and ei.value.code == "missing_resource"


@pytest.mark.parametrize("famille", ["datastore_namespace", "project", "procedure"])
def test_une_famille_connue_atteint_toujours_la_gouvernance(famille, monkeypatch):
    """Le garde-fou ne s'interpose que sur l'inconnu : sur une famille réelle, c'est
    toujours `can_govern` qui tranche, et son 403 est inchangé."""
    vus: list[tuple[str, str, str]] = []

    def _can_govern(sub, rtype, rid):
        vus.append((sub, rtype, rid))
        return True

    monkeypatch.setattr(ownership, "can_govern", _can_govern)
    ctx = REGLE(RAW, R.ResourceInput(op="transfer", resource_type=famille,
                                     resource_id="1246"))
    assert ctx.sub == "u1"
    # `ownership` reçoit le kind STOCKÉ de la famille publique — pour la procédure,
    # ce n'est pas le nom que l'appelant a écrit.
    assert vus == [("u1", KIND_OF[famille], "1246")]


def test_op_list_ne_passe_toujours_pas_par_la_gouvernance():
    """`list` n'a pas de `resource_id` : la règle la laisse passer et c'est le handler
    qui refuse la famille (`_check_type`). Ce chemin-là servait déjà son 400 nommé —
    il ne change pas, sinon on transformerait un 400 du handler en 400 de la règle
    pour rien."""
    ctx = REGLE(RAW, R.ResourceInput(op="list", resource_type="guide"))
    assert ctx.sub == "u1"
    with pytest.raises(AuthzDenied) as ei:
        R._check_type("guide")
    assert ei.value.status == 400 and ei.value.code == "unsupported_resource_type"
