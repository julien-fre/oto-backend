"""Socle partagé des modules du connecteur `pennylane` — oto-backend#872.

Le connecteur tient sur plusieurs modules (`tools/pennylane*.py`, cf.
`Connector.modules` au registre). Ce fichier porte ce qu'ils ont en commun :
la résolution de la clé, les deux formes d'erreur, et surtout la **traduction
d'un refus amont en exception**.

Pourquoi un fichier plutôt qu'une copie par module : le client d'oto-core rend
un refus comme une *valeur* (`{"error": "422", "details": …}`) et non comme une
exception. La pièce qui rattrape ça ne doit exister qu'une fois — dupliquée,
elle diverge, et c'est le module oublié qui écrira dans une comptabilité sans
que personne le voie.
"""
from __future__ import annotations

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError
from .. import access


def _client():
    """Le client Pennylane pour la clé de CET appelant.

    L'import est fait ici, pas au chargement du module : les tests remplacent
    `PennylaneClient` sur le package, et un import différé est ce qui leur
    laisse la main.
    """
    from oto.tools.pennylane import PennylaneClient

    key, _is_platform = access.resolve_api_key("pennylane")
    # Rédaction appliquée à la frontière des tools par `FieldRedactionMiddleware`
    # (policy de l'org active), plus au niveau client.
    return PennylaneClient(api_key=key)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
    if value is None:
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _ecrit(res, geste: str):
    """Rend le résultat d'une écriture, ou LÈVE si Pennylane l'a refusée.

    Le client rend un refus comme une **valeur** — `{"error": "422", "details":
    …}` — et non comme une exception. Sans cette traduction, l'agent enchaîne
    sur un refus en croyant avoir écrit ; sur une comptabilité, l'écart se
    découvre au rapprochement, très loin du geste qui l'a créé.

    Le message discrimine les causes, parce qu'elles n'appellent pas la même
    suite : un droit manquant ne se corrige pas en changeant les arguments.
    """
    if not (isinstance(res, dict) and res.get("error")):
        return res
    st = str(res.get("status_code") or res.get("error"))
    detail = str(res.get("details") or res.get("error"))[:400]
    if st in ("401", "403"):
        raise _bad(
            f"Pennylane a refusé {geste} ({st}) : c'est un DROIT qui manque à la "
            "clé, pas un argument à corriger — rejouer à l'identique échouera "
            "pareil. Chaque utilisateur pose sa propre clé, avec son propre "
            "périmètre : qu'un tool soit monté ne prouve donc AUCUN droit. Lis "
            "les droits réels de la clé avec `pennylane_ref(kind=\"company\")`, "
            f"champ `scopes`, puis dis à l'utilisateur lequel manque. Détail : {detail}")
    if st == "422":
        raise _bad(f"Pennylane a refusé le CONTENU de {geste} ({st}) : les valeurs "
                   f"envoyées ne passent pas ses contrôles. Détail : {detail}")
    if st == "404":
        raise _bad(f"Pennylane ne trouve pas la cible de {geste} ({st}) : l'id "
                   f"n'existe pas dans CETTE société. Détail : {detail}")
    raise _bad(f"Pennylane a refusé {geste} ({st}). Détail : {detail}")
