"""Les noms SERVIS qu'on remplace — et LA date à laquelle l'ancien s'en va (#519).

Le produit a changé de mot (#519) : il dit **guide** (ADR 0042, le guide =
primitive unique d'instruction) et **procédure** pour ce qui s'exécute. Le lot A a
retiré l'ancien de l'interne sans changer un octet servi. Le lot B renomme les
SURFACES — et une surface ne se renomme pas, elle se DOUBLE : le nouveau nom naît à
côté de l'ancien, l'ancien continue de répondre, et la date de son retrait est
écrite là où le consommateur la lit.

**Ce module est cette date.** Elle vit ici et nulle part ailleurs ; chaque avis de
dépréciation servi la recopie depuis `RETRAIT`. Décaler le retrait est alors un
geste — changer cette constante — et non une chasse aux chaînes de caractères dans
quarante descriptions, dont on oublierait trois.

Pourquoi un TAG et pas une date de merge (`RETRAIT` se lit « premier tag `vX.Y.Z`
posé à partir de cette date ») : `main` est la PREPROD. Un alias retiré au merge
serait retiré du serveur que les intégrateurs sondent, 30 jours de préavis annoncés
et zéro jour servi. Le retrait est le lot D — issue #526, qui porte la liste
complète de ce que la date emporte.

⚠️ **Ce module n'est pas un fourre-tout de compatibilité.** Il ne porte que des
renommages de vocabulaire à durée de vie FINIE, chacun avec sa contrepartie dans
#526. Un alias sans date de retrait est un second nom permanent : ça se décide, ça
ne s'ajoute pas ici.
"""
from __future__ import annotations

import datetime
from typing import NamedTuple
from urllib.parse import quote

# Premier tag `vX.Y.Z` posé à partir de cette date (décision du 28/08/2026 + 30 j).
RETRAIT = datetime.date(2026, 9, 27)

# ── Outils MCP (lot B1) ─────────────────────────────────────────────────────
# ancien nom SERVI → nom canonique. L'ancien reste listé et appelable jusqu'au
# retrait ; le bord du protocole (`middleware/alias.ToolAliasMiddleware`) rétablit
# le canonique AVANT que quoi que ce soit d'autre ne le lise, exactement comme pour
# le renommage par tenant — donc rien en aval n'apprend que l'alias existe : les
# gates, la denylist de visibilité, le journal `tool_calls` et les références
# `<tool:slug>` continuent de voir un seul nom pour un seul outil.
TOOLS: dict = {
    "oto_admin_doctrine": "oto_admin_guide",
}


def date_de_retrait() -> str:
    """La date de retrait telle qu'elle est SERVIE (JJ/MM/AAAA)."""
    return RETRAIT.strftime("%d/%m/%Y")


def avis(canonique: str) -> str:
    """L'avis qui PRÉFIXE la description d'un nom déprécié.

    En tête, pas en queue : beaucoup de clients tronquent une description longue,
    et un avis de dépréciation lu après 400 caractères n'a averti personne. C'est
    aussi la première chose que le modèle lit quand il choisit son outil.
    """
    return (f"Déprécié : utilisez `{canonique}` (retrait le {date_de_retrait()}). ")


def tool_canonique(nom: str) -> str:
    """Le nom que le SERVEUR connaît. Un nom non déprécié passe inchangé."""
    return TOOLS.get(nom, nom)


def tools_deprecies_de(canonique: str) -> tuple:
    """Les anciens noms d'un outil, à servir à côté du sien. Vide si aucun."""
    return tuple(sorted(a for a, c in TOOLS.items() if c == canonique))


# ── Chemins REST (lot B2) ───────────────────────────────────────────────────
class AliasRest(NamedTuple):
    """Un ancien chemin REST, monté en **308** vers son chemin d'aujourd'hui.

`ancien` et `nouveau` s'écrivent chacun avec SES propres placeholders — ceux de
    la route réellement montée de chaque côté, pour que le chemin publié dans
    `/openapi.json` et dans la doc soit celui qu'on lit dans la table de routes.
    `params` porte l'écart quand un placeholder change de nom en route ; seule la
    VALEUR capturée voyage.
    """
    verbe: str
    ancien: str
    nouveau: str
    # placeholder de `ancien` → placeholder de `nouveau`, quand ils diffèrent.
    # Jamais muté (une valeur par défaut de NamedTuple est partagée).
    params: dict = {}
    # L'`operationId` HISTORIQUE de ce chemin, quand plus personne ne le réclame.
    #
    # ⚠️ L'`operationId` suit la CAPACITÉ, pas le chemin — c'est le nom de méthode
    # qu'un client généré s'est donné pour une opération. Quand la clé de capacité ne
    # change pas (`library.list`), le NOUVEAU chemin hérite de l'id : regénérer le
    # client ne renomme rien, seule l'URL bouge. C'est le bon résultat, et ça
    # interdit de donner le même id à l'entrée dépréciée (un `operationId` est unique
    # dans un document OpenAPI — garde-fou `test_openapi.py`). Elle en reçoit alors un
    # dérivé de son chemin, laissé à `""` ici.
    #
    # Un seul cas le renseigne : la clé a changé AUSSI (`org.doctrine.get` →
    # `org.guide.get`), l'id historique n'est plus réclamé par personne, et le garder
    # sur l'ancien chemin laisse un client déjà généré retrouver sa méthode.
    operation_id: str = ""


REST: tuple = (
    # Bibliothèque publique de guides (marketplace) — servie sans auth, consommée
    # par le build de la vitrine et par un `fetch` de navigateur.
    AliasRest("GET", "/api/doctrines/library", "/api/guide-library"),
    AliasRest("GET", "/api/doctrines/library/{slug}", "/api/guide-library/{slug}"),
    # ⚠️ ORDRE — `library` AVANT `{doctrine_id}` : le second capture un segment, et
    # servirait `library` comme un identifiant. C'est exactement ce que faisait la
    # table d'avant ce lot (le chemin `…/doctrines/library` y était inatteignable).
    AliasRest("GET", "/api/me/doctrines/library", "/api/me/guide-library"),
    AliasRest("GET", "/api/me/doctrines/library/{slug}",
              "/api/me/guide-library/{slug}"),
    AliasRest("DELETE", "/api/me/doctrines/library/{id}",
              "/api/me/guide-library/{id}"),
    AliasRest("POST", "/api/me/doctrines/publish", "/api/me/guide-library/publish"),
    AliasRest("POST", "/api/me/doctrines/fork", "/api/me/guide-library/fork"),
    AliasRest("GET", "/api/me/doctrines/{doctrine_id}", "/api/me/guides/{guide_id}",
              {"doctrine_id": "guide_id"}, "org_doctrine_get_get"),
)


def cible(alias: AliasRest, path_params: dict, query: str = "") -> str:
    """Le chemin de destination d'un alias, params de chemin injectés.

    La query string est REPORTÉE telle quelle : la vitrine appelle
    `…/library?limit=200`, et un 308 qui la perdrait rendrait 100 entrées au lieu
    de 200 — une régression qu'aucun code d'erreur ne signale.
    """
    chemin = alias.nouveau
    for nom, valeur in (path_params or {}).items():
        cible_nom = alias.params.get(nom, nom)
        chemin = chemin.replace("{" + cible_nom + "}", quote(str(valeur), safe=""))
    return f"{chemin}?{query}" if query else chemin


# ── Clés de capacité (lot B2) ───────────────────────────────────────────────
# ancienne clé → clé d'aujourd'hui. ⚠️ **Renommées SANS alias**, et c'est un choix :
# une clé de capacité ne sort du serveur qu'à deux endroits — `/api/admin/capabilities`
# (le navigateur d'objets de la plateforme, réservé à l'admin plateforme, sans
# intégrateur tiers) et l'`operationId` de `/openapi.json`. Ce second est le seul qui
# engage quelqu'un dehors, et il est préservé : l'entrée DÉPRÉCIÉE du chemin d'avant
# le porte (`AliasRest.operation_id`). Il n'y a donc rien à aliaser.
CAPACITES: dict = {
    "org.doctrine.get": "org.guide.get",
    "org.doctrine.admin_get": "org.guide.admin_get",
    "org.doctrine.admin_list": "org.guide.admin_list",
    "admin.doctrine": "admin.guide",
}
