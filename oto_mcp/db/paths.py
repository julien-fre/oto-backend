"""Chemins et feuilles — comment on DÉSIGNE une valeur, et comment on la lit en SQL.

Extrait de `db/datastore.py` sans un changement de comportement (#325) : c'est une
couture de préoccupation, pas un découpage par taille. Tout ce qui traduit un nom
donné par l'appelant en expression SQL vit ici, et **nulle part ailleurs** — filtres,
tri, agrégats, clé métier et contrôles de schéma en dépendent tous. On a déjà payé
d'en avoir deux copies : le même filtre répondait juste sur un verbe et faux sur trois.

La grammaire que ce module porte, du plus petit au plus grand :

    email                  la valeur d'une colonne (plate OU à couches)
    email.origine          une couche de cette colonne
    contacts[0].email      l'attribut d'une fiche de rang précis
    contacts[].email       le même attribut à travers TOUS les items

Les trois premiers désignent UNE valeur : ils se filtrent, se trient et s'agrègent. Le
quatrième en désigne N — il ne se filtre que par existence, et `field_read_sql` le
refuse en le nommant.
"""
from __future__ import annotations

import re

from ..datastore_schema import VALUE_LAYER, split_layer  # noqa: F401 — ré-export

__all__ = [
    "FIELD_VALUE_PARAM_SQL", "LAYER_VALUE_PARAM_SQL", "ROW_VALUES_TEXT_SQL",
    "bkey_index_expr", "field_read_sql", "field_value_sql", "leaf_read_sql",
    "split_layer", "split_list_path",
]


def field_value_sql(key: str) -> str:
    """SQL qui rend la VALEUR d'une colonne, qu'elle soit plate ou à couches (#318).

    Une colonne peut porter `{"valeur": …, "source": …, "origine": …}` au lieu d'un
    scalaire. Personne ne réécrira les 43 782 lignes existantes : **la table reste
    mixte pour toujours**, ce n'est pas un état de transition. Tout lecteur adressé
    par champ passe donc par ici — filtres, tri, agrégats, clé métier, contrôles de
    schéma — et **aucun ne recopie l'expression** : c'est le contrat que la bascule
    du modèle de contenu transportera, et il n'existe qu'à un endroit.

    Le `COALESCE` ne se déclenche que sur NULL, donc une `valeur` vide ("") reste
    une valeur et ne retombe pas sur l'objet entier. Un champ `json` légitime qui
    se trouve être un objet sans `valeur` rend son texte, comme avant : l'expression
    ne DEVINE pas — c'est le type déclaré au schéma qui dit ce qui porte des couches,
    jamais la forme observée.

    ⚠️ Le champ est un **littéral** échappé (`psycopg.sql.Literal`), pas un
    paramètre : l'index d'unicité de clé métier est un index d'EXPRESSION, et le
    planner ne le sert au lookup que si le `WHERE` porte la MÊME chaîne. Un écart
    ne casserait rien de visible — la déduplication marcherait, chaque lookup
    partirait en seq scan.
    """
    from psycopg import sql as _sql
    k = _sql.Literal(str(key))
    # Rend un COMPOSABLE, jamais une chaîne : la composition ne quitte pas psycopg.
    # Une chaîne calculée puis re-enveloppée dans `_sql.SQL()` serait CORRECTE ici
    # (le `Literal` double les apostrophes — vérifié sur `x'; DROP TABLE …`), mais
    # la correction reposerait alors sur ce seul échappement, sans filet : une
    # édition future qui retirerait le `Literal` passerait sans que rien ne crie.
    # Signalé par la revue de sécurité automatique, et le durcissement est gratuit.
    return _sql.SQL(
        "COALESCE(data->{k}->>{v}, data->>{k})"
    ).format(k=k, v=_sql.Literal(VALUE_LAYER))


# Même expression, forme PARAMÉTRÉE — le champ passe en `%s` (deux fois) au lieu
# d'être inscrit dans le SQL. C'est la forme des filtres, du tri et des agrégats :
# eux n'ont aucun index d'expression à servir, donc rien n'exige le littéral, et
# l'invariant anti-injection du module (« le champ est TOUJOURS paramétré ») reste
# intact. Seul le chemin CLÉ MÉTIER prend `field_value_sql`, parce que lui doit
# matcher son index à la chaîne près.
FIELD_VALUE_PARAM_SQL = f"COALESCE(data->%s->>'{VALUE_LAYER}', data->>%s)"

# Les COUCHES adressables d'une colonne (#318). `valeur` n'en fait pas partie : elle
# EST la colonne, on l'atteint par son nom nu — c'est ce qui garde le contrat de
# lecture inchangé pour tout l'existant.
# Le vocabulaire vit dans le module PUR du domaine — une seule source, pas deux.
# `source` et `source_link` y sont DEUX couches, et la séparation a une raison
# opérationnelle : une source unique qui mélangerait « registre » et une URL rendrait
# `group_by champ.source` inutile — chaque URL comptant pour une provenance distincte,
# on obtiendrait autant de groupes que de lignes. Or « combien de valeurs déduites ? »
# est précisément la question de pilotage. La NATURE se groupe, la PREUVE se vérifie.

# Chemin GÉNÉRIQUE vers une sous-clé : `data->%s->>%s` (colonne, sous-clé) — les deux
# en paramètres, rien de figé. Il sert les couches aujourd'hui ; il servira tel quel le
# jour où l'on voudra filtrer la sous-clé d'un champ `json` ordinaire (oto#20), qui est
# la même question posée sur un autre vocabulaire.
#
# Pas de COALESCE ici : une sous-clé n'a pas de forme plate à laquelle retomber. Sur
# une colonne scalaire elle est NULL, et c'est la BONNE réponse — « cette valeur n'a
# pas de source » est justement la question qu'on veut pouvoir poser.
LAYER_VALUE_PARAM_SQL = "data->%s->>%s"


# Le blob RECONSTRUIT avec les valeurs à la place des enveloppes — pour tout ce qui
# lit la ligne entière en texte : recherche plein-texte, extrait, embedding sémantique.
#
# Sans ça, une colonne à couches ferait entrer sa provenance dans le texte cherché :
# `q=hunter` matcherait toute ligne dont un email VIENT de Hunter, et l'embedding
# porterait la source au même titre que le contenu. Ce n'est pas une casse — c'est
# une pollution, et elle est indétectable depuis le résultat.
#
# ⚠️ On reconstruit un JSONB puis on le sérialise, plutôt que de concaténer les
# valeurs : le texte produit est alors IDENTIQUE À L'OCTET à `data::text` sur une
# ligne plate — c'est-à-dire sur les 43 782 lignes existantes et sur tout ce qui
# n'aura jamais de couches. Une concaténation aurait changé la forme (ponctuation
# JSON perdue), donc le résultat de recherches en sous-chaîne, pour tout le monde.
_ROW_VALUES_REBUILD_SQL = (
    "COALESCE((SELECT jsonb_object_agg(k, CASE"
    " WHEN jsonb_typeof(v) = 'object' AND v ? '" + VALUE_LAYER + "'"
    " THEN v->'" + VALUE_LAYER + "' ELSE v END)"
    " FROM jsonb_each(data) AS _e(k, v)), data)::text"
)

# ⚠️ GARDÉ, et la garde vient d'une mesure, pas d'une intuition. Reconstruire le blob
# pour chaque ligne scannée coûte ×6,4 ; le faire seulement quand la ligne PORTE une
# couche ramène à ×1,5 sur une table sans couches — c'est-à-dire sur la totalité de
# l'existant. Le coût suit donc l'usage : il n'arrive qu'avec la fonctionnalité.
#
# Mesuré sur 50 000 lignes, 7 colonnes (PG 17) :
#     data::text nu ............  78 ms    projection systématique ...  498 ms  ×6,4
#     garde jsonpath ..........  113 ms    garde par sous-chaîne .....  150 ms  ×1,9
# Le pire cas (toutes les lignes à couches) revient à ×7 quelle que soit la variante —
# c'est le prix du service rendu, pas un défaut de la garde.
ROW_VALUES_TEXT_SQL = (
    "CASE WHEN jsonb_path_exists(data, '$.*." + VALUE_LAYER + "')"
    " THEN " + _ROW_VALUES_REBUILD_SQL + " ELSE data::text END"
)


# `split_layer` vit dans `datastore_schema` depuis #377 et n'est que RÉ-EXPORTÉE ici
# (les appelants la connaissent sous `db.split_layer`). Elle a déménagé parce que la
# validation de schéma en a besoin, et que ce module importe déjà `datastore_schema` :
# la garder ici en aurait fait une seconde copie, et une grammaire de chemin en deux
# exemplaires est exactement ce que ce module existe pour empêcher.


_LIST_PATH_RE = re.compile(r"^(?P<col>[^\[\]]+)\[(?P<rang>\d*)\]\.(?P<reste>.+)$")


def split_list_path(field: str):
    """`contacts[].email` → `("contacts", None, "email")` ; `contacts[0].email.origine`
    → `("contacts", 0, "email.origine")` ; autre chose → None.

    Deux formes, deux usages : le rang VIDE interroge la liste entière (« il existe un
    contact dont… »), un rang NOMMÉ vise une fiche précise — c'est ce dont la
    projection d'une migration a besoin pour résoudre un ancien nom plat."""
    m = _LIST_PATH_RE.match(str(field))
    if not m:
        return None
    rang = m.group("rang")
    return m.group("col"), (int(rang) if rang else None), m.group("reste")


def leaf_read_sql(base_sql: str, base_params: list, field: str) -> tuple:
    """La lecture d'une FEUILLE sous une base quelconque — `data` au premier niveau,
    l'item courant sous une liste. Une seule expression, deux contextes : c'est le
    principe de la feuille rendu littéral, plutôt que deux SQL à garder d'accord."""
    base, layer = split_layer(field)
    if layer:
        return f"{base_sql}->%s->>%s", base_params + [base, layer]
    return (f"COALESCE({base_sql}->%s->>'{VALUE_LAYER}', {base_sql}->>%s)",
            base_params + [base] + base_params + [base])


def field_read_sql(field: str) -> tuple:
    """`(fragment SQL, paramètres)` pour lire ce que l'appelant a désigné.

    Un nom nu lit la VALEUR (plate ou à couches) ; `champ.source` lit la couche ;
    `contacts[0].email` lit l'attribut d'une fiche de rang précis. **Les trois se
    filtrent, se trient et s'agrègent pareil** — c'est ici que l'uniformité des verbes
    se joue, et on a déjà payé de la perdre : le même filtre répondait juste sur un
    verbe et faux sur trois, parce que la résolution était recopiée ailleurs.

    `contacts[].email` (TOUS les items) n'a pas sa place ici : il ne désigne pas UNE
    valeur mais N, donc rien à trier ni à regrouper. Refusé en le nommant plutôt que
    rendu comme s'il valait le premier item — un ordre reproductible et faux."""
    chemin = split_list_path(field)
    if chemin is not None:
        colonne, rang, reste = chemin
        if rang is None:
            raise ValueError(
                f"`{field}` désigne TOUS les items de `{colonne}` : il n'a pas une "
                f"valeur mais N, donc il ne se trie ni ne se regroupe (il se FILTRE, "
                f"par existence). Viser un rang précis — `{colonne}[0].{reste}`.")
        # Le rang vient d'un `\d+` converti en entier : l'inscrire dans le SQL n'est
        # pas une interpolation de saisie.
        return leaf_read_sql(f"data->%s->{int(rang)}", [colonne], reste)
    base, layer = split_layer(field)
    if layer:
        return LAYER_VALUE_PARAM_SQL, [base, layer]
    return FIELD_VALUE_PARAM_SQL, [base, base]


def bkey_index_expr(key: str) -> str:
    """Expression indexée pour la clé métier — LA MÊME que celle du lookup.

    Délègue plutôt que de recopier : la dérive entre les deux est impossible par
    construction, et le test qui compare les deux chaînes garde l'invariant si
    quelqu'un rompt un jour cette délégation."""
    return field_value_sql(key)
