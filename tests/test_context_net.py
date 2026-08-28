"""Le filet : le contexte de l'org arrive même sans être demandé (oto-backend#478).

Le canal prévu — le champ `instructions` de l'`initialize` — n'est pas fiable : deux
clients mesurés, deux façons de ne pas délivrer (tronqué à 2048 sous Claude Code, non
montré au modèle sur claude.ai). Or ce bloc porte les GARDE-FOUS de l'org, pas
seulement des conseils : « fais valider avant tout envoi externe ». Tant qu'il dépend
d'un appel volontaire, un agent qui va droit à `email_send` ne l'a jamais lu.
"""
import asyncio

import pytest
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from oto_mcp.middleware import context_net as cn

_COMPOSE_REEL = cn._compose


class _Msg:
    def __init__(self, name): self.name = name


class _Ctx:
    def __init__(self, name): self.message = _Msg(name)


def _resultat(texte="donnée", structuree=None, erreur=False):
    return ToolResult(content=[TextContent(type="text", text=texte)],
                      structured_content=structuree, is_error=erreur)


@pytest.fixture(autouse=True)
def _table_rase(monkeypatch):
    cn._servi.clear()
    monkeypatch.setattr(cn, "current_user_sub_from_token", lambda: "u-1")
    monkeypatch.setattr(cn, "_compose", lambda sub: (35, "RÈGLES DE L'ORG"))
    monkeypatch.delenv("OTO_CONTEXT_NET", raising=False)


def _appel(nom="serper_search", res=None):
    mw = cn.ContextNetMiddleware()
    res = res if res is not None else _resultat()
    return asyncio.run(mw.on_call_tool(_Ctx(nom), lambda ctx: _async(res)))


async def _async(v):
    return v


def _texte(r):
    return "\n".join(c.text for c in r.content if getattr(c, "text", None))


# --- le filet livre ----------------------------------------------------------

def test_le_premier_appel_doutil_livre_le_contexte():
    r = _appel()
    assert "RÈGLES DE L'ORG" in _texte(r)


def test_le_bloc_est_balise_et_annonce_pour_ce_quil_est():
    """Sans balise, le modèle lit la prose comme une sortie d'outil et la recopie —
    le défaut de décodage déjà vécu sur un résultat vide servi en structure nue."""
    t = _texte(_appel())
    assert "<oto-contexte-organisation>" in t
    assert "</oto-contexte-organisation>" in t
    assert "n'est PAS le résultat de l'outil" in t


def test_le_resultat_de_loutil_part_intact():
    """Le filet AJOUTE, il ne remplace pas : un agent qui parse ne voit rien changer."""
    r = _appel(res=_resultat("donnée métier", structuree={"rows": [1, 2]}))
    assert "donnée métier" in _texte(r)
    assert r.structured_content == {"rows": [1, 2]}


# --- et ne se répète pas ------------------------------------------------------

def test_le_deuxieme_appel_ne_relivre_pas():
    _appel()
    assert "RÈGLES DE L'ORG" not in _texte(_appel())


def test_un_chargement_volontaire_vaut_livraison(monkeypatch):
    """Appeler `oto_context` soi-même doit désarmer le filet — sinon l'agent qui fait
    bien les choses reçoit le bloc deux fois."""
    monkeypatch.setattr(cn, "_compose", lambda sub: pytest.fail("ne pas composer"))
    import oto_mcp.access as access
    monkeypatch.setattr(access, "current_org", lambda sub: 35)
    _appel(nom="oto_context")
    monkeypatch.setattr(cn, "_compose", lambda sub: (35, "RÈGLES DE L'ORG"))
    assert "RÈGLES DE L'ORG" not in _texte(_appel())


# --- et ne casse jamais rien --------------------------------------------------

def test_un_appel_en_echec_nest_pas_le_moment():
    """Le modèle lit une erreur ; lui empiler dessus un bloc d'instructions sans
    rapport brouille les deux."""
    r = _appel(res=_resultat("boom", erreur=True))
    assert "RÈGLES DE L'ORG" not in _texte(r)


def test_une_composition_en_echec_laisse_passer_lappel(monkeypatch):
    def _boom(sub): raise RuntimeError("base indisponible")
    monkeypatch.setattr(cn, "_compose", _boom)
    r = _appel()
    assert _texte(r) == "donnée"


def test_sans_identite_le_filet_se_tait(monkeypatch):
    monkeypatch.setattr(cn, "current_user_sub_from_token", lambda: None)
    assert _texte(_appel()) == "donnée"


def test_un_contexte_vide_ne_livre_rien(monkeypatch):
    monkeypatch.setattr(cn, "_compose", lambda sub: (35, ""))
    assert _texte(_appel()) == "donnée"


def test_le_cran_darret_coupe_tout(monkeypatch):
    """Ce middleware retouche le rendu d'un résultat et compose du DB à chaud : il doit
    pouvoir être coupé par l'environnement, sans rollback ni redéploiement."""
    monkeypatch.setenv("OTO_CONTEXT_NET", "0")
    assert _texte(_appel()) == "donnée"


# --- couper pour UNE org, pas pour tout le monde ------------------------------

def _sans_stub_de_compose(monkeypatch, org):
    """Rend le VRAI `_compose` (la fixture le remplace par défaut) et pilote ses deux
    dépendances — c'est lui qu'on veut exercer ici."""
    monkeypatch.setattr(cn, "_compose", _COMPOSE_REEL)
    from oto_mcp import access
    monkeypatch.setattr(access, "current_org", lambda sub: org)


def test_une_org_peut_etre_exclue_sans_couper_les_autres(monkeypatch):
    """Un cran tout-ou-rien obligerait à choisir entre la mesure d'une org et le
    garde-fou de toutes les autres. Demandé par une campagne dont chaque fiche est une
    session neuve : le bloc s'y ajouterait au premier résultat de CHAQUE fiche.

    Et l'org exclue ne paie même pas la lecture — la sortie est AVANT la composition,
    qui traverse la cascade de statut de tous les connecteurs."""
    monkeypatch.setenv("OTO_CONTEXT_NET_EXCLUDE_ORGS", "226")
    _sans_stub_de_compose(monkeypatch, 226)
    from oto_mcp import instructions
    monkeypatch.setattr(instructions, "_block_c",
                        lambda s, o: pytest.fail("une org exclue ne compose même pas"))
    assert _texte(_appel()) == "donnée"


def test_une_org_non_exclue_recoit_toujours(monkeypatch):
    monkeypatch.setenv("OTO_CONTEXT_NET_EXCLUDE_ORGS", "226")
    _sans_stub_de_compose(monkeypatch, 35)
    from oto_mcp import instructions
    monkeypatch.setattr(instructions, "_block_c", lambda s, o: "RÈGLES DE L'ORG")
    assert "RÈGLES DE L'ORG" in _texte(_appel())


def test_une_liste_vide_ou_bruitee_nexclut_personne(monkeypatch):
    for valeur in ("", "  ", "abc", ","):
        monkeypatch.setenv("OTO_CONTEXT_NET_EXCLUDE_ORGS", valeur)
        assert cn._orgs_exclues() == set(), valeur
    monkeypatch.setenv("OTO_CONTEXT_NET_EXCLUDE_ORGS", "226, 35")
    assert cn._orgs_exclues() == {226, 35}
