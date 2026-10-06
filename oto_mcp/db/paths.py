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
    tags[0]                l'élément de rang précis d'une liste de VALEURS (oto#102)
    contacts[].email       le même attribut à travers TOUS les items
    tags[]                 TOUS les éléments d'une liste de valeurs (oto#102)

Les quatre premiers désignent UNE valeur : ils se filtrent, se trient et s'agrègent.
Les deux derniers en désignent N — ils se filtrent par existence et s'agrègent par
occurrence (un élément, une occurrence : `list_items_sql`), mais ne se trient pas, et
`field_read_sql` les refuse en les nommant.
"""
from __future__ import annotations

import re

from ..datastore.schema import LAYER_KEYS, VALUE_LAYER, split_layer  # noqa: F401 — ré-export

__all__ = [
    "DDL_FONCTION_VALEURS_TEXTE", "FIELD_VALUE_PARAM_SQL", "FONCTION_VALEURS_TEXTE",
    "LAYER_VALUE_PARAM_SQL", "ROW_SERVED_VALUES_TEXT_SQL", "ROW_VALUES_TEXT_SQL",
    "SQL_FONCTION_VALEURS_PRESENTE",
    "bkey_index_expr", "field_read_sql", "field_value_sql", "leaf_read_sql",
    "list_items_sql", "split_layer", "split_list_path",
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

    ⚠️ **L'index d'unicité de clé métier passe par ici** depuis oto#223
    (`bkey_index_expr`) : son texte est donc celui de cette règle, au caractère près,
    et le changer oblige à reconstruire les index `ds_bkey_<ns>` par une migration.
    Le littéral échappé reste aussi la forme des contrôles de schéma, qui composent
    leur requête autour.
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
# lit la ligne entière en texte : l'embedding sémantique (`datastore_embed`). La
# recherche `q` l'a lu du 13/08 au 05/10/2026 ; depuis #307 elle choisit son texte
# (`q_scope`) : `data::text` entier, ou les valeurs servies seules
# (`ROW_SERVED_VALUES_TEXT_SQL`, ci-dessous) — les deux indexés, ce que ce blob, fait
# d'une sous-requête, ne peut pas être.
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


# ── Les VALEURS SERVIES d'une ligne, en texte : la portée `values` de `q` (#307) ────
#
# `ROW_VALUES_TEXT_SQL` ci-dessus garde la FORME du JSON (clés, guillemets,
# échappements) : c'était le prix de l'identité à l'octet avec `data::text`. La
# recherche `q_scope=values` veut l'inverse — ce qu'un LECTEUR reçoit, rien d'autre :
#   - la valeur de chaque case, déballée par la règle de lecture (`_REGLE_VALEUR`, le
#     jumeau d'`unwrap`) ; une case faite de couches seules ne contribue rien ;
#   - un cran plus bas pour une liste de fiches, comme `served_value` : chaque attribut
#     d'un item est déballé par la même règle ;
#   - de tout cela, les FEUILLES seules (chaînes, nombres, booléens), en texte nu —
#     ni clé, ni couche (`origine`/`comment`/`link`), ni échappement JSON.
#
# ⚠️ **Une FONCTION, et IMMUTABLE, parce que c'est la seule forme indexable.** Une
# expression d'index ne peut pas porter de sous-requête ; le déroulé des cases en
# exige une. PostgreSQL accepte en revanche une fonction IMMUTABLE dont le corps en
# contient — d'où ce choix, et le `ORDER BY` de l'agrégat : sans lui, l'ordre des
# feuilles dépendrait du plan, et la fonction ne serait immuable que de nom.
#
# ⚠️ **Le corps est FIGÉ, le nom est VERSIONNÉ.** Deux index d'expression en dépendent
# (`db/search.py::INDEX_VALEURS`). Changer le corps sous le même nom laisserait ces
# index décrire l'ANCIENNE règle — PostgreSQL ne le détecte pas, et une ligne serait
# trouvée ou non selon que le plan passe par l'index. Changer la règle, c'est donc
# une `_v2`, ses propres index, et le retrait des anciens. C'est aussi pourquoi le
# démarrage ne la pose que si elle MANQUE (`CREATE`, jamais `CREATE OR REPLACE`).
#
# ⚠️ **`SET jit = off`, et c'est mesuré** (PostgreSQL 17, JIT actif par défaut) : le
# planificateur estime le corps à plus de `jit_above_cost` (ses fonctions de
# déroulement valent 100 lignes chacune, imbriquées), et la requête d'une fonction SQL
# se recompile À CHAQUE APPEL — 200 ms par ligne, quelle que soit sa taille. Sans JIT :
# 45 µs par ligne réaliste (2 000 lignes en 93 ms, contre 6 ms pour `data::text`).
# Laissé tel quel, chaque écriture de ligne payait 2 × 200 ms de maintenance d'index,
# et la construction d'un index sur 5 500 lignes ne finissait pas en dix minutes.
#
# Le corps n'appelle que des fonctions de `pg_catalog` : il se résout sous le
# `search_path` restreint que PostgreSQL 17 impose aux opérations de maintenance
# (`CREATE INDEX`, `REINDEX`, `VACUUM`).
FONCTION_VALEURS_TEXTE = "datastore_valeurs_texte_v1"
ROW_SERVED_VALUES_TEXT_SQL = f"{FONCTION_VALEURS_TEXTE}(data)"

# La règle de lecture rendue en JSONB plutôt qu'en texte : une liste de fiches doit
# rester une liste pour descendre dans ses items. DÉRIVÉE du texte de la règle, jamais
# recopiée — la seule différence est l'opérateur qui lit `valeur`.
_REGLE_VALEUR_JSONB = _REGLE_VALEUR.replace("{c}->>{v}", "{c}->{v}")
assert _REGLE_VALEUR_JSONB != _REGLE_VALEUR, "la règle ne lit plus `valeur` par ->>"


def _valeur_jsonb(cellule: str) -> str:
    return _REGLE_VALEUR_JSONB.format(c=cellule, f=cellule, v=f"'{VALUE_LAYER}'",
                                      vide=_OBJET_VIDE_SQL, couches=_COUCHES_SQL)


_FEUILLES = ("strict $.** ? (@.type() == \"string\" || @.type() == \"number\""
             " || @.type() == \"boolean\")")

DDL_FONCTION_VALEURS_TEXTE = (
    f"CREATE FUNCTION {FONCTION_VALEURS_TEXTE}(data jsonb) RETURNS text "
    "LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET jit = off AS $corps$ "
    "SELECT coalesce(string_agg(f.feuille #>> '{}', ' ' "
    "ORDER BY c.n, m.i, m.a, f.n), '') "
    "FROM jsonb_each(CASE WHEN jsonb_typeof(data) = 'object' THEN data END) "
    "WITH ORDINALITY AS c(cle, cellule, n) "
    f"CROSS JOIN LATERAL (SELECT {_valeur_jsonb('c.cellule')} AS v) AS u "
    "CROSS JOIN LATERAL ("
    # la case elle-même, quand sa valeur n'est pas une liste
    "SELECT 0::bigint AS i, 0::bigint AS a, u.v AS piece "
    "WHERE jsonb_typeof(u.v) IS DISTINCT FROM 'array' "
    # les éléments d'une liste de valeurs
    "UNION ALL SELECT e.i, 0::bigint, e.item "
    "FROM jsonb_array_elements(CASE WHEN jsonb_typeof(u.v) = 'array' THEN u.v END) "
    "WITH ORDINALITY AS e(item, i) WHERE jsonb_typeof(e.item) <> 'object' "
    # les attributs des fiches d'une liste, déballés par la même règle
    f"UNION ALL SELECT e.i, t.a, {_valeur_jsonb('t.attribut')} "
    "FROM jsonb_array_elements(CASE WHEN jsonb_typeof(u.v) = 'array' THEN u.v END) "
    "WITH ORDINALITY AS e(item, i) "
    "CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(e.item) = 'object' "
    "THEN e.item END) WITH ORDINALITY AS t(cle, attribut, a)"
    ") AS m "
    f"CROSS JOIN LATERAL jsonb_path_query(m.piece, '{_FEUILLES}') "
    "WITH ORDINALITY AS f(feuille, n) $corps$"
)

#: `None` tant que la fonction n'existe pas — la garde du démarrage et de la révision.
SQL_FONCTION_VALEURS_PRESENTE = (
    f"SELECT to_regprocedure('{FONCTION_VALEURS_TEXTE}(jsonb)') IS NOT NULL")


# `split_layer` vit dans `datastore_schema` depuis #377 et n'est que RÉ-EXPORTÉE ici
# (les appelants la connaissent sous `db.split_layer`). Elle a déménagé parce que la
# validation de schéma en a besoin, et que ce module importe déjà `datastore_schema` :
# la garder ici en aurait fait une seconde copie, et une grammaire de chemin en deux
# exemplaires est exactement ce que ce module existe pour empêcher.


_LIST_PATH_RE = re.compile(r"^(?P<col>[^\[\]]+)\[(?P<rang>\d*)\]\.(?P<reste>.+)$")
# L'élément NU (`tags[]`, `tags[0]`, oto#102) : le nom de colonne y suit la règle de
# l'écriture par rang (`rangs._ADRESSE` — ni espace, ni point), sans quoi une colonne
# nommée `Note [1]` deviendrait un chemin.
_LIST_ELEMENT_RE = re.compile(r"^(?P<col>[^\s\[\].]+)\[(?P<rang>\d*)\]$")


def split_list_path(field: str):
    """`contacts[].email` → `("contacts", None, "email")` ; `contacts[0].email.origine`
    → `("contacts", 0, "email.origine")` ; `tags[]` → `("tags", None, None)`, l'élément
    LUI-MÊME d'une liste de valeurs (oto#102) ; autre chose → None.

    Deux formes, deux usages : le rang VIDE interroge la liste entière (« il existe un
    contact dont… »), un rang NOMMÉ vise une fiche précise — c'est ce dont la
    projection d'une migration a besoin pour résoudre un ancien nom plat."""
    m = _LIST_PATH_RE.match(str(field)) or _LIST_ELEMENT_RE.match(str(field))
    if not m:
        return None
    rang = m.group("rang")
    return m.group("col"), (int(rang) if rang else None), m.groupdict().get("reste")


def leaf_read_sql(base_sql: str, base_params: list, field: str) -> tuple:
    """La lecture d'une FEUILLE sous une base quelconque — `data` au premier niveau,
    l'item courant sous une liste. Une seule expression, deux contextes : c'est le
    principe de la feuille rendu littéral, plutôt que deux SQL à garder d'accord.

    `field=None` lit la base ELLE-MÊME — l'élément d'une liste de valeurs (`tags[]`,
    oto#102) —, par la même règle qu'une case."""
    if field is None:
        return (_regle_texte(f"({base_sql})", f"({base_sql} #>> '{{}}')"),
                list(base_params) * _LECTURES)
    base, layer = split_layer(field)
    if layer:
        return f"{base_sql}->%s->>%s", base_params + [base, layer]
    # Chaque lecture de la case réécrit la base PUIS le champ : la liste se répète
    # autant de fois que la règle lit la case, dans l'ordre même du texte rendu.
    return (_regle_texte(f"({base_sql}->%s)", f"{base_sql}->>%s"),
            (list(base_params) + [base]) * _LECTURES)


def list_items_sql(colonne: str, alias: str) -> tuple:
    """`(fragment, params)` qui déroule les éléments de la colonne-liste `colonne`,
    un par ligne SQL, sous `alias(v)` — le filtre d'existence (`EXISTS (SELECT 1 FROM
    …)`) et l'agrégat par occurrence (`LATERAL …`) lisent la liste par ce seul chemin.

    La garde de type est OBLIGATOIRE : `jsonb_array_elements` LÈVE sur une valeur qui
    n'est pas un tableau, et pendant une conversion une partie des lignes ne l'est pas
    encore — l'état NORMAL, pas un cas limite. Une telle ligne n'a aucun élément."""
    return (f"jsonb_array_elements(CASE WHEN jsonb_typeof(data->%s) = 'array' "
            f"THEN data->%s ELSE '[]'::jsonb END) AS {alias}(v)",
            [colonne, colonne])


def field_read_sql(field: str) -> tuple:
    """`(fragment SQL, paramètres)` pour lire ce que l'appelant a désigné.

    Un nom nu lit la VALEUR (plate ou à couches) ; `champ.source` lit la couche ;
    `contacts[0].email` lit l'attribut d'une fiche de rang précis. **Les trois se
    filtrent, se trient et s'agrègent pareil** — c'est ici que l'uniformité des verbes
    se joue, et on a déjà payé de la perdre : le même filtre répondait juste sur un
    verbe et faux sur trois, parce que la résolution était recopiée ailleurs.

    `contacts[].email` (TOUS les items) n'a pas sa place ici : il ne désigne pas UNE
    valeur par ligne mais N. Le filtre (existence) et l'agrégat (occurrences) le lisent
    élément par élément (`list_items_sql`) ; le TRI, lui, n'a rien à ranger. Refusé en
    le nommant plutôt que rendu comme s'il valait le premier item — un ordre
    reproductible et faux."""
    chemin = split_list_path(field)
    if chemin is not None:
        colonne, rang, reste = chemin
        if rang is None:
            raise ValueError(
                f"`{field}` désigne TOUS les items de `{colonne}` : il n'a pas une "
                f"valeur par ligne mais N, donc il ne se trie pas et ne se met pas en "
                f"commun avec d'autres colonnes. Il se FILTRE (par existence) et "
                f"s'AGRÈGE seul (`group_by: \"{field}\"` compte une occurrence par "
                f"item). Pour une valeur par ligne, viser un rang précis — "
                f"`{colonne}[0]{'.' + reste if reste else ''}`.")
        # Le rang vient d'un `\d+` converti en entier : l'inscrire dans le SQL n'est
        # pas une interpolation de saisie.
        return leaf_read_sql(f"data->%s->{int(rang)}", [colonne], reste)
    base, layer = split_layer(field)
    if layer:
        # Le nom COMPLET en troisième paramètre : c'est la relique littérale.
        return LAYER_VALUE_PARAM_SQL, [base, layer, field]
    return FIELD_VALUE_PARAM_SQL, [base] * _LECTURES


def bkey_index_expr(key: str) -> str:
    """Expression de la clé métier : celle de l'index d'unicité, du lookup, des groupes
    de doublons et de leur fusion — tous la prennent ici (oto#223).

    **C'est la règle de lecture, `field_value_sql`, et rien d'autre.** La valeur qui
    fait foi pour la clé est celle que SERT la lecture : une case faite de couches
    seules, ou `{"valeur": null, …}`, ne sert aucune valeur, donc n'entre pas dans
    l'index (partiel sur `IS NOT NULL`) et ne fait doublon avec rien ; `"123"` et
    `{"valeur": "123", "comment": "c"}` servent la même valeur, donc sont la même clé
    pour les quatre chemins. Jusqu'à oto#223, l'index comparait le texte V1
    (`COALESCE(data->'k'->>'valeur', data->>'k')`) et les doublons le texte brut
    `data->>'k'` : trois règles pour une clé.

    ⚠️ **Le texte est celui d'index d'EXPRESSION déjà construits en base**, partagée
    entre préprod et prod, et le lookup ne sert l'index qu'à expression identique. Il
    dépend de `LAYER_KEYS` (via la règle) : changer la règle ou le vocabulaire des
    couches change ce texte, et chaque lookup partirait en parcours séquentiel sans
    une erreur. Un tel changement exige une migration qui reconstruit les
    `ds_bkey_<ns>` (modèle : `0029_cle_metier_valeur_servie`). Le banc
    `test_cle_metier_index_223.py` fige la définition enregistrée par PostgreSQL et
    le plan du vrai lookup."""
    return field_value_sql(key)
