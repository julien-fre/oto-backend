"""Drain d'alias : traduire un identifiant de compte ANCIEN en compte ACTUEL.

`sub_aliases` est le produit du merge de comptes (`users.migrate_sub`, ADR 0052) :
une ligne `old_sub → new_sub` par bascule. Le drain la relit en tête de CHAQUE
requête servie (REST `api/base._authenticate`, MCP `auth/hooks`), gaté par
`tenant_migration.alias_drain_armed`.

## Pourquoi un saut ne suffit pas

Une personne peut être rapprochée plusieurs fois : la table porte alors une CHAÎNE
`A → B → C → D`, une ligne par bascule. Un drain qui ne fait qu'un saut rend `B` à
qui présente `A` — or `B` a été supprimé par la bascule suivante. Et l'erreur n'est
pas propre : la porte REST appelle `upsert_user` juste après le drain, **hors de
toute commande**, donc l'identifiant mort est **RECRÉÉ**. Le compte fantôme qui en
naît n'a ni org, ni coffre, ni historique — et rien ne le signale.

Mesuré en production le 2026-09-03, sur 23 alias : **une chaîne de 3 maillons**
(bascules du 28/07, 03/08 et 13/08) et **une de 2** — la même personne. Le maillon
supprimé le 13/08 a été **recréé le 16/08** par ce chemin, puis a servi **884
appels** sous une identité morte ; un maillon intermédiaire ne portait déjà plus
aucune ligne `users`, donc le drain à un saut y aboutissait **nulle part**.

## Les deux refus, et pourquoi ce sont des refus

« Je ne sais pas qui tu es » et « tu es celui-ci » ne sont pas la même réponse
(`docs/silences-2026-08-27.md`, site B5). Quand la chaîne n'aboutit pas à un compte
vivant, ou qu'elle ne se termine pas, la seule sortie sûre est de **lever** :
rendre l'identifiant d'entrée servirait la requête sous le compte d'AVANT bascule,
et rendre un maillon mort fabriquerait le fantôme qu'on ferme ici.

## Ce que ça coûte sur le chemin chaud

Ce code est en tête de CHAQUE requête servie. **Le cas nominal — un identifiant
courant, qu'aucun alias ne nomme — exécute la requête d'AVANT, mot pour mot, et
s'arrête là.** Pas une requête de plus, pas un buffer de plus, pas un accès à `users`.

⚠️ **Ce n'est pas ce que faisait la première version de ce module**, et c'est le
plan d'exécution qui l'a dit, pas la relecture. Elle ancrait la récursion sur la
requête d'avant et laissait le reste inerte — raisonnement juste, mesure fausse :
sur la production (`EXPLAIN (ANALYZE, BUFFERS)`, 2026-09-03, sub sans alias), la
jointure de hachage du terme récursif construit son côté interne AVANT de sonder
une table de travail vide, donc `sub_aliases` était lue DEUX fois — 5 buffers et
0,149 ms contre 1 buffer et 0,012 ms. Le test d'existence du compte, lui, était
bien `never executed`. Rien de dramatique en absolu ; mais « rien de plus » était
faux, et il ne se voyait ni dans le code ni sur la base de test (dont le plan,
minuscule, est différent).

D'où la forme retenue : le saut simple d'abord, la récursion **seulement** s'il y a
un alias. Le prix se paie là où il y a quelque chose à résoudre — un chemin
emprunté par un identifiant sur les vingt-trois que porte la table — et nulle part
ailleurs. Les deux requêtes partagent la même connexion (pas d'aller-retour de pool
en plus), et c'est mesuré au plan d'exécution dans
`tests/test_resolve_sub_chaine_live.py`, pas affirmé.

## Et si un alias bouclait ?

Rien n'interdit `A → B` puis `B → A` : `sub_aliases` contraint `old_sub` (clé
primaire), jamais `new_sub`. Une résolution qui tourne en rond en tête de chaque
requête gèlerait le service — la production a déjà gelé 13 minutes pour une raison
voisine (2026-07-02, `docs/event-loop-perf.md`). **Deux freins
indépendants** : la clause `CYCLE` de PostgreSQL arrête la récursion dès qu'un maillon
déjà parcouru se représente — c'est la forme qu'un cliquet exige de toute récursion de
`db/`, et il a rougi sur la 1re version de ce module, qui portait le chemin à la main ;
et la descente est bornée en profondeur, quoi qu'il arrive, ce que `CYCLE` ne fait pas
(une chaîne acyclique absurdement longue se déroulerait sans elle). Retirer les DEUX
fait tourner la récursion jusqu'à ce que le serveur la coupe — c'est ce qui prouve
qu'aucun des deux n'est décoratif.
"""
from __future__ import annotations

import logging

from ._conn import _connect

logger = logging.getLogger(__name__)

# Borne de sûreté de la récursion. Le plus long enchaînement observé en production
# est de 3 maillons ; au-delà de cette borne on refuse au lieu de continuer, parce
# qu'une chaîne aussi longue ne décrit plus une personne rapprochée plusieurs fois
# mais une table qu'on ne comprend plus. La détection de cycle est un frein
# SÉPARÉ — celle-ci attrape aussi ce qu'elle ne verrait pas (une chaîne acyclique
# absurdement longue, un `sub_aliases` corrompu).
MAX_SAUTS = 16


class AliasNonResolvable(RuntimeError):
    """L'identifiant présenté ne se traduit pas en compte actuel.

    ⚠️ **Ce refus est ce qui empêche la fabrication d'un compte fantôme.** Les deux
    portes servies appellent `upsert_user` juste après le drain : rendre un
    identifiant qui ne désigne plus rien fait NAÎTRE la ligne `users` que la
    bascule avait supprimée, sans org, sans coffre, sans historique — et sans une
    ligne de trace. Vécu : 884 appels servis sous une identité ressuscitée.

    `motif` dit lequel des cas :
    - `compte_disparu` — la chaîne aboutit à un identifiant sans ligne `users` ;
    - `cycle` — un maillon se répète, la chaîne ne se termine pas ;
    - `chaine_trop_longue` — la borne `MAX_SAUTS` est atteinte sans terminaison ;
    - `alias_evanoui` — l'alias vu au premier saut a disparu au second (garde de
      cohérence entre les deux lectures ; aucun chemin ne l'écrit aujourd'hui).
    """

    def __init__(self, sub: str, motif: str, detail: str):
        self.sub, self.motif = sub, motif
        super().__init__(
            f"identifiant {sub} non résolvable ({motif}) : {detail} — la requête est "
            "refusée plutôt que servie sous un compte qui n'existe plus")


# LE CHEMIN NOMINAL, et rien d'autre : la requête d'avant le 2026-09-03, à l'octet
# près. Tout le trafic passe par elle et s'arrête là. Elle est une constante parce
# qu'un test la compare à ce qui s'exécute réellement — la promesse « rien de plus
# qu'avant » ne vaut que si « avant » est écrit quelque part.
_UN_SAUT_SQL = "SELECT new_sub FROM sub_aliases WHERE old_sub=%s"


# La chaîne d'alias en UNE requête, bornée et protégée du cycle. N'est exécutée que
# lorsque le saut simple a trouvé un alias.
#
# - **la clause `CYCLE` de PostgreSQL** arrête la récursion dès qu'un maillon déjà
#   parcouru se représente, et allume `boucle` sur la ligne fautive — qu'elle rend,
#   ce qui permet de NOMMER le cycle plutôt que de le taire. C'est la forme qu'un
#   cliquet exige de toute récursion de `db/`
#   (`tests/test_node_parent_cycle.py::test_aucune_recursion_sur_l_arbre_n_est_SANS_BORNE`),
#   et il a rougi sur la 1re version de ce module, qui portait le chemin à la main ;
# - `c.profondeur <= %(max)s` borne la descente quoi qu'il arrive — SECOND frein,
#   indépendant du premier (une chaîne acyclique absurdement longue ne déclenche pas
#   la clause `CYCLE`). La borne laisse produire un maillon de PLUS que `MAX_SAUTS` :
#   c'est ce qui rend « trop longue » distinguable de « terminée pile à la borne »,
#   qu'on ne pourrait pas trancher sinon ;
# - le test d'existence du compte est corrélé au SEUL bout de chaîne (`bout`), donc
#   il ne touche `users` que s'il y a eu au moins un alias.
#
# ⚠️ `bout` nomme ses colonnes une par une, et ce n'est pas du zèle : `SELECT * FROM
# chaine` dans une CTE imbriquée ne rapporte PAS les colonnes que la clause `CYCLE`
# ajoute (`boucle`, `chemin`) — l'expansion de l'étoile a lieu avant leur greffe.
# Mesuré sur PostgreSQL 17 : `column b.boucle does not exist`, alors que le même
# `SELECT *` posé directement sur la CTE récursive les rend toutes les deux.
_CHAINE_SQL = """
WITH RECURSIVE chaine AS (
        SELECT a.new_sub AS sub, 1 AS profondeur
          FROM sub_aliases a
         WHERE a.old_sub = %(sub)s
    UNION ALL
        SELECT n.new_sub, c.profondeur + 1
          FROM chaine c
          JOIN sub_aliases n ON n.old_sub = c.sub
         WHERE c.profondeur <= %(max)s
) CYCLE sub SET boucle USING chemin
, bout AS (
    SELECT sub, profondeur, boucle, chemin FROM chaine ORDER BY profondeur DESC LIMIT 1
)
SELECT b.sub AS canonique, b.profondeur, b.boucle, array_length(b.chemin, 1) AS maillons,
       EXISTS (SELECT 1 FROM users u WHERE u.sub = b.sub) AS compte_vivant
  FROM bout b
"""


def resolve_sub(sub: str) -> str:
    """Canonicalise un sub en suivant la chaîne d'alias JUSQU'AU BOUT.

    Rend le sub inchangé si aucun alias ne le nomme (cas normal, et cas dominant).

    ⚠️ **Ne rattrape RIEN.** Rendre le sub d'entrée sur un hoquet DB, c'est servir la
    requête sous le compte d'AVANT bascule — coffre, org, projets — et sans une ligne
    de trace (`docs/silences-2026-08-27.md`, site B5). Rendre un maillon intermédiaire
    est pire encore : `upsert_user`, appelé juste après par les deux portes, RECRÉE
    l'identifiant mort. Les deux se lèvent ; aucun ne se devine.
    """
    if not sub:
        return sub
    with _connect() as conn:
        # 1. Le saut simple — la requête d'avant, mot pour mot. Aucun alias : on
        #    s'arrête ici, et le chemin chaud n'a rien payé de plus qu'hier.
        if conn.execute(_UN_SAUT_SQL, (sub,)).fetchone() is None:
            return sub
        # 2. Il y a un alias : dérouler la chaîne jusqu'au bout, sur la MÊME
        #    connexion. Le premier saut est refait — c'est le prix, minuscule et
        #    payé seulement là où il y a quelque chose à résoudre.
        row = conn.execute(_CHAINE_SQL, {"sub": sub, "max": MAX_SAUTS}).fetchone()
    if not row:
        # L'alias a disparu entre les deux lectures. Impossible aujourd'hui (rien ne
        # supprime de `sub_aliases`), mais le taire rendrait le sub d'entrée — donc
        # servirait la requête sous le compte d'AVANT bascule, ce que ce module
        # existe pour empêcher.
        raise AliasNonResolvable(
            sub, "alias_evanoui",
            "un alias nommait ce sub à la lecture précédente et n'y est plus")
    # L'ORDRE compte : dans un cycle, le « bout » de chaîne est arbitraire — juger
    # son existence avant d'avoir écarté le cycle donnerait un verdict au hasard.
    if row["boucle"]:
        logger.error(
            "drain d'alias : cycle dans sub_aliases à partir de %s (%s maillons) — "
            "la chaîne ne se termine pas, la requête est refusée", sub, row["maillons"])
        raise AliasNonResolvable(
            sub, "cycle",
            f"un maillon se répète après {row['maillons']} sauts dans sub_aliases")
    if row["profondeur"] > MAX_SAUTS:
        logger.error(
            "drain d'alias : chaîne de plus de %s sauts à partir de %s — refusée",
            MAX_SAUTS, sub)
        raise AliasNonResolvable(
            sub, "chaine_trop_longue",
            f"la chaîne dépasse {MAX_SAUTS} sauts sans aboutir")
    if not row["compte_vivant"]:
        logger.error(
            "drain d'alias : la chaîne partie de %s aboutit à un identifiant sans "
            "ligne users après %s saut(s) — refusée plutôt que recréée",
            sub, row["profondeur"])
        raise AliasNonResolvable(
            sub, "compte_disparu",
            f"le compte visé après {row['profondeur']} saut(s) n'existe plus")
    return row["canonique"]
