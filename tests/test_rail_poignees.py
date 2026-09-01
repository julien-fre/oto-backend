"""Le rail dit où mène chaque ligne — et ce que son identifiant désigne vraiment.

Deux manques mesurés sur le document servi le 2026-09-01 (#650, points 2 et 3) :

- **`doc_id` n'était pas servi** alors que la requête du rail lit DÉJÀ la clé legacy
  pour la référence de procédure, et la jetait pour tout le reste. Ouvrir une page
  depuis le rail imposait donc un aller-retour par ligne, uniquement pour récupérer un
  entier que la ligne SQL portait.
- **`RailNode` n'avait aucune description**, alors que son `id` **change de nature
  selon `type`** : identifiant de nœud pour une page, un tableau, un agent —
  identifiant de DÉROULÉ pour une exécution, qui n'a pas de fiche et rend 404 sur
  `GET /api/me/nodes/{id}`. Un client qui traite toutes les lignes pareil casse sur
  celle-là, et rien ne l'en avertissait.

⚠️ **Le troisième test est le moins évident et le plus utile.** La dérivation de
`doc_id` existe déjà pour la fiche ; la recopier au rail créerait deux écritures d'une
même règle. Celle qui se tromperait ne le montrerait pas — elle ouvrirait la page d'un
autre nœud, sans erreur. Le rail appelle donc la fonction de la fiche, et ce fichier
refuse qu'une seconde dérivation réapparaisse.

Éprouvé rouge le 2026-09-01 : `doc_id` retiré du modèle ⟹ premier test ; règle
recopiée en dur dans `shell.py` ⟹ troisième.
"""
from __future__ import annotations

import ast
import inspect

from oto_mcp.capabilities import shell
from oto_mcp.capabilities.node_keys import doc_id_de
from oto_mcp.capabilities.shell import RailNode


def test_le_rail_porte_la_poignee_vers_la_page():
    assert "doc_id" in RailNode.model_fields


def test_la_nature_de_l_identifiant_est_DITE_au_contrat():
    """Un `str` nu laissait croire à un identifiant uniforme. Ce qui doit voyager
    n'est pas un champ de plus, c'est la phrase qui dit que le même champ ne désigne
    pas la même chose selon la ligne."""
    schema = RailNode.model_json_schema()
    modele = schema.get("$defs", {}).get("RailNode", schema)
    description_id = (modele["properties"]["id"].get("description") or "")
    assert "execution" in description_id, "la nature d'un id d'exécution n'est pas dite"
    assert (modele.get("description") or "").strip(), "RailNode reste sans docstring"


def test_la_derivation_de_doc_id_reste_a_UN_SEUL_endroit():
    """Contre-test de divergence, sur le modèle de celui qui garde la résolution du
    namespace. Deux endroits qui dérivent la même clé finissent par diverger, et le
    faux résultat n'a aucun symptôme : il ouvre une page qui existe."""
    arbre = ast.parse(inspect.getsource(shell))
    litteraux = [n for n in ast.walk(arbre)
                 if isinstance(n, ast.Constant) and n.value == "doc"]
    assert not litteraux, (
        "`shell.py` compare une clé legacy en dur : la dérivation doit rester dans "
        "`node_keys.doc_id_de`, appelée par les deux surfaces")


def test_seule_une_page_a_un_doc_id():
    """`null` ailleurs, jamais un entier deviné : un projet, un tableau natif et une
    exécution n'ont pas de document derrière eux."""
    assert doc_id_de("doc", 1167) == 1167
    assert doc_id_de("doc", "1167") == 1167     # la colonne SQL rend du texte
    assert doc_id_de("prj", 59) is None         # un projet n'est pas une page
    assert doc_id_de(None, None) is None        # un nœud natif n'a pas de source
    assert doc_id_de("doc", None) is None       # clé annoncée, valeur absente


def test_la_poignee_est_ABSENTE_quand_il_n_y_a_rien_a_dire():
    """Le rail omet les `None` (`exclude_none`) : une ligne sans page derrière elle ne
    porte pas un `doc_id: null` que le client devrait tester, elle ne le porte pas du
    tout — la convention déjà tenue par `procedure` et `more`."""
    sans = RailNode(id="nod_1", name="Tableau natif", type="table")
    assert "doc_id" not in sans.model_dump(exclude_none=True)

    avec = RailNode(id="nod_2", name="Une page", type="page", doc_id=1167)
    assert avec.model_dump(exclude_none=True)["doc_id"] == 1167
