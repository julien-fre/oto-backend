"""Le catalogue des modèles qu'un agent hébergé peut déclarer — et leur FAMILLE.

Un agent (déclencheur, flotte) nomme un modèle ; le travail qu'il produit emporte
ce modèle ET sa famille. La famille est ce qui ROUTE : un worker ne réserve que
les travaux d'une famille qu'il sait servir, c'est-à-dire du dépôt de clé qu'il
nomme au claim (`provider`, cf. `runner_jobs._cle_de_modele`). Les deux mots
coïncident à dessein — la famille EST le nom du dépôt.

⚠️ **Un travail SANS famille est servi par N'IMPORTE QUEL worker**, sur son propre
modèle — c'est le comportement d'avant ce catalogue, à l'octet près, et c'est ce
que portent tous les déclencheurs et toutes les flottes déclarés avant lui. Une
règle stricte (« pas de famille, pas de worker ») aurait orphelin chacun d'eux le
jour du déploiement, sans une erreur.

⚠️ **Le catalogue refuse à l'ÉCRITURE, jamais à la lecture.** Une valeur inconnue
déjà en base (une flotte déclarée avec un modèle libre avant ce lot) se lit
toujours ; elle ne voyage simplement pas avec le travail, puisque rien ne sait la
router.

Pur : aucune dépendance, pour que la base comme les capacités puissent le lire.
"""
from __future__ import annotations

from typing import NamedTuple, Optional


class Modele(NamedTuple):
    id: str
    label: str
    family: str
    #: L'effort de réflexion — propriété du MODÈLE catalogué, pas un réglage
    #: par campagne ni par déclencheur (14/09/2026, ordonnancement Audiens).
    #: `None` = le fournisseur applique son défaut, comme avant ce champ.
    effort: Optional[str] = None


#: ⚠️ L'ORDRE est la préférence : le modèle proposé par défaut est le premier
#: modèle SERVI de cette liste (cf. `catalogue`). Aucun n'est marqué en dur.
MODELES: tuple[Modele, ...] = (
    Modele("claude-sonnet-5", "Claude Sonnet 5", "anthropic"),
    Modele("claude-opus-5", "Claude Opus 5", "anthropic"),
    Modele("claude-haiku-4-5", "Claude Haiku 4.5", "anthropic"),
    # La voie Conversations des workers de production (cf. oto-runner).
    Modele("mistral-large-2512", "Mistral Large", "mistral"),
    Modele("mistral-medium-2604", "Mistral Medium", "mistral", effort="high"),
)

_PAR_ID = {m.id: m for m in MODELES}

#: Les familles connues — c'est-à-dire les seuls dépôts dont la présence se note.
FAMILLES = frozenset(m.family for m in MODELES)


def famille(model: Optional[str]) -> Optional[str]:
    """La famille d'un modèle du catalogue, ou None (absent, ou inconnu)."""
    m = _PAR_ID.get(model or "")
    return m.family if m else None


def charge(model: Optional[str]) -> dict:
    """Ce qu'un travail emporte de son modèle : `{model, model_family}`, plus
    `effort` (14/09/2026) SI le modèle catalogué en déclare un — mistral-medium-2604
    tourne en effort HAUT, les autres modèles n'émettent rien de plus qu'avant :
    une requête sans effort porté est inchangée à l'octet près.

    ⚠️ Un modèle que le catalogue ne connaît pas ne part PAS : sans famille, il
    atteindrait un worker quelconque qui tenterait de l'appeler chez un
    fournisseur qui ne le sert peut-être pas. Ne rien envoyer rend le travail au
    comportement d'avant — le worker tourne sur son propre modèle."""
    m = _PAR_ID.get(model or "")
    if not m:
        return {}
    charge = {"model": model, "model_family": m.family}
    if m.effort:
        charge["effort"] = m.effort
    return charge


def catalogue(familles_servies, familles_sans_cle=()) -> list[dict]:
    """Le catalogue tel qu'un écran le propose : chaque modèle, s'il est SERVI —
    une famille dont un worker a sondé la file dans la fenêtre de présence — et
    celui à proposer par DÉFAUT.

    ⚠️ **Le défaut se DÉRIVE de ce qui est servi, il ne se déclare pas** : c'est le
    premier modèle servi dans l'ordre de `MODELES`. Une marque posée en dur
    (`claude-sonnet-5` jusqu'au 12/09/2026) proposait un modèle que les workers de
    production, qui ne servent que `mistral`, ne servaient pas : un agent qui la
    suivait se faisait refuser `model_not_served`.

    ⚠️ **Aucun défaut quand aucune famille n'est servie** — jamais de repli sur le
    premier du catalogue, qui proposerait un modèle refusé. L'absence se lit avec
    `families: []`, et le geste est alors de ne nommer aucun modèle : le worker
    tourne sur le sien. Le défaut ne s'écrit nulle part : un agent posé sans modèle
    reste NULL, et un modèle choisi n'est jamais changé.

    ⚠️ **Le défaut écarte les familles que l'org ne peut pas payer** (14/09/2026) :
    `familles_sans_cle` = celles dont la clé est EXIGÉE et que l'org n'a pas déposée
    (`capabilities/_cle_exigee.manquantes`). Un modèle de ces familles reste `served`
    — un worker le sert, et l'org peut déposer sa clé puis le choisir — mais il n'est
    jamais PROPOSÉ. Le premier worker Anthropic « clés clients seules » avait fait de
    `claude-sonnet-5` le défaut de toutes les orgs, dont aucune n'avait de clé : un agent
    qui le suivait se faisait refuser `model_key_required`. Si toutes les familles
    servies sont écartées, aucun défaut — jamais un repli sur un modèle refusé."""
    servies = set(familles_servies or ())
    proposables = servies - set(familles_sans_cle or ())
    defaut = next((m.id for m in MODELES if m.family in proposables), None)
    return [{"id": m.id, "label": m.label, "family": m.family,
             "default": m.id == defaut, "served": m.family in servies}
            for m in MODELES]
