"""Le texte SERVI ne promet plus `origine: "system"`, supprimé le 08/09/2026.

Le cran armait une capture automatique : à la première écriture qui changeait une
valeur, la plateforme figeait la précédente comme origine. Il a été **supprimé**
(`declaration.system_origin_fields` rend toujours `set()`) et remplacé par
`donnees_d_origine`, un geste DÉCLARÉ qui pose la version d'origine au moment où la
valeur entre.

⚠️ **La description de `data_write` a continué de le promettre pendant un jour**, et
c'est le texte le plus lu de la plateforme. Trois affirmations, toutes fausses, sur la
garantie que le produit met en avant :

- « The safety net is `origine: "system"` » — le filet n'existe plus ;
- « writing `<field>.origine` is REFUSED, **today** » — `refus_arme()` est `False`, le
  refus est daté du 01/10/2026 ;
- « **no parameter lifts that** » — `origine_override` existe, et il est offert dans le
  schéma du même outil.

Un agent qui lisait ça renonçait à un geste permis, ou comptait sur un filet absent.
C'est la classe « le texte servi pilote l'agent » : une description d'outil est du code
de production.

**Ce banc n'interdit pas de NOMMER le cran** — il faut pouvoir dire qu'il est parti,
sinon le trafic qui l'emploie encore ne reçoit aucune explication. Il interdit de le
présenter comme disponible : toute mention doit porter son retrait.
"""
from __future__ import annotations

import inspect
import re

from oto_mcp.datastore.declaration import system_origin_fields


def test_le_mecanisme_est_bien_MORT_avant_de_juger_le_texte():
    """⚠️ D'abord la mesure, ensuite le texte. Si le cran revenait un jour, c'est CE
    banc qui doit tomber en premier — sans quoi il interdirait de documenter un
    mécanisme redevenu vivant."""
    assert system_origin_fields({"fields": [{"key": "a", "origine": "system"}]}) == set()


def test_aucun_texte_SERVI_ne_le_presente_comme_disponible():
    """Toute mention du cran, dans un texte servi, doit porter son retrait.

    ⚠️ **Ce banc a d'abord regardé UN SEUL fichier, et ligne à ligne — et il a raté la
    source la plus partagée du produit.** `description_parametre_origine()` est servie
    sur QUATRE surfaces (deux entrées d'écriture REST, l'écriture par lot, le dépôt de
    fichier) et promettait encore, le lendemain du retrait, que « la plateforme fige
    l'origine au premier enrichissement ». Les deux faces disaient donc l'inverse l'une
    de l'autre sur le même paramètre.

    ⚠️ **Et la leçon de méthode, payée le même jour** : un texte servi est presque
    toujours **coupé dans le source** (chaînes concaténées, f-strings découpées). Le
    chercher ligne à ligne rend un zéro qui n'est pas une absence — c'est ce qui m'a
    fait accuser à tort une session d'avoir inventé une citation qui existait. On
    RECONSTITUE avant de chercher.
    """
    import importlib

    modules = ["oto_mcp.tools.datastore", "oto_mcp.datastore.champs_reserves",
               "oto_mcp.capabilities.datastore.rows", "oto_mcp.capabilities.uploads"]
    fautifs = []
    for nom in modules:
        # Le SOURCE ENTIER, pas ses lignes : une phrase servie traverse les coupures.
        src = inspect.getsource(importlib.import_module(nom))
        for m in re.finditer(r'origine: ?\\?"system\\?"', src):
            # La fenêtre couvre la phrase même si elle est coupée sur plusieurs lignes.
            autour = src[max(0, m.start() - 400):m.end() + 400]
            if not re.search(r"REMOVED|removed|SUPPRIM|supprim", autour):
                extrait = " ".join(src[m.start() - 80:m.end() + 80].split())
                fautifs.append(f"{nom} … {extrait}")
    assert not fautifs, (
        "ces textes SERVIS nomment `origine: \"system\"` sans dire qu'il est "
        f"supprimé — un agent les lira comme une option disponible : {fautifs}")


def test_le_texte_COMPOSE_est_verifie_tel_qu_il_est_SERVI():
    """⚠️ Le seul contrôle qui vaille sur un texte assemblé : l'appeler.

    Lire le source aurait laissé passer une phrase construite à l'exécution — et c'est
    exactement cette fonction qui a échappé au banc précédent. On juge ce que l'agent
    reçoit, pas ce que le fichier contient."""
    from oto_mcp.datastore import champs_reserves as cr
    for texte in (cr.description_parametre_origine(),
                  cr.description_parametre_origine(en=True)):
        assert "fige l'origine au premier enrichissement" not in texte
        assert "freezes the origin at the first enrichment" not in texte
        # et il nomme le remplaçant, sinon il laisse l'agent sans conduite
        assert "donnees_d_origine" in texte


def test_les_trois_affirmations_fausses_ont_disparu():
    """Les phrases exactes qui ont menti, nommées pour qu'on ne les réintroduise pas
    par copier-coller depuis un ancien commit ou une doc."""
    import oto_mcp.tools.datastore as T
    src = inspect.getsource(T)
    for phrase in ("The safety net is `origine",
                   "is REFUSED, today",
                   "no parameter lifts that"):
        assert phrase not in src, f"phrase périmée réintroduite : {phrase!r}"


def test_le_REMPLACANT_est_nomme_là_où_le_filet_est_annoncé_absent():
    """⚠️ Dire « il n'y a plus de filet » sans dire ce qui le remplace laisse l'agent
    sans conduite — c'est la moitié d'un refus, celle qui ne sert à rien. Le geste qui
    aboutit doit être nommé au même endroit."""
    import oto_mcp.tools.datastore as T
    src = inspect.getsource(T)
    i = src.find("There is NO automatic safety net")
    assert i != -1, "l'avertissement d'absence de filet a disparu"
    voisinage = src[i:i + 1200]
    assert "donnees_d_origine" in voisinage, (
        "l'absence de filet est annoncée sans nommer le geste qui la remplace")


def test_le_refus_servi_est_la_regle_sans_date():
    """Le préavis du 01/10/2026 est passé et retiré : le texte servi dit la règle au
    présent, sans annoncer une date que plus rien ne porte."""
    from oto_mcp.datastore import champs_reserves as cr
    import oto_mcp.tools.datastore as T
    src = inspect.getsource(T)
    assert "2026-10-01" not in src
    assert "is REFUSED unless the call declares" in src
    assert "2026-10-01" not in cr.description_parametre_origine(en=True)
