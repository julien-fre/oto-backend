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
