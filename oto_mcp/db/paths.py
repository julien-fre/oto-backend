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

from ..datastore.schema import LAYER_KEYS, VALUE_LAYER, split_layer  # noqa: F401 — ré-export

__all__ = [
    "FIELD_VALUE_PARAM_SQL", "LAYER_VALUE_PARAM_SQL", "ROW_VALUES_TEXT_SQL",
    "bkey_index_expr", "field_read_sql", "field_value_sql", "leaf_read_sql",
    "split_layer", "split_list_path",
]


# ── La VALEUR d'une case, en SQL : le jumeau d'`unwrap` (oto#163) ─────────────────
#
# `unwrap` (`datastore/couches.py`) rend `None` pour une case faite UNIQUEMENT de
# couches connues, sans `valeur` : « la valeur n'est pas encore posée ». Le SQL, lui,
# retombait sur le TEXTE de l'enveloppe — `{"origine": ""}` était compté rempli par
# `not_empty`, comparé par `eq` et `contains`, trié et regroupé comme une valeur, et
# la pose d'un enum le déclarait « hors options ». Sur un tableau de campagne : 537
# lignes mal classées sur 1 243, sans une erreur.
#
# La règle, dans cet ordre, pour une case `c` :
#   - objet qui porte `valeur`               → cette valeur ; un `null` reste NULL, il
#                                              ne retombe plus sur l'enveloppe ;
#   - objet NON VIDE fait de couches seules  → NULL ;
#   - tout le reste                          → le texte de la case : scalaire, liste,
#                                              objet métier (une clé hors couches),
#                                              et `{}` (#165, traité à part).
#
# ⚠️ La garde `jsonb_typeof = 'object'` n'est pas décorative : `?` répond VRAI sur la
# chaîne "valeur" et sur la liste ["valeur"]. Sans elle, ces deux cases se liraient
# comme des enveloppes. Et aucune sous-requête : la règle reste une expression pure.
#
# Le tableau des couches est DÉRIVÉ de `LAYER_KEYS` : une couche ajoutée au
# vocabulaire entre ici sans recopie, et le banc porte un témoin par entrée.
_COUCHES_SQL = "ARRAY[" + ", ".join(f"'{c}'" for c in LAYER_KEYS) + "]::text[]"
_OBJET_VIDE_SQL = "'{}'::jsonb"

_REGLE_VALEUR = (
    "CASE WHEN jsonb_typeof({c}) = 'object' AND {c} ? {v} THEN {c}->>{v}"
    " WHEN jsonb_typeof({c}) = 'object' AND {c} <> {vide}"
    " AND ({c} - {couches}) = {vide} THEN NULL"
    " ELSE {f} END")

#: Combien de fois la règle lit la case, donc combien de fois le champ se passe.
#: Compté sur la règle elle-même : aucun appelant n'a à le savoir.
_LECTURES = _REGLE_VALEUR.count("{c}") + _REGLE_VALEUR.count("{f}")


def _regle_texte(cellule: str, plat: str) -> str:
    """La règle rendue en texte, pour la case que lit `cellule` (en jsonb) et dont
    `plat` lit le texte. Sert les formes PARAMÉTRÉES ; la forme littérale compose
    la même règle sans quitter psycopg (`field_value_sql`)."""
    return _REGLE_VALEUR.format(c=cellule, f=plat, v=f"'{VALUE_LAYER}'",
                                vide=_OBJET_VIDE_SQL, couches=_COUCHES_SQL)


def field_value_sql(key: str) -> str:
    """SQL qui rend la VALEUR d'une colonne, plate ou à couches — le jumeau d'`unwrap`.

    Une colonne peut porter `{"valeur": …, "comment": …, "origine": …}` au lieu d'un
    scalaire. Personne ne réécrira les lignes existantes : **la table reste mixte pour
    toujours**. Tout lecteur SQL adressé par champ passe donc par ici ou par
    `field_read_sql` — filtres, tri, agrégats, contrôles de schéma — et **aucun ne
    recopie la règle** (ci-dessus, `_REGLE_VALEUR`).

    Une `valeur` vide ("") reste une valeur. Un objet sans `valeur` qui porte au moins
    une clé hors couches est une donnée `json` métier : il rend son texte. Seul un
    objet fait de couches connues, et d'elles seules, vaut NULL — comme en Python.

    ⚠️ **L'index d'unicité de clé métier ne passe PAS par ici** depuis oto#163 : son
    expression est figée dans `bkey_index_expr`. Le littéral échappé reste la forme
    des contrôles de schéma, qui composent leur requête autour.
    """
    from psycopg import sql as _sql
    k = _sql.Literal(str(key))
    # Rend un COMPOSABLE, jamais une chaîne : la composition ne quitte pas psycopg.
    # Une chaîne calculée puis re-enveloppée dans `_sql.SQL()` serait CORRECTE ici
    # (le `Literal` double les apostrophes — vérifié sur `x'; DROP TABLE …`), mais
    # la correction reposerait alors sur ce seul échappement, sans filet : une
    # édition future qui retirerait le `Literal` passerait sans que rien ne crie.
    # Signalé par la revue de sécurité automatique, et le durcissement est gratuit.
    return _sql.SQL(_REGLE_VALEUR).format(
        c=_sql.Composed([_sql.SQL("(data->"), k, _sql.SQL(")")]),
        f=_sql.Composed([_sql.SQL("data->>"), k]),
        v=_sql.Literal(VALUE_LAYER), vide=_sql.SQL(_OBJET_VIDE_SQL),
        couches=_sql.SQL(_COUCHES_SQL))


# Même règle, forme PARAMÉTRÉE — le champ passe en `%s` à chaque lecture de la case
# au lieu d'être inscrit dans le SQL. C'est la forme des filtres, du tri et des
# agrégats, et l'invariant anti-injection du module (« le champ est TOUJOURS
# paramétré ») reste intact. La liste des paramètres vient de `field_read_sql` :
# un appelant qui la compterait à la main casserait au prochain changement de règle.
FIELD_VALUE_PARAM_SQL = _regle_texte("(data->%s)", "data->>%s")

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
# ⚠️ **Les DEUX formes, comme `FIELD_VALUE_PARAM_SQL` juste au-dessus** — et pour une
# raison mesurée le 08/09/2026 : une couche peut vivre imbriquée (`data->'c'->>'link'`,
# la forme normale) OU comme une clé LITTÉRALE pointée au premier niveau
# (`data->>'c.link'`). Cette seconde forme est une RELIQUE : la garde qui l'empêche
# d'entrer est posée sur les quatre chemins d'écriture depuis le 31/08, mais ce qui a
# été écrit avant est toujours en base.
#
# Sans le `COALESCE`, un filtre sur `c.link` rendait **0 alors que la donnée existe** —
# et ce n'est pas un défaut d'affichage : c'est le geste même dont on se sert pour
# VÉRIFIER une destruction. Une campagne venait de purger trois colonnes de données de
# personnes ; le filtre lui aurait dit qu'il ne restait rien, sur une ligne qui portait
# encore la relique. Un instrument aveugle à cette place ne se rattrape pas.
LAYER_VALUE_PARAM_SQL = "COALESCE(data->%s->>%s, data->>%s)"


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
    # Chaque lecture de la case réécrit la base PUIS le champ : la liste se répète
    # autant de fois que la règle lit la case, dans l'ordre même du texte rendu.
    return (_regle_texte(f"({base_sql}->%s)", f"{base_sql}->>%s"),
            (list(base_params) + [base]) * _LECTURES)


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
        # Le nom COMPLET en troisième paramètre : c'est la relique littérale.
        return LAYER_VALUE_PARAM_SQL, [base, layer, field]
    return FIELD_VALUE_PARAM_SQL, [base] * _LECTURES


def bkey_index_expr(key: str) -> str:
    """Expression de l'index d'unicité de clé métier — ET de son lookup, qui la prend ici.

    ⚠️ **FIGÉE au texte V1 depuis oto#163, et elle ne délègue plus à `field_value_sql`.**
    Les index `ds_bkey_<ns_id>` sont des index d'EXPRESSION déjà construits en base,
    partagée entre préprod et prod. Si ce texte bougeait, le lookup
    (`db.datastore_find_row_id_by_key`) ne correspondrait plus à l'index au caractère
    près : aucune erreur, la déduplication continuerait, et chaque lookup partirait en
    parcours séquentiel. Le changer exigerait de reconstruire tous les index — un acte
    hors démarrage, pas un effet de bord de la règle de valeur.

    Pourquoi V1 suffit ici : le lookup ne cherche qu'une clé DÉBALLÉE et non vide
    (`ecriture.py`, `lots.py`, `controles.py`). Sur une telle clé, V1 et la règle de
    `field_value_sql` rendent la même chose ; ils ne divergent que sur une case sans
    valeur, qu'aucun lookup ne cherche.

    Deux épreuves la tiennent : son texte au caractère près, et le `pg_get_indexdef`
    comparé à une chaîne fixe, avec l'`EXPLAIN` du vrai lookup qui doit porter
    `Index Cond`."""
    from psycopg import sql as _sql
    k = _sql.Literal(str(key))
    return _sql.SQL(
        "COALESCE(data->{k}->>{v}, data->>{k})"
    ).format(k=k, v=_sql.Literal(VALUE_LAYER))
