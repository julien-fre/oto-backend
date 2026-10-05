"""Le refus d'un `id` nu enseignait, en LOT, deux gestes qui échouent (#72).

Chaque lecture sert l'identifiant **à l'intérieur** de l'objet ligne (`{"_id": "01a0…",
…}`). Un agent réécrit donc ce qu'on lui a servi, dans la forme où on le lui a servi — et
en lot, c'est refusé. Le refus qu'il reçoit alors lui conseille deux choses :

    « garde son `_id` tel que servi dans la ligne, ou passe le paramètre id= »

⚠️ **Les deux échouent sur un LOT.** Reposer l'`_id` tombe sur
`_reject_misplaced_id(batch=True)` ; passer `id=` tombe sur le refus de dispatch
(« `rows` OU `row`/`id`, pas les deux »). Un lot ne cible aucune ligne par son
identifiant, sous aucune forme : sa seule cible est la clé métier.

**Mesuré sur les comptes clients : 87 erreurs sur 132 (65,9 %) viennent de cette
famille, dont 29 « `_id` dans une row du LOT » — et 22 d'entre elles (76 %) suivent
IMMÉDIATEMENT le conseil du refus précédent.** Le défaut est auto-entretenu : notre
propre texte produit le refus suivant, et l'agent paie deux allers-retours par ligne.

⚠️ Un refus qui nomme un geste qui échoue est pire qu'un refus muet : il est CRU, et il
fait dépenser. C'est la leçon du 09/09 sur un autre refus — un avertissement
parfaitement délivré n'arrête rien, mais un conseil faux, lui, ENVOIE quelque part.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.controles import ControlesMixin

SCHEMA = {"fields": [{"key": "siren", "type": "text"}], "key": "siren"}
#: les deux gestes que le mode LOT refuse — aucun ne doit être conseillé là
REFUSES_EN_LOT = ("tel que servi", "passe le paramètre id=")


class _Store(ControlesMixin):
    """Le mixin seul : ces refus se prononcent avant toute base."""
    off_schema = set(); off_format_preavis = set(); off_geles = {}; off_notices = set()
    off_erased = []; off_rejected = []; off_ignored = []; off_non_rapprochables = {}
    dernier_tableau = None

    def _trace(self, *a, **k):
        pass


def _refus(**kw) -> str:
    with pytest.raises(ValueError) as e:
        _Store()._check_row(SCHEMA, {"id": "01a0abc", "siren": "1"}, **kw)
    return str(e.value)


def test_en_LOT_le_refus_ne_conseille_AUCUN_geste_qui_echoue():
    """⚠️ Le cœur de #72. C'est ce banc qui empêche le défaut de revenir : les deux
    gestes nommés ici seraient refusés par deux autres gardes, et 76 % des agents les
    suivaient."""
    msg = _refus(lot=True)
    for interdit in REFUSES_EN_LOT:
        assert interdit not in msg, (
            f"le refus en LOT conseille « {interdit} », que le mode lot refuse — "
            f"c'est le défaut que cette issue ferme")


def test_en_LOT_il_nomme_les_DEUX_voies_qui_aboutissent():
    """Un refus qui retire un conseil sans en donner d'autre laisse l'agent sans
    issue — il réessaiera, en consommant. Les deux voies réelles sont nommées : laisser
    la clé métier dédoublonner, ou sortir du lot pour cette ligne."""
    msg = _refus(lot=True)
    assert "clé métier" in msg and "key=" in msg, "la voie du lot"
    assert 'id="<_id>"' in msg, "la voie unitaire, pour cette ligne-là"
    assert "aucune ligne par son identifiant" in msg, "la raison, pas seulement le geste"


def test_en_UNITAIRE_le_conseil_d_origine_est_INTACT():
    """⚠️ La moitié qui garantit qu'on n'a pas cassé le cas courant : hors lot, ces
    deux gestes sont exactement les bons, et c'est la majorité du trafic."""
    msg = _refus()
    assert "tel que servi" in msg and "passe le paramètre id=" in msg


def test_les_deux_modes_gardent_la_CAUSE_et_la_sortie_par_le_schema():
    """Ce qui ne dépend pas du mode ne doit pas avoir bougé : pourquoi c'est refusé, et
    le cas où `id` est une vraie colonne des données de l'appelant."""
    for msg in (_refus(), _refus(lot=True)):
        assert "ligne fantôme" in msg, "la conséquence concrète"
        assert "data_set_schema" in msg, "la sortie si `id` est une vraie colonne"


def test_le_chemin_de_LOT_passe_bien_le_mode():
    """⚠️ La garde contre le défaut qui a produit celui-ci : un paramètre qui existe et
    que l'appelant ne passe pas ne sert à rien. Si `lots.py` cesse de le passer, le
    refus redevient trompeur — en silence, puisque les deux branches lèvent."""
    import ast
    import inspect

    from oto_mcp.datastore import lots

    # ⚠️ Par l'AST, pas par une recherche de texte. Première version de ce banc :
    # `assert "lot=True" in inspect.getsource(lots)`. Elle PASSAIT alors que l'appel
    # avait été ramené à `_check_row(schema, user_data)` — parce que la chaîne
    # survivait dans le COMMENTAIRE qui explique le paramètre. Une garde qui se
    # satisfait de son propre commentaire ne garde rien ; c'est la preuve par la
    # chute qui l'a démasquée, pas la relecture.
    arbre = ast.parse(inspect.getsource(lots))
    appels = [n for n in ast.walk(arbre)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "_check_row"]
    assert appels, "`lots.py` n'appelle plus `_check_row` du tout"
    for appel in appels:
        kwargs = {k.arg: k.value for k in appel.keywords}
        val = kwargs.get("lot")
        assert isinstance(val, ast.Constant) and val.value is True, (
            "le chemin de lot ne déclare plus son mode : le refus y redeviendrait "
            "celui qui conseille deux gestes refusés, en silence")


def test_le_chemin_de_FUSION_recoit_et_transmet_le_mode():
    """⚠️ Le trou que le premier lot a laissé (oto#72). Le mode était transmis sur la
    branche de CRÉATION du lot — et le banc ci-dessus ne regardait que `lots.py`, donc
    il ne pouvait pas le voir. Une ligne de lot qui retrouve une ligne EXISTANTE par sa
    clé métier passe par `_merge_into_row`, dont l'appel au contrôle ne déclarait aucun
    mode : elle recevait encore le conseil qui échoue au tour suivant.

    Par l'AST, et sur le paramètre PROPAGÉ (`lot=lot`), pas sur une constante : un
    `lot=True` gravé dans la fusion mentirait sur le chemin unitaire, qui passe par la
    même fonction."""
    import ast
    import inspect

    from oto_mcp.datastore import ecriture

    arbre = ast.parse(inspect.getsource(ecriture))
    fusion = next((n for n in ast.walk(arbre)
                   if isinstance(n, ast.FunctionDef) and n.name == "_merge_into_row"), None)
    assert fusion is not None, "`_merge_into_row` a disparu d'`ecriture.py`"
    assert "lot" in [a.arg for a in fusion.args.kwonlyargs], (
        "la fusion ne reçoit plus le mode : le refus y redevient celui qui conseille "
        "deux gestes refusés, en silence")
    appels = [n for n in ast.walk(fusion)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "_check_row"]
    assert appels, "la fusion n'appelle plus le contrôle"
    for appel in appels:
        val = {k.arg: k.value for k in appel.keywords}.get("lot")
        assert isinstance(val, ast.Name) and val.id == "lot", (
            "la fusion ne PROPAGE pas le mode reçu (attendu `lot=lot`)")


def test_le_chemin_de_LOT_declare_son_mode_a_la_FUSION_aussi():
    """Le pendant du précédent : recevoir le mode ne sert à rien si le lot ne le passe
    pas. Les deux appels de `lots.py` comptent — celui du chemin nominal et celui de la
    course perdue sous l'index de clé métier, qui converge en update."""
    import ast
    import inspect

    from oto_mcp.datastore import lots

    arbre = ast.parse(inspect.getsource(lots))
    appels = [n for n in ast.walk(arbre)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "_merge_into_row"]
    assert len(appels) >= 2, f"attendu les deux chemins de fusion du lot, vu {len(appels)}"
    for appel in appels:
        kwargs = {k.arg: k.value for k in appel.keywords}
        val = kwargs.get("lot")
        assert isinstance(val, ast.Constant) and val.value is True, (
            "un chemin de lot fusionne sans déclarer son mode")
        # Même famille, trouvée en corrigeant : la course perdue laissait tomber le
        # paramètre d'import, que le chemin nominal transmet.
        assert "donnees_d_origine" in kwargs, (
            "un chemin de lot fusionne sans transmettre `donnees_d_origine` : une ligne "
            "d'import y perdrait sa version d'origine")

