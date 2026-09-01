"""La timeline d'un déroulé sert les mêmes arguments que la fiche d'un appel — et le dit.

Question posée par le dashboard produit le 31/08/2026, restée sans réponse : « `RunCall.
args` subit-il la même troncature et le même masquage que `CallDetail.args`, qui le
déclare ? » Mesuré le 01/09 : **oui, et sur la voie d'ÉCRITURE**, ce qui est la seule
mesure qui vaille — les deux surfaces lisent la même colonne, remplie par la même
fonction. Seul le contrat le disait d'un côté, d'où un front qui affichait « arguments
journalisés » prudemment, faute de savoir.

Deux choses tenues ici, et la seconde est celle qui protège la promesse :

1. `RunCall.args` porte une description, et elle nomme les deux traitements ;
2. **aucune écriture d'arguments d'outil n'échappe à `truncated_args`.** C'est elle
   qui tronque (300 caractères par valeur) et qui masque (un argument déclaré secret
   part en empreinte, y compris à travers le dispatch universel). Une écriture directe
   ajoutée un jour ferait mentir la description **des deux surfaces à la fois**, sans
   qu'aucun test de masquage existant ne bouge : ils exercent la fonction, pas le fait
   qu'on l'appelle partout.

Le masquage et la troncature eux-mêmes sont éprouvés ailleurs
(`test_journal_no_plaintext_secret.py`, `test_journal_args_634.py`) — ce fichier ne les
rejoue pas, il garde le chaînage.

Éprouvé rouge le 2026-09-01 : une écriture `"args": arguments` posée en dur dans
`calllog` ⟹ le second test la nomme, ligne à l'appui.
"""
from __future__ import annotations

import ast
import inspect

from oto_mcp import calllog
from oto_mcp.capabilities.org_monitoring import CallDetail, RunCall

FABRIQUE = "truncated_args"
# Le SEUL dict `args` légitimement écrit sans passer par la fabrique : le handshake
# `initialize`, dont les « arguments » sont trois métadonnées de client MCP — pas des
# arguments d'outil, donc rien qui puisse être secret.
HANDSHAKE = {"client_name", "client_version", "protocol_version"}


def _valeurs_args() -> list[tuple[int, ast.AST]]:
    """Chaque valeur associée à une clé `"args"` dans le module du journal."""
    arbre = ast.parse(inspect.getsource(calllog))
    trouvees = []
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Dict):
            continue
        for cle, valeur in zip(noeud.keys, noeud.values):
            if (isinstance(cle, ast.Constant) and cle.value == "args"):
                trouvees.append((getattr(valeur, "lineno", noeud.lineno), valeur))
    return trouvees


def _passe_par_la_fabrique(valeur: ast.AST) -> bool:
    return any(isinstance(n, ast.Call)
               and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == FABRIQUE
               for n in ast.walk(valeur))


def _est_le_handshake(valeur: ast.AST) -> bool:
    if not isinstance(valeur, ast.Dict):
        return False
    cles = {k.value for k in valeur.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return bool(cles) and cles <= HANDSHAKE


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
    """Le garde-fou de fond. La description promet un masquage : elle ne vaut que tant
    que TOUTE écriture passe par la fonction qui masque."""
    valeurs = _valeurs_args()
    assert valeurs, "banc caduc : plus aucune écriture d'`args` trouvée dans le journal"
    fautives = [ligne for ligne, v in valeurs
                if not _passe_par_la_fabrique(v) and not _est_le_handshake(v)]
    assert not fautives, (
        f"des arguments sont journalisés sans passer par `{FABRIQUE}` "
        f"(lignes {fautives}) : la promesse de masquage tombe pour les DEUX surfaces")


def test_le_banc_distingue_bien_le_handshake_du_reste():
    """Témoin de l'exception : elle doit rester UNE, et rester celle-là. Élargie par
    inadvertance, elle laisserait passer n'importe quelle écriture directe."""
    valeurs = _valeurs_args()
    exceptions = [ligne for ligne, v in valeurs if _est_le_handshake(v)]
    assert len(exceptions) == 1, (
        f"l'exception du handshake doit rester unique, trouvée {len(exceptions)} fois")
    fabriques = [ligne for ligne, v in valeurs if _passe_par_la_fabrique(v)]
    assert len(fabriques) >= 2, "le banc ne voit plus les écritures REST et MCP"
