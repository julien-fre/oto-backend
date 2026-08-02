"""Un connecteur de MARQUE doit porter son logo.

Vécu le 31/07 : la fiche Salesforce s'affichait sans logo, au milieu de connecteurs
qui en avaient un. Ce n'était pas un cas isolé — vingt connecteurs étaient dans ce
cas, dont HubSpot, Notion, Apollo et toute la famille Zoho. Chacun était un oubli
silencieux : rien ne distinguait « pas encore renseigné » de « pas de marque ».

Ce test fige la distinction : l'absence de logo doit être un CHOIX déclaré, pas un
trou. Ajouter un connecteur de marque sans son domaine casse la CI.
"""
from __future__ import annotations

from oto_mcp import providers


def _sans_logo() -> set[str]:
    return {
        n for n, c in providers.REGISTRY.items()
        if c.kind == "tools"
        and not providers._LOGO_DOMAIN_BY_CONNECTOR.get(n)
        and not c.logo_url
    }


def test_seuls_les_connecteurs_sans_marque_nont_pas_de_logo():
    oublis = sorted(_sans_logo() - providers._SANS_LOGO_DE_MARQUE)
    assert not oublis, (
        f"{oublis} : connecteur de marque sans domaine de logo. Ajoute-le à "
        "`_LOGO_DOMAIN_BY_CONNECTOR`, ou à `_SANS_LOGO_DE_MARQUE` si l'absence est "
        "délibérée (générique, maison, sources publiques hétérogènes).")


def test_la_liste_des_exceptions_ne_se_perime_pas():
    """RATCHET. Une exception qui ne correspond plus à aucun connecteur sans logo est
    soit un connecteur supprimé, soit un logo ajouté sans nettoyer la liste — dans les
    deux cas la liste ment sur ce qu'elle protège."""
    perimees = sorted(providers._SANS_LOGO_DE_MARQUE - _sans_logo())
    assert not perimees, (
        f"{perimees} : entrées mortes dans `_SANS_LOGO_DE_MARQUE` — à retirer.")


def test_les_domaines_de_logo_visent_un_connecteur_reel():
    """Un domaine keyé sur un nom qui n'existe plus ne sert rien et fait croire à une
    couverture qu'on n'a pas."""
    fantomes = sorted(set(providers._LOGO_DOMAIN_BY_CONNECTOR) - set(providers.REGISTRY))
    assert not fantomes, f"{fantomes} : domaine de logo sans connecteur correspondant."


def test_salesforce_a_bien_son_logo():
    """Le cas qui a révélé le trou — figé explicitement, c'est le connecteur d'un
    client en cours d'installation.

    On vérifie le DOMAINE, pas l'URL rendue : celle-ci dépend de `LOGODEV_TOKEN`,
    posé sur la box et absent en local — un test qui en dépendrait passerait en CI
    et mentirait partout ailleurs."""
    assert providers._LOGO_DOMAIN_BY_CONNECTOR.get("salesforce") == "salesforce.com"
