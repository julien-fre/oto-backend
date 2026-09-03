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

**Le cas nominal — un identifiant courant, sans alias — coûte exactement ce qu'il
coûtait : UNE requête, UN accès index sur `sub_aliases_pkey`, et zéro accès à
`users`.** L'ancrage de la récursion est le `SELECT … WHERE old_sub = %(sub)s` de
l'ancienne version ; s'il ne rend rien, la partie récursive ne tourne pas et le test
d'existence du compte, corrélé au bout de chaîne, n'est jamais exécuté. C'est mesuré
(`tests/test_resolve_sub_chaine_live.py`), pas affirmé.

## Et si un alias bouclait ?

Rien n'interdit `A → B` puis `B → A` : `sub_aliases` contraint `old_sub` (clé
primaire), jamais `new_sub`. Une résolution qui tourne en rond en tête de chaque
requête gèlerait le service — la production a déjà gelé 13 minutes pour une raison
voisine (2026-07-02, `docs/event-loop-perf.md`). **Deux freins
indépendants** : la récursion transporte le chemin déjà parcouru et s'arrête net dès
qu'un maillon s'y répète ; et elle est bornée en profondeur, quoi qu'il arrive. Les
deux se prouvent séparément (retirer l'un laisse l'autre rouge).
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

    `motif` dit lequel des trois cas :
    - `compte_disparu` — la chaîne aboutit à un identifiant sans ligne `users` ;
    - `cycle` — un maillon se répète, la chaîne ne se termine pas ;
    - `chaine_trop_longue` — la borne `MAX_SAUTS` est atteinte sans terminaison.
    """

    def __init__(self, sub: str, motif: str, detail: str):
        self.sub, self.motif = sub, motif
        super().__init__(
            f"identifiant {sub} non résolvable ({motif}) : {detail} — la requête est "
            "refusée plutôt que servie sous un compte qui n'existe plus")


# La chaîne d'alias en UNE requête, bornée et protégée du cycle.
#
# - l'ANCRE est mot pour mot la requête d'avant (`old_sub = %(sub)s`, accès par la
#   clé primaire) : sans alias, elle ne rend rien et tout le reste est inerte —
#   c'est ce qui tient la promesse « le cas nominal ne coûte rien de plus » ;
# - `chemin` transporte les maillons déjà vus ; `boucle` s'allume dès qu'un maillon
#   s'y répète, et la clause `NOT c.boucle` arrête la récursion à ce moment-là (on
#   garde la ligne fautive pour pouvoir NOMMER le cycle plutôt que le taire) ;
# - `c.profondeur <= %(max)s` borne la descente quoi qu'il arrive. La borne laisse
#   produire un maillon de PLUS que `MAX_SAUTS` : c'est ce qui rend « trop longue »
#   distinguable de « terminée pile à la borne », qu'on ne pourrait pas trancher
#   sinon ;
# - le test d'existence du compte est corrélé au SEUL bout de chaîne (`bout`), donc
#   il ne touche `users` que s'il y a eu au moins un alias.
_CHAINE_SQL = """
WITH RECURSIVE chaine(sub, profondeur, chemin, boucle) AS (
        SELECT a.new_sub, 1, ARRAY[a.old_sub, a.new_sub], false
          FROM sub_aliases a
         WHERE a.old_sub = %(sub)s
    UNION ALL
        SELECT n.new_sub, c.profondeur + 1, c.chemin || n.new_sub,
               n.new_sub = ANY(c.chemin)
          FROM chaine c
          JOIN sub_aliases n ON n.old_sub = c.sub
         WHERE NOT c.boucle AND c.profondeur <= %(max)s
), bout AS (
    SELECT * FROM chaine ORDER BY profondeur DESC LIMIT 1
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
        row = conn.execute(_CHAINE_SQL, {"sub": sub, "max": MAX_SAUTS}).fetchone()
    if not row:
        return sub  # cas nominal : aucun alias ne nomme ce sub
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
