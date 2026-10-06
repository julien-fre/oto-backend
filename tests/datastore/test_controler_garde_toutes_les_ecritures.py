"""Garde : TOUT chemin qui écrit une charge d'appelant passe par `mots_deprecies.controler`
AVANT la fusion (oto#140, J3).

`@keep` et `@clear` sont retirés. Leur résolution a disparu de la fusion : la seule chose
qui les empêche d'être STOCKÉS comme du texte — puis servis à une cliente comme sa propre
donnée — est le refus à l'entrée. Ce banc en garde la preuve, structurellement :

1. il relève, dans tout `oto_mcp/`, chaque appel aux fonctions qui FUSIONNENT ou
   INSÈRENT une ligne de tableau (`SENSIBLES`) ;
2. chaque appelant doit être soit une de ces fonctions (la chaîne interne), soit une
   ENTRÉE d'écriture du store (`ENTREES`), soit un chemin HORS CONTRAT nommé avec sa
   raison (aucune charge d'appelant n'y passe) ;
3. chaque ENTRÉE appelle `mdp.controler(` avant son premier appel sensible.

Un nouveau point d'entrée qui fusionnerait sans `controler` fait tomber le point 2 : il
faut alors le brancher sur `controler`, pas l'ajouter à une liste.

Les surfaces au-dessus des entrées n'ont pas à être listées : MCP `data_write`, REST
`POST/PATCH …/rows` et `…/rows/batch`, l'upload signé (`upload_tokens.import_rows`),
`oto_import`, la promotion `_id`, les écritures `service:` — toutes appellent
`append_row`, `update_row` ou `write_rows`, donc une ENTRÉE.
"""
from __future__ import annotations

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PAQUET = RACINE / "oto_mcp"

#: Ce qui fusionne une charge dans une ligne, ou l'insère : appelé hors d'une ENTRÉE,
#: une chaîne `@keep` y entrerait telle quelle.
SENSIBLES = {
    "_merge_column", "_merge_items", "_sentinelles_dans_les_items", "_resoudre_la_fiche",
    "_vider", "mots_resolus_a_la_creation", "refuser_cle_metier_vide",
    "fusionner", "_fusionner_l_element", "_nouvel_element", "appliquer@rangs",
    "_merge_into_row", "datastore_merge_row_locked", "datastore_insert_row",
}

#: Les entrées d'écriture du store — chacune doit appeler `mdp.controler(` avant toute
#: fusion.
ENTREES = {
    "oto_mcp/datastore/ecriture.py:EcritureMixin.append_row",
    "oto_mcp/datastore/ecriture_par_id.py:EcritureParIdMixin.update_row",
    "oto_mcp/datastore/lots.py:LotsMixin._write_rows_to_ns",
}

#: Appelants qui n'écrivent AUCUNE charge d'appelant — avec la raison.
HORS_CONTRAT = {
    # Recalcule les colonnes `formula` d'une ligne en place : valeurs calculées.
    "oto_mcp/formula_backfill_worker.py:_backfill_round",
    # Copie d'un projet : recopie des lignes DÉJÀ stockées (passées par une entrée).
    "oto_mcp/db/projects.py:_provision_tableau",
}


def _cle_de_l_appel(noeud: ast.Call) -> str | None:
    f = noeud.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        # `rangs.appliquer(...)` : le nom seul est trop commun pour être suivi partout.
        if f.attr == "appliquer":
            return ("appliquer@rangs" if isinstance(f.value, ast.Name)
                    and f.value.id == "rangs" else None)
        return f.attr
    return None


def _appels(chemin: Path, racine: Path = RACINE):
    """`(appelant qualifié, nom sensible, ligne)` pour chaque appel sensible du fichier ;
    l'appelant est la fonction de premier niveau, ou `Classe.methode` — une fonction
    imbriquée (`_apply`) compte pour celle qui l'enferme."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    rel = chemin.relative_to(racine).as_posix()

    def _dans(noeud, qualifie):
        for n in ast.walk(noeud):
            if isinstance(n, ast.Call):
                cle = _cle_de_l_appel(n)
                if cle in SENSIBLES:
                    yield f"{rel}:{qualifie}", cle, n.lineno

    for n in arbre.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _dans(n, n.name)
        elif isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield from _dans(m, f"{n.name}.{m.name}")


def _tous_les_appels() -> list:
    return [a for f in sorted(PAQUET.rglob("*.py")) for a in _appels(f)]


def _nom_court(qualifie: str) -> str:
    fonction = qualifie.split(":", 1)[1].rsplit(".", 1)[-1]
    return "appliquer@rangs" if qualifie.endswith("EcrituresParRang.appliquer") else fonction


def test_aucune_fusion_hors_d_une_entree_gardee():
    intrus = sorted({appelant for appelant, _cle, _l in _tous_les_appels()
                     if appelant not in ENTREES and appelant not in HORS_CONTRAT
                     and not (_nom_court(appelant) in SENSIBLES
                              and appelant.startswith("oto_mcp/datastore/"))})
    assert not intrus, (
        "ces fonctions fusionnent ou insèrent une ligne sans être une entrée gardée par "
        f"`mots_deprecies.controler` : {intrus}. Branche-les sur une entrée du store "
        "(`append_row`, `update_row`, `write_rows`) ou sur `controler` — sinon `@keep` "
        "y serait stocké comme du texte.")


def test_chaque_entree_controle_AVANT_sa_premiere_fusion():
    appels = _tous_les_appels()
    for entree in ENTREES:
        fichier, qualifie = entree.split(":")
        classe, methode = qualifie.split(".")
        arbre = ast.parse((RACINE / fichier).read_text(encoding="utf-8"))
        fn = next(m for c in arbre.body if isinstance(c, ast.ClassDef) and c.name == classe
                  for m in c.body if isinstance(m, ast.FunctionDef) and m.name == methode)
        controles = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == "controler"
                     and isinstance(n.func.value, ast.Name) and n.func.value.id == "mdp"]
        assert controles, f"{entree} n'appelle pas `mdp.controler`"
        premieres = [ligne for appelant, _cle, ligne in appels if appelant == entree]
        assert premieres, f"{entree} ne fusionne plus rien : la liste ENTREES est périmée"
        assert min(controles) < min(premieres), (
            f"{entree} fusionne (ligne {min(premieres)}) avant `mdp.controler` "
            f"(ligne {min(controles)})")


def test_les_listes_ne_sont_pas_perimees():
    """Une entrée ou une exception qui n'appelle plus rien de sensible doit sortir de sa
    liste — sinon la garde finirait par couvrir un chemin qui n'existe plus."""
    appelants = {appelant for appelant, _cle, _l in _tous_les_appels()}
    assert ENTREES <= appelants, ENTREES - appelants
    assert HORS_CONTRAT <= appelants, HORS_CONTRAT - appelants


def test_la_garde_mord_sur_un_appel_non_garde(tmp_path):
    """La garde elle-même : un module qui fusionne hors d'une entrée est vu, et une
    méthode qui passe par `rangs.appliquer` aussi."""
    faux = tmp_path / "faux.py"
    faux.write_text("def ecrire(x):\n    return _merge_column(None, x)\n\n\n"
                    "class C:\n    def m(self, rangs):\n"
                    "        return rangs.appliquer({}, None)\n", encoding="utf-8")
    assert {a for a, _c, _l in _appels(faux, tmp_path)} == {"faux.py:ecrire", "faux.py:C.m"}
