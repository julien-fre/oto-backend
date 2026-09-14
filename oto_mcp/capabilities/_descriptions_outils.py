"""La borne des descriptions d'outils servies à l'agent, déclarée sur une campagne (oto#241).

Le worker la lit dans le travail (`payload["descriptions_outils"]`) et l'applique outil par
outil : `{defaut: <entier ≥ 1>, entieres: [<outil>, …]}` — les descriptions coupées à
`defaut` caractères, sauf celles des outils nommés, servies entières. Sans déclaration,
rien ne part et le worker garde son défaut (1 024, `data_write` entière).

Mesuré le 14/09/2026 sur une passe de 14 outils : servir entières `data_claim_next` et
`data_rows` ajoute 1 539 jetons par tour, lus à 99,5 % en cache. Coupées, ces deux
descriptions perdaient les règles de la réservation (`row: null`, ne jamais écrire son
intention, `null` = « ne l'invente jamais »).

⚠️ **La même grammaire vit dans le runner** (`oto_runner/descriptions.py::reglage`, autre
dépôt) : le runner valide aussi une déclaration LOCALE qui ne passe jamais par ici. Qui
change cette forme d'un côté change l'autre (14/09/2026).

⚠️ **Figée à la déclaration**, comme le modèle et la température : ce que l'agent lit fait
partie du contexte d'exécution, et le changer en vol rendrait incomparables les lignes déjà
écrites. `tools`, lui, reste modifiable : retirer un outil que `entieres` nomme est donc
refusé, sans quoi le réglage cesserait d'agir sans un mot.
"""
from __future__ import annotations

from typing import Optional

from ._types import AuthzDenied

CODE = "invalid_descriptions_outils"


def valider(reglage: Optional[dict], tools) -> Optional[dict]:
    """Le réglage déclaré, validé ; None s'il est absent. Lève `invalid_descriptions_outils`."""
    if reglage is None:
        return None
    if not isinstance(reglage, dict) or set(reglage) - {"defaut", "entieres"}:
        raise AuthzDenied(
            400, CODE,
            f"`descriptions_outils` = {reglage!r} : deux clés permises, `defaut` (un entier "
            "≥ 1, les caractères servis par description) et `entieres` (les outils servis "
            "sans coupe).")
    defaut = reglage.get("defaut")
    if defaut is not None and (isinstance(defaut, bool) or not isinstance(defaut, int)
                               or defaut < 1):
        raise AuthzDenied(400, CODE, f"`descriptions_outils.defaut` = {defaut!r} : un "
                                     "entier ≥ 1 est attendu.")
    entieres = reglage.get("entieres")
    if entieres is not None:
        if not isinstance(entieres, list) or not all(isinstance(x, str) and x for x in entieres):
            raise AuthzDenied(400, CODE, f"`descriptions_outils.entieres` = {entieres!r} : "
                                         "une liste de noms d'outils est attendue.")
        hors = sorted(set(entieres) - set(tools or ()))
        if hors:
            raise AuthzDenied(
                400, CODE,
                f"`descriptions_outils.entieres` nomme {', '.join(hors)}, absent(s) de "
                "`tools` : un outil que la campagne n'autorise pas n'a pas de description "
                "à servir.")
    return dict(reglage)


def outils_nommes_retires(reglage: Optional[dict], nouveaux_tools) -> list[str]:
    """Les outils que `entieres` nomme et qu'une nouvelle allowlist retirerait."""
    return sorted(set((reglage or {}).get("entieres") or ()) - set(nouveaux_tools or ()))
