#!/usr/bin/env python3
"""Régénère `.env.example` depuis `oto_mcp/env_inventory.py` — SOURCE UNIQUE.

oto-backend#968 (ADR 0070). `.env.example` a longtemps été écrit à la main, au fil des
lots (13 variables au 15/09/2026, dont une MORTE — `OTO_MCP_OAUTH_PASSWORD`, retirée
dans ce même lot). Une liste recopiée à la main prend du retard par construction :
personne ne la met à jour en ajoutant une lecture, parce qu'elle n'est nulle part
VÉRIFIÉE contre le code. `tests/test_env_inventory_complet.py` verrouille l'inventaire
contre le code ; ce script verrouille `.env.example` contre l'inventaire — la chaîne
complète ne peut donc plus diverger sans qu'un test le dise.

Organisation du fichier produit, dans cet ordre — REQUISE d'abord (rien ne démarre
sans elles), puis NOTRE_DEFAUT (silencieuses mais pointent chez nous tant qu'on ne les
déclare pas), puis REGLAGE (tout le reste, à toucher seulement pour dévier du défaut) :
chaque variable de la classe NOTRE_DEFAUT porte un commentaire qui le dit EXPLICITEMENT
(demande de l'issue), pour qu'une instance tierce ne le découvre pas en production.

Usage : `python -m scripts.generer_env_example` écrit `.env.example` à la racine du
repo. `--check` n'écrit rien et sort en erreur (1) si le fichier existant diverge de ce
que l'inventaire produirait — c'est ce que joue
`tests/test_env_example_a_jour.py::test_env_example_est_derive_de_l_inventaire`.
"""
from __future__ import annotations

import pathlib
import sys

from oto_mcp import env_inventory as inv

RACINE = pathlib.Path(__file__).resolve().parent.parent
CIBLE = RACINE / ".env.example"

_ENTETE = "# oto-mcp environment\n#\n# Fichier DÉRIVÉ — ne pas éditer à la main. Régénéré depuis\n# oto_mcp/env_inventory.py par `python -m scripts.generer_env_example`\n# (oto-backend#968). Toute variable lue par le code y a une entrée ;\n# `tests/test_env_inventory_complet.py` le garantit contre le CODE et\n# `tests/test_env_example_a_jour.py` garantit CE fichier contre l'inventaire.\n"

_TITRES = {
    inv.Classe.REQUISE: (
        "# ── REQUISES — le boot (ou le premier appel qui en dépend) échoue\n"
        "# proprement sans elles. Aucun défaut : à poser explicitement. ──────────"),
    inv.Classe.NOTRE_DEFAUT: (
        "# ── DÉFAUT = UNE VALEUR À NOUS — absente, chacune pointe chez NOUS (notre\n"
        "# domaine, notre adresse). Une instance tierce DOIT les écraser sous peine\n"
        "# de nous envoyer du trafic qui ne nous appartient pas. Pas de garde de\n"
        "# boot dessus (décision du 15/09/2026) : elles se documentent, c'est tout. ──"),
    inv.Classe.REGLAGE: (
        "# ── RÉGLAGES — défaut neutre légitime en toute instance (timeout, cadence,\n"
        "# taille, rétention, interrupteur, secret optionnel). À toucher seulement\n"
        "# pour dévier du défaut. ────────────────────────────────────────────────"),
}


def _ligne_variable(v: "inv.Variable") -> str:
    corps = "\n".join(f"# {ligne}" for ligne in _enrouler(v.description))
    refs = f"# Réf. : {', '.join(v.refs)}"
    if v.classe is inv.Classe.REQUISE:
        exemple = f"{v.nom}=\n"
    elif v.defaut is None:
        exemple = f"# {v.nom}=\n"
    else:
        exemple = f"# {v.nom}={v.defaut}\n"
    if v.classe is inv.Classe.NOTRE_DEFAUT:
        corps += (f"\n# ⚠️ Défaut = NOTRE valeur ({v.defaut!r}) — à écraser pour toute "
                   "instance qui n'est pas nous.")
    return f"{corps}\n{refs}\n{exemple}"


def _enrouler(texte: str, largeur: int = 78) -> list[str]:
    mots = texte.split()
    lignes: list[str] = []
    courante = ""
    for mot in mots:
        candidate = f"{courante} {mot}".strip()
        if len(candidate) > largeur and courante:
            lignes.append(courante)
            courante = mot
        else:
            courante = candidate
    if courante:
        lignes.append(courante)
    return lignes or [""]


def _bloc_familles_dynamiques() -> str:
    if not inv.FAMILLES_DYNAMIQUES:
        return ""
    lignes = [
        "\n# ── FAMILLES DYNAMIQUES — le NOM lui-même se construit au runtime, donc\n"
        "# aucune ligne KEY=VALUE fixe ici. Posées au cas par cas (par provider, par\n"
        "# annuaire tenant) : ────────────────────────────────────────────────────\n"
    ]
    for f in inv.FAMILLES_DYNAMIQUES:
        forme = f"{f.prefixe}<...>{f.suffixe}"
        for ligne in _enrouler(f.description):
            lignes.append(f"# {ligne}\n")
        lignes.append(f"# Forme : {forme} — réf. : {', '.join(f.refs)}\n")
    return "".join(lignes)


def generer() -> str:
    blocs = [_ENTETE]
    for classe in (inv.Classe.REQUISE, inv.Classe.NOTRE_DEFAUT, inv.Classe.REGLAGE):
        variables = [v for v in inv.NOMS_FIXES if v.classe is classe]
        if not variables:
            continue
        blocs.append("\n" + _TITRES[classe] + "\n")
        for v in variables:
            blocs.append("\n" + _ligne_variable(v))
    blocs.append(_bloc_familles_dynamiques())
    return "".join(blocs)


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    contenu = generer()
    if "--check" in argv:
        actuel = CIBLE.read_text(encoding="utf-8") if CIBLE.exists() else ""
        if actuel != contenu:
            print(f"{CIBLE} diverge de l'inventaire — régénère avec "
                  "`python -m scripts.generer_env_example`.", file=sys.stderr)
            return 1
        return 0
    CIBLE.write_text(contenu, encoding="utf-8")
    print(f"{CIBLE} régénéré ({len(inv.NOMS_FIXES)} variables, "
          f"{len(inv.FAMILLES_DYNAMIQUES)} familles dynamiques non listées).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
