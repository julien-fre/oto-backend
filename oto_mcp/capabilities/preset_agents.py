"""Le réglage PERSONNEL : comment naissent MES agents, dans une org.

`private` (défaut) ou `shared`, posé par la personne pour elle-même
(`oto_trigger op=default_sharing`) et lu à `op=create`, quelle que soit la face qui crée
(application, client MCP, agent hébergé).

`shared` fait, à la naissance, les deux gestes du propriétaire : partage en écriture à
toute l'org, puis prêt du forfait si l'agent tourne sur un abonnement personnel. Ce
n'est PAS `partage_par_defaut` : aucun admin ne le pose pour un autre (garde
`default_sharing_write_unsupported`), le titulaire consent une fois pour tous ses
futurs agents de cette org. Le partage porte son `granted_by`, il prête donc comme un
geste manuel et se retire pareil. Rien de rétroactif. `private=true` à la création
l'emporte.

Stocké dans la fiche du compte (`user_account_profile.profile`), clé `agent_sharing`,
`{"<org_id>": "shared"}`.
"""
from __future__ import annotations

from .. import db

PRIVE, PARTAGE = "private", "shared"
VALEURS = (PRIVE, PARTAGE)
CLE = "agent_sharing"


def _par_org(sub: str) -> dict:
    v = db.get_account_profile(sub)["profile"].get(CLE)
    return v if isinstance(v, dict) else {}


def lire(sub: str, org_id: int) -> str:
    return PARTAGE if _par_org(sub).get(str(org_id)) == PARTAGE else PRIVE


def poser(sub: str, org_id: int, valeur: str) -> str:
    db.update_account_profile(sub, {CLE: {**_par_org(sub), str(org_id): valeur}})
    return valeur
