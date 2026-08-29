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
    rel = json.loads(r.stdout)
    assert list(rel) == ["data_release"]


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
    r = subprocess.run([sys.executable, "scripts/empreinte_servie.py", "--diff", "HEAD",
                        "data_write", "data_claim_next"],
                       cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "aucun outil servi n'a changé" in r.stdout, (
        "comparer un état à lui-même doit être vide — sortie :\n" + r.stdout[-800:])
