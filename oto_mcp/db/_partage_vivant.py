"""Le prédicat « partage vivant » de `resource_grants` (otomata-tech/oto#39).

Un partage peut porter une échéance (`expires_at`, NULL = sans échéance). Passée, il
ne donne plus rien : ni accès, ni visibilité, ni prêt de clés. Le refus ne vit donc
pas dans un contrôle d'accès parmi d'autres mais dans CHAQUE lecture de la table —
un seul `SELECT` qui l'oublie rouvre la ressource à qui l'on croyait l'avoir fermée.

Une seule écriture du prédicat, ici ; `tests/test_partages_echeance_39.py` refuse une
lecture de `resource_grants` qui ne le porte pas, sauf exemption motivée.
"""
from __future__ import annotations


def partage_vivant(alias: str = "") -> str:
    """`(expires_at IS NULL OR expires_at > NOW())`, qualifié par `alias` s'il y en a un."""
    col = f"{alias}.expires_at" if alias else "expires_at"
    return f"({col} IS NULL OR {col} > NOW())"


#: La table lue sans alias.
PARTAGE_VIVANT = partage_vivant()
#: La table lue sous l'alias `g`, la convention de toutes les jointures.
PARTAGE_VIVANT_G = partage_vivant("g")
