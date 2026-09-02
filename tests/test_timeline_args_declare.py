"""La timeline d'un déroulé sert les mêmes arguments que la fiche d'un appel — et le dit.

Question posée par le dashboard produit le 31/08/2026, restée sans réponse : « `RunCall.
args` subit-il la même troncature et le même masquage que `CallDetail.args`, qui le
déclare ? » Mesuré le 01/09 : **oui, et sur la voie d'ÉCRITURE**, ce qui est la seule
mesure qui vaille — les deux surfaces lisent la même colonne. Seul le contrat le disait
d'un côté, d'où un front qui affichait « arguments journalisés » prudemment, faute de
savoir.

Deux choses tenues ici, et la seconde est celle qui protège la promesse :

1. `RunCall.args` porte une description, et elle nomme les deux traitements ;
2. **aucune écriture d'arguments d'outil n'échappe à `truncated_args`.** C'est elle
   qui tronque (300 caractères par valeur) et qui masque (un argument déclaré secret
   part en empreinte). Une écriture directe ferait mentir la description **des deux
   surfaces à la fois**, sans qu'aucun test de masquage existant ne bouge : ils
   exercent la fonction, pas le fait qu'on l'appelle partout.

⚠️ **Le point 2 n'a d'abord regardé qu'un seul module, et c'est ce qui l'a laissé
passer.** La première version de ce banc scannait `calllog` — le domicile déclaré du
journal — et le trouvait irréprochable. Or `tool_calls` est écrit depuis **cinq**
modules, et l'un d'eux (`tools/meta`, la trace du dispatch universel) y posait les
arguments bruts : ni tronqués, ni masqués, sur 40 159 lignes de la base (cf.
`tests/test_journal_dispatch_universel.py`). Le banc part donc désormais des modules
qui écrivent RÉELLEMENT, découverts et non listés — c'est la seule forme qui voie un
sixième chemin ajouté demain.

Le masquage et la troncature eux-mêmes sont éprouvés ailleurs
(`test_journal_no_plaintext_secret.py`, `test_journal_args_634.py`) — ce fichier ne les
rejoue pas, il garde le chaînage.

Éprouvé rouge le 2026-09-01 : sur `507784f4`, l'écriture brute de `tools/meta` est
nommée, ligne à l'appui.
"""
from __future__ import annotations

import ast
import pathlib

import oto_mcp
from oto_mcp.capabilities.org_monitoring import CallDetail, RunCall

FABRIQUE = "truncated_args"
ECRITURE = "insert_tool_call"

# Le seul dict `args` légitimement écrit sans passer par la fabrique dans `calllog` :
# le handshake `initialize`, dont les « arguments » sont trois métadonnées de client
# MCP — pas des arguments d'outil, donc rien qui puisse être secret.
HANDSHAKE = {"client_name", "client_version", "protocol_version"}


def _modules_du_journal() -> list[tuple[str, ast.Module]]:
    """Tout module du serveur qui ÉCRIT dans `tool_calls` — **découvert, jamais
    listé** : une liste écrite à la main ne verrait pas le chemin ajouté demain, et
    c'est exactement comme ça que la trace du dispatch universel a échappé au banc."""
    racine = pathlib.Path(oto_mcp.__file__).resolve().parent
    trouves = []
    for chemin in sorted(racine.rglob("*.py")):
        source = chemin.read_text(encoding="utf-8")
        # ⚠️ Le nom NU, sans parenthèse : trois des cinq écrivains ne l'appellent pas,
        # ils le PASSENT (`asyncio.to_thread(db.insert_tool_call, row)`). Chercher
        # le nom suivi d'une parenthèse ne ramenait que `calllog` — c'est-à-dire
        # précisément le module irréprochable, et aucun de ceux qui posaient le défaut.
        if ECRITURE not in source:
            continue
        nom = chemin.relative_to(racine.parent).as_posix()
        trouves.append((nom, ast.parse(source)))
    return trouves


def _valeurs_args(arbre: ast.Module) -> list[tuple[int, ast.AST]]:
    """Chaque valeur associée à la clé `args` d'une ligne de journal : la clé d'un
    dict littéral (`{"args": …}`) comme l'affectation d'une ligne déjà bâtie
    (`row["args"] = …`) — les deux formes écrivent la même colonne."""
    trouvees: list[tuple[int, ast.AST]] = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Dict):
            for cle, valeur in zip(noeud.keys, noeud.values):
                if isinstance(cle, ast.Constant) and cle.value == "args":
                    trouvees.append((getattr(valeur, "lineno", noeud.lineno), valeur))
        elif isinstance(noeud, ast.Assign):
            for cible in noeud.targets:
                if (isinstance(cible, ast.Subscript)
                        and isinstance(cible.slice, ast.Constant)
                        and cible.slice.value == "args"):
                    trouvees.append((noeud.lineno, noeud.value))
    return trouvees


def _passe_par_la_fabrique(valeur: ast.AST) -> bool:
    return any(isinstance(n, ast.Call)
               and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == FABRIQUE
               for n in ast.walk(valeur))


def _est_le_handshake(valeur: ast.AST) -> bool:
    """`calllog.on_initialize` : trois métadonnées de client, aucun argument d'outil."""
    if not isinstance(valeur, ast.Dict):
        return False
    cles = {k.value for k in valeur.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return bool(cles) and cles <= HANDSHAKE


def _est_un_enrichissement(valeur: ast.AST) -> bool:
    """`server._calllog_sink` : le sink verse dans l'`args` DÉJÀ fabriqué les entités
    résolues pendant l'appel (`ns_id`…). Il ÉTEND, il ne remplace pas — la fabrique a
    donc déjà tourné sur tout ce qui vient de l'appelant.

    ⚠️ On exige le **littéral** `"args"` dans le déballage (`{**row.get("args") …}`), pas
    la sous-chaîne : `{**raw_args}` la contiendrait et se ferait exempter alors qu'il
    remplacerait tout."""
    if not isinstance(valeur, ast.Dict):
        return False
    for cle, val in zip(valeur.keys, valeur.values):
        rendu = ast.unparse(val) if cle is None else ""
        if "'args'" in rendu or '"args"' in rendu:
            return True
    return False


def _est_l_empreinte_de_route(valeur: ast.AST, arbre: ast.Module) -> bool:
    """`api/routes.RestCallLogger` : cette ligne-là ne porte AUCUN argument d'appelant,
    seulement l'empreinte des jetons du chemin, déjà masquée par `journal_secrets`. La
    provenance est vérifiée, pas le nom : on remonte à l'affectation qui lie ce nom au
    second membre de `route_and_secrets(...)`."""
    if not isinstance(valeur, ast.Name):
        return False
    for n in ast.walk(arbre):
        if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)):
            continue
        fn = n.value.func
        if (getattr(fn, "attr", None) or getattr(fn, "id", None)) != "route_and_secrets":
            continue
        for cible in n.targets:
            if valeur.id in [e.id for e in getattr(cible, "elts", [])
                             if isinstance(e, ast.Name)]:
                return True
    return False


def _exempte(valeur: ast.AST, arbre: ast.Module) -> bool:
    return (_est_le_handshake(valeur) or _est_un_enrichissement(valeur)
            or _est_l_empreinte_de_route(valeur, arbre))


def test_les_deux_surfaces_disent_ce_qu_elles_servent():
    """`CallDetail` le disait dans son docstring, `RunCall` ne disait rien. Les deux
    doivent nommer les deux traitements — un client qui lit l'une et l'autre ne doit
    pas conclure qu'elles diffèrent."""
    champ = RunCall.model_json_schema()["properties"]["args"].get("description") or ""
    assert champ.strip(), "`RunCall.args` est servi sans description"
    assert "masqu" in champ.lower() and "tronqu" in champ.lower(), champ
    detail = ((CallDetail.model_json_schema().get("description") or "")
              + (CallDetail.model_json_schema()["properties"]["args"].get("description") or ""))
    assert "masqu" in detail.lower() and "tronqu" in detail.lower(), detail


def test_aucune_ecriture_d_arguments_n_echappe_a_la_fabrique():
    """Le garde-fou de fond, sur TOUS les modules qui écrivent la table. La description
    promet un masquage : elle ne vaut que tant que toute écriture passe par la fonction
    qui masque, d'où qu'elle parte."""
    modules = _modules_du_journal()
    assert len(modules) >= 2, (
        "banc caduc : la découverte ne voit plus qu'un seul module écrivain — c'est "
        "l'angle mort qui avait laissé passer la trace du dispatch universel")
    fautives = [f"{nom}:{ligne}"
                for nom, arbre in modules
                for ligne, valeur in _valeurs_args(arbre)
                if not _passe_par_la_fabrique(valeur) and not _exempte(valeur, arbre)]
    assert not fautives, (
        f"des arguments sont journalisés sans passer par `{FABRIQUE}` ({fautives}) : "
        "la promesse de masquage tombe pour les DEUX surfaces")


def test_le_banc_distingue_bien_les_exceptions_du_reste():
    """Témoin des exceptions : elles doivent rester TROIS, une par forme nommée
    ci-dessus. Élargie par inadvertance, n'importe laquelle laisserait passer une
    écriture directe."""
    modules = _modules_du_journal()
    comptes = {"handshake": 0, "enrichissement": 0, "empreinte": 0, "fabrique": 0}
    for _nom, arbre in modules:
        for _ligne, valeur in _valeurs_args(arbre):
            if _passe_par_la_fabrique(valeur):
                comptes["fabrique"] += 1
            elif _est_le_handshake(valeur):
                comptes["handshake"] += 1
            elif _est_un_enrichissement(valeur):
                comptes["enrichissement"] += 1
            elif _est_l_empreinte_de_route(valeur, arbre):
                comptes["empreinte"] += 1
    assert comptes["handshake"] == 1, comptes
    assert comptes["enrichissement"] == 1, comptes
    assert comptes["empreinte"] == 1, comptes
    assert comptes["fabrique"] >= 3, (
        f"le banc ne voit plus les écritures MCP, REST et dispatchée : {comptes}")
