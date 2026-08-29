"""L'outil qui mesure l'empreinte servie doit mesurer ce qui est SERVI (#575, 29/08).

Le script `scripts/empreinte_servie.py` est devenu la source du chiffre qu'une PR
annonce quand elle allonge une description. Un instrument de mesure qui dérive sans
qu'on le voie est pire que pas d'instrument : il donne un chiffre, donc on le croit.

Deux défauts vécus le jour de son écriture, tous deux silencieux, tous deux figés ici :

1. **Il mesurait la docstring, pas le texte servi.** Le harnais retire le bloc `Args:`
   et désindente : sur `data_write`, 2 776 caractères de docstring pour 2 058 servis.
   Un delta annoncé à `+162` valait `+75` en réalité.
2. **Il importait le paquet depuis l'installation éditable du venv**, c'est-à-dire un
   AUTRE checkout — `python scripts/x.py` met `scripts/` en tête du chemin, pas la
   racine. Le premier `--diff` annonçait quinze outils modifiés par une PR qui n'en
   touchait aucun.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _outils_modifies_dans_l_arbre() -> str:
    """`--diff <ref>` compare l'ARBRE DE TRAVAIL à la référence — pas la référence à
    elle-même. Le témoin du diff vide ne vaut donc que si rien n'est modifié sous
    `oto_mcp/` : en CI toujours, chez un développeur au milieu d'un lot, non."""
    r = subprocess.run(["git", "status", "--porcelain", "--", "oto_mcp"],
                       cwd=RACINE, capture_output=True, text=True)
    return r.stdout.strip()


def test_le_releve_mesure_le_texte_SERVI_et_pas_la_docstring():
    """La description servie est plus courte que la docstring : c'est ça qu'on compte."""
    sys.path.insert(0, str(RACINE))
    from scripts.empreinte_servie import relever

    from oto_mcp.tools import datastore as t_ds

    rel = relever(["data_write"])
    assert "data_write" in rel, "l'outil doit être monté comme le serveur le monte"
    servie = rel["data_write"]["description"]

    # La docstring brute, telle qu'un comptage à la main l'aurait prise.
    src = Path(t_ds.__file__).read_text()
    debut = src.index('def data_write(')
    docstring = src[src.index('"""', debut) + 3: src.index('"""', src.index('"""', debut) + 3)]

    assert servie > 0
    assert servie < len(docstring), (
        "la description servie doit être PLUS COURTE que la docstring (le bloc `Args:` "
        f"n'est pas servi) — servie {servie}, docstring {len(docstring)}")


def test_le_releve_porte_la_description_ET_le_schema():
    """Un paramètre ajouté change ce que le modèle lit autant qu'une phrase."""
    sys.path.insert(0, str(RACINE))
    from scripts.empreinte_servie import relever
    rel = relever(["data_write", "data_claim_next"])
    for nom, v in rel.items():
        assert set(v) == {"description", "schema", "sha256"}, nom
        assert v["schema"] > 0, f"{nom} : le schéma d'entrée doit être mesuré"
        assert len(v["sha256"]) == 12


def test_le_script_tourne_a_la_main_et_rend_du_json():
    """Il s'emploie à la main avant une PR — donc il doit marcher hors pytest."""
    r = subprocess.run([sys.executable, "scripts/empreinte_servie.py", "--json",
                        "data_release"], cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    import json
    sortie = json.loads(r.stdout)
    assert set(sortie) == {"portee", "outils"}, "le JSON porte le relevé ET sa portée"
    assert list(sortie["outils"]) == ["data_release"]


def test_il_mesure_LE_depot_ou_il_tourne_et_pas_un_autre_checkout():
    """⚠️ Le défaut le plus coûteux, parce qu'il rend un chiffre plausible et faux.

    `oto_mcp` est installé en éditable dans le venv : sans la racine du dépôt en tête
    du chemin d'import, le script mesure le checkout de l'installation — un tree que
    d'autres sessions modifient — au lieu de celui où il tourne."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         " from scripts.empreinte_servie import relever;"
         " relever(['data_write']);"
         " import oto_mcp; print(oto_mcp.__file__)"],
        cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    charge = Path(r.stdout.strip()).resolve()
    assert str(charge).startswith(str(RACINE)), (
        f"le script a chargé `oto_mcp` depuis {charge}, hors du dépôt mesuré {RACINE}")


def test_comparer_un_etat_a_LUI_MEME_ne_montre_aucun_changement():
    """Le témoin qui ferme les trois pièges d'un coup — et il en a attrapé deux.

    `--diff HEAD` doit être vide par construction. Il ne l'était pas :

    - le script du clone était celui que la RÉFÉRENCE portait, pas celui qui mesure ;
    - `origin/main` désignait, dans le clone, la branche locale du dépôt de départ —
      souvent en retard de plusieurs jours sur le tronc réel.

    Les deux rendaient des deltas plausibles et faux, ce qui est le pire des deux : un
    chiffre absent se remarque, un chiffre faux se cite."""
    modifs = _outils_modifies_dans_l_arbre()
    if modifs:
        import pytest
        pytest.skip("arbre modifié sous oto_mcp/ — le témoin ne vaut "
                    "que sur un arbre propre :\n" + modifs)
    r = subprocess.run([sys.executable, "scripts/empreinte_servie.py", "--diff", "HEAD",
                        "data_write", "data_claim_next"],
                       cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "aucun outil servi n'a changé" in r.stdout, (
        "comparer un état à lui-même doit être vide — sortie :\n" + r.stdout[-800:])


# ── Un rapport nomme ce qu'il ne regarde pas ─────────────────────────────────

def test_le_rapport_DELIMITE_sa_portee_en_tete():
    """Tous les outils ne viennent pas du code : les connecteurs fédérés sont montés
    d'après la base. Sans base, ils manquent — **et un rapport muet là-dessus se lit
    comme s'il couvrait tout.** La ligne de portée est donc la première du rapport."""
    r = subprocess.run([sys.executable, "scripts/empreinte_servie.py", "data_write"],
                       cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    tete = r.stdout.splitlines()[0]
    assert tete.startswith("portée :"), f"première ligne inattendue : {tete!r}"
    assert "base" in tete


def test_la_portee_est_aussi_dans_le_json():
    sys.path.insert(0, str(RACINE))
    from scripts.empreinte_servie import portee
    p = portee()
    assert set(p) >= {"base", "connecteurs_montables", "connecteurs_montes", "non_regardes"}
    assert p["base"] in ("lue", "indisponible")
    # Ce qui n'est pas monté est exactement ce qui n'est pas regardé.
    assert set(p["non_regardes"]) == set(p["connecteurs_montables"]) - set(p["connecteurs_montes"])


def test_le_temoin_du_diff_vide_vaut_AUSSI_sur_la_ligne_de_portee():
    """Comparer un état à lui-même ne montre aucun changement — et le rapport dit
    quand même ce qu'il n'a pas regardé. Un rapport vide sans portée laisserait croire
    que « rien n'a changé » couvre tout ; il ne couvre que ce qui a été monté."""
    modifs = _outils_modifies_dans_l_arbre()
    if modifs:
        import pytest
        pytest.skip("arbre modifié sous oto_mcp/ — le témoin ne vaut "
                    "que sur un arbre propre :\n" + modifs)
    r = subprocess.run([sys.executable, "scripts/empreinte_servie.py", "--diff", "HEAD",
                        "data_write"], cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "aucun outil servi n'a changé" in r.stdout
    assert "portée :" in r.stdout, "un delta vide doit quand même délimiter sa portée"


def test_deux_portees_differentes_refusent_de_se_soustraire():
    """⚠️ Le cas qui rendrait un chiffre faux sans que rien ne le signale : un côté
    mesuré avec la base, l'autre sans. Les connecteurs manquants sortiraient en
    « RETIRÉS » alors que personne ne les a retirés."""
    sys.path.insert(0, str(RACINE))
    from scripts.empreinte_servie import _portees_comparables
    meme = {"base": "lue", "connecteurs_montes": ["a", "b"]}
    assert _portees_comparables(meme, dict(meme)) is True
    autre = {"base": "indisponible", "connecteurs_montes": ["a"]}
    assert _portees_comparables(meme, autre) is False
