"""La consigne d'une campagne, telle que le backend la COMMANDE au worker.

Le worker ne compose rien : il reçoit un texte et le sert à l'agent. C'est donc
ici, et nulle part ailleurs sur le chemin hébergé, que les marqueurs d'une
consigne déclarée deviennent des valeurs.

⚠️ Les marqueurs sont ceux que le runner remplit déjà en mode direct
(`oto_runner/declaration.py`), et ils doivent le rester à l'identique : une même
procédure déclarée en local ou en campagne doit recevoir le même texte. Le
12/09/2026, `{date_du_jour}` n'était rempli QUE par le mode direct — les six
passes d'une chaîne d'enrichissement l'emploient, et une campagne hébergée les
aurait servies avec le marqueur en littéral, sans aucune erreur.

⚠️ Le FORMAT de `{date_du_jour}` n'est plus celui du mode direct : le chemin hébergé
sert la date ISO (`YYYY-MM-DD`) — un texte lu par un agent hébergé s'écrit en anglais
et sans format de date français (docs/conventions.md). Le mode direct d'oto-runner
écrit encore `%d/%m/%Y` : l'aligner est l'affaire de ce dépôt-là.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

#: Le jour s'entend à Paris, pas à l'heure du serveur (UTC) : entre minuit et
#: deux heures, une consigne datée « d'hier » partirait dans une fiche.
FUSEAU = ZoneInfo("Europe/Paris")
#: ISO 8601 — sans ambiguïté jour/mois, quelle que soit la langue de l'agent.
FORMAT_DATE = "%Y-%m-%d"


def composer(campagne: dict, maintenant: Optional[datetime] = None) -> str:
    """Le texte servi à l'agent pour un travail de cette campagne."""
    jour = (maintenant or datetime.now(FUSEAU)).astimezone(FUSEAU).strftime(FORMAT_DATE)
    return ((campagne.get("input") or "")
            .replace("{namespace}", campagne.get("namespace") or "")
            .replace("{filter}", json.dumps(campagne.get("row_filter") or {},
                                            ensure_ascii=False))
            .replace("{date_du_jour}", jour))
