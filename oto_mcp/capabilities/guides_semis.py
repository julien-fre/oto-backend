"""Santé d'instance : les guides plateforme du dépôt sont-ils vraiment servis ?
(otomata-tech/oto#236)

Un guide plateforme est un TEXTE SERVI À L'AGENT : périmé, il fait appliquer des
gestes qui n'existent plus et ignorer ceux qui existent. Mesuré le 13/09/2026 sur
`datastore-semantics` : le guide servi en production avait environ 250 lignes de
retard sur le fichier du dépôt, depuis plusieurs livraisons, parce que le semis de
démarrage n'INSÉRAIT que les slugs absents. Personne ne pouvait l'apprendre : rien
ne comparait, et rien ne le disait.

Le semis compare désormais (`guide_store.seed_platform_guides`). Cette capacité sert
ce qu'il a constaté — et surtout ce qu'il n'a PAS pu faire : un slug en échec, un
guide édité en base depuis son dernier semis (conservé, ADR 0042 : la base reste la
source éditable), une ligne posée avant que le semis n'empreinte. Trois défauts
nommés, parce que les conduites diffèrent : réparer, réconcilier, ou jouer le geste
de maintenance `scripts/aligner_guides_plateforme.py`.

**Le rapport est celui de CE process** : le semis est un geste de démarrage, il n'a
pas d'autre domicile que la mémoire du serveur qui l'a joué. `fait: false` ne dit
donc pas « tout va bien », il dit « ce process n'a rien semé » — et c'est un défaut,
pas un silence.

Lecture seule, `PLATFORM_ADMIN`. Pas de face REST : rien ici n'est destiné à un
écran, c'est la réponse à une question d'opérateur.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ._authz import PLATFORM_ADMIN
from ._types import Capability, ResolvedCtx
from .registry import CAPABILITIES


class SemisGuidesInput(BaseModel):
    pass


class DefautDeSemis(BaseModel):
    #: `semis_absent` | `guides_non_semes` | `guide_divergent` | `guides_sans_empreinte`
    code: str
    slugs: list[str] = []
    detail: str = ""


class SanteSemisGuides(BaseModel):
    """Ce que le démarrage a fait des fichiers `oto_mcp/guides/*.md`."""
    fait: bool
    at: Optional[str] = None
    semes: list[str] = []
    mis_a_jour: list[str] = []
    inchanges: list[str] = []
    divergents: list[str] = []
    sans_empreinte: list[str] = []
    echecs: dict[str, str] = {}
    #: Vide = aucun défaut. Chaque entrée nomme une conduite différente.
    defauts: list[DefautDeSemis] = []


def _semis_guides(ctx: ResolvedCtx, inp: SemisGuidesInput) -> dict:
    from .. import guide_store
    return guide_store.sante_du_semis()


CAPABILITIES += [
    Capability(
        key="admin.guides_semis", handler=_semis_guides, Input=SemisGuidesInput,
        Output=SanteSemisGuides, authz=PLATFORM_ADMIN,
        description=(
            "[platform admin] Instance health for PLATFORM GUIDES: what this server's "
            "boot did with the repo files `oto_mcp/guides/*.md` — seeded, updated, "
            "unchanged, and the three defects that need a human: `guides_non_semes` "
            "(the seed failed for that slug), `guide_divergent` (the DB copy was "
            "edited since its last seed, so it is KEPT and the repo file is NOT "
            "served — ADR 0042), `guides_sans_empreinte` (rows written before the "
            "seed started fingerprinting; run `scripts/aligner_guides_plateforme.py` "
            "once). `fait: false` means this process seeded nothing — that is a "
            "defect, not silence. A served guide that lags the repo makes every agent "
            "apply gestures that no longer exist. Read-only."),
        mcp="oto_admin_guides_semis",
    ),
]
