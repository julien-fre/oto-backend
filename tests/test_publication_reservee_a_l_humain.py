"""Un agent ne rend rien lisible sans login (décision d'Alexis, 04/09/2026).

L'inventaire des chemins d'élargissement a montré que trois verbes ouvraient au web
depuis une conversation, et que deux le faisaient PAR DÉFAUT : `oto_project
op=publish_mcp` sans `mcp_access` publiait en `anonymous` (le web entier, et listé dans
l'annuaire public), `oto_procedure op=publish` sans `visibility` publiait en `public`.

La règle retenue est asymétrique et c'est délibéré : élargir vers l'ORG reste possible
à un agent — population nommée, comptes, administrateur, geste réparable — pendant
qu'ouvrir au WEB ne l'est plus. Ce qui est servi sans compte est indexable et
recopiable ; le retirer n'efface pas ce qui a été lu.

⚠️ Ces bancs passent par `ResolvedCtx(channel="mcp")`, c'est-à-dire par ce que le
handler reçoit. Que ce canal SOIT bien posé sur un vrai appel est une autre question,
et elle a son banc à elle (`test_canal_d_appel.py`) — sans lui, tout ce fichier
resterait vert en ne gardant plus rien.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import _publication
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

AGENT = ResolvedCtx(sub="u1", org_id=7, channel="mcp")
HUMAIN = ResolvedCtx(sub="u1", org_id=7, channel="rest")
INTERNE = ResolvedCtx(sub="u1", org_id=7)


def test_un_agent_est_refuse():
    with pytest.raises(AuthzDenied) as e:
        _publication.refuser_si_agent(AGENT, "cette page", "Passe par le dashboard.")
    assert e.value.code == "publication_reservee_a_l_humain"
    assert e.value.status == 403


def test_le_refus_dit_CE_QUI_partirait_et_PAR_OU_passer():
    """Un refus qui ne nomme pas la sortie ne stoppe pas la demande, il la déplace :
    l'agent réessaie par un autre chemin, et on perd le contrôle qu'on croyait poser.
    Il doit aussi dire ce qui reste permis, sinon il enseigne « ne partage rien »."""
    with pytest.raises(AuthzDenied) as e:
        _publication.refuser_si_agent(
            AGENT, "ce projet et les tableaux qui y sont liés",
            "La publication se fait depuis le dashboard, sur la page du projet.")
    m = e.value.message
    assert "les tableaux qui y sont liés" in m, "ce qui part avec doit être nommé"
    assert "dashboard" in m, "le geste humain équivalent doit être nommé"
    assert "élargir à l'org reste possible" in m, "sinon le refus enseigne trop large"


def test_la_face_HUMAINE_passe():
    """Le dashboard appelle la face REST : c'est là qu'un humain clique, et il doit
    pouvoir publier. Garder les deux faces serait interdire le produit."""
    _publication.refuser_si_agent(HUMAIN, "x", "y")


def test_un_contexte_SANS_canal_passe():
    """Appel interne, banc, script : pas de canal, pas de refus. Le prix de ce choix
    est écrit dans le module — une garde qui refuserait sur « pas rest » ferait
    échouer tout ce qui n'est ni l'une ni l'autre des deux faces."""
    _publication.refuser_si_agent(INTERNE, "x", "y")


# ── Les trois verbes, sur leur vrai dispatcher ────────────────────────────────

def test_oto_doc_set_public_refuse_a_l_agent(monkeypatch):
    from oto_mcp.capabilities.docs import core as D
    monkeypatch.setattr(D.db, "get_doc_by_id",
                        lambda i: {"id": 1, "title": "T", "project_id": 4})
    monkeypatch.setattr(D.common, "can", lambda sub, pid, perm: True)
    with pytest.raises(AuthzDenied) as e:
        D._doc(AGENT, D.DocInput(op="set_public", doc_id=1, public=True))
    assert e.value.code == "publication_reservee_a_l_humain"


def test_RETIRER_un_partage_public_reste_permis_a_l_agent(monkeypatch):
    """⚠️ La garde vise l'OUVERTURE, jamais la fermeture. `public=false` réduit la
    portée : le refuser laisserait un agent capable de constater une fuite et
    incapable de la refermer — le contraire de ce qu'on cherche."""
    from oto_mcp.capabilities.docs import core as D
    monkeypatch.setattr(D.db, "get_doc_by_id",
                        lambda i: {"id": 1, "title": "T", "project_id": 4})
    monkeypatch.setattr(D.common, "can", lambda sub, pid, perm: True)
    monkeypatch.setattr(D.writes, "set_public",
                        lambda sub, inp, row, pid: {"ok": True, "public": False})
    out = D._doc(AGENT, D.DocInput(op="set_public", doc_id=1, public=False))
    assert out["ok"] is True
