"""Outils MCP RETIRÉS — le nom ne répond plus : il REFUSE en nommant le geste qui aboutit.

Un retrait n'est pas un renommage (`deprecations.TOOLS`) : il n'y a pas de nom
d'aujourd'hui vers lequel traduire l'appel, donc rien à servir « à côté ». Ce n'est pas
non plus un nom simplement inconnu : `error_taxonomy._surviving_siblings` DÉRIVE la
fratrie d'un nom disparu, ce qui suffit quand ses verbes ont été rangés sous un voisin
(`gmail_search` → `gmail_message`, aujourd'hui `gmail`). Pour un retrait, la fratrie serait les ~60 outils
`oto_*` — une liste qui n'apprend rien — alors que le geste qui aboutit est connu et
précis. Il s'écrit donc, une fois, ICI, et trois surfaces le lisent :

- `error_taxonomy.classify` — un `tools/call` direct sur le nom (fastmcp : « Unknown
  tool ») ;
- `tools/meta.oto_call` — AVANT le refus méta/spine : un nom `oto_*` y recevait « appelle-le
  directement », qui renvoie au chemin précédent. Une boucle ;
- `tools/meta.oto_tool_schema` — ce que l'agent lit avant d'appeler.

⚠️ **Le refus doit être AUTOSUFFISANT.** Il est lu par des agents qu'une procédure ou une
page d'org — contenu client, qu'on ne corrige pas — envoie encore vers ce nom : aucun
renvoi à un guide, aucune hypothèse sur ce que l'agent a lu avant.

Un retrait peut aussi REMPLACER un nom sans le servir à côté (`RENOMMAGES_ACT`, 17/10/2026) :
quand l'ancien nom est lui-même la faille, il n'y a pas d'alias à tenir pendant un préavis —
il refuse tout de suite, en nommant le nouvel appel.

Module PUR (aucun import `oto_mcp`) : la taxonomie d'erreurs doit rester importable seule.
"""
from __future__ import annotations

import datetime
from typing import NamedTuple, Optional


class Retrait(NamedTuple):
    #: Ce que l'agent lit : ce qui a disparu, pourquoi rien n'est perdu, et le geste.
    message: str
    #: Le `hint` de l'enveloppe d'erreur (`data.oto.hint`) : le geste seul.
    hint: str


#: `oto_kb` — retiré le 10/09/2026 (décision d'Alexis) : la « base de connaissance » n'existe
#: plus comme concept, il n'y a que projet, doc et procédure. La DONNÉE ne bouge pas : c'était
#: déjà un projet ordinaire. La route REST `POST /api/me/kb` reste, le tableau de bord la
#: consomme (cf. `capabilities/kb.py`).
KB = Retrait(
    message=(
        "`oto_kb` is retired: there is no org \"knowledge base\" any more — oto has "
        "projects, docs and procedures, nothing else. What was called the knowledge base "
        "is an ordinary PROJECT of the org; nothing was moved or deleted, its pages are all "
        "still there. Reach them like any project's pages: find the project with "
        "`oto_project op=list` (seeded as \"Knowledge base\", or \"Base de connaissance\" in "
        "older orgs, and possibly renamed since), then read or write its pages with "
        "`oto_doc` and that `project_id` (`op=search`, `op=get`, `op=create`, "
        "`op=update`). Durable knowledge you capture goes, as a doc, into the project it "
        "belongs to. If a procedure or a page told you to call `oto_kb`, it predates this "
        "retirement: take the path above instead — this name only returns this refusal, "
        "called directly or through `oto_call`."),
    hint=("find the project with oto_project op=list, then read or write its pages with "
          "oto_doc and its project_id"),
)

# --- 17/10/2026 : lire ou simuler (`ns`), agir (`ns_act`) --------------------------------
# Décision d'Alexis, pour la sécurité : un client MCP autorise des OUTILS, pas des
# arguments. `gmail_compose(mode="send")` envoyait sous l'autorisation du brouillon,
# `gmail_message(op="trash")` vidait une boîte sous celle de la lecture. Chaque connecteur
# sert désormais DEUX outils : `<ns>` lit ou simule (un brouillon est une simulation),
# `<ns>_act` engage ou détruit (envoi, corbeille, archivage, déplacement) — c'est lui que
# le client fait confirmer. Coupure IMMÉDIATE, sans transition (exception à l'Art 8.2,
# `docs/alias-deprecies.md`).
#
# **`RENOMMAGES_ACT` est LA table de correspondance** : le refus nommé de chaque ancien
# nom, la migration des listes d'outils enregistrées (révision 0062) et l'annonce servie
# aux agents (`annonce_act`) en DÉRIVENT. Une ligne ajoutée ici est annoncée, refusée et
# migrée ; rien ne se recopie ailleurs.


class Renommage(NamedTuple):
    ancien: str                 # l'outil retiré
    selecteur: Optional[str]    # ce qui choisissait le geste : "op=…", "mode=…", None = tout l'outil
    outil: str                  # le nouvel outil
    op: str                     # son op


def _ops(ancien: str, cible: str, ops: tuple) -> tuple:
    return tuple(Renommage(ancien, f"op={o}", cible, o) for o in ops)


RENOMMAGES_ACT: tuple[Renommage, ...] = (
    Renommage("gmail_list_accounts", None, "gmail", "accounts"),
    *_ops("gmail_message", "gmail", ("search", "get", "attachment", "drafts")),
    *_ops("gmail_message", "gmail_act", ("archive", "trash")),
    Renommage("gmail_compose", "mode=draft", "gmail", "draft"),
    Renommage("gmail_compose", "mode=send", "gmail_act", "send"),
    *_ops("outlook_message", "outlook", ("search", "get", "attachment", "drafts", "folders")),
    *_ops("outlook_message", "outlook_act", ("archive", "trash", "move")),
    Renommage("outlook_compose", "mode=draft", "outlook", "draft"),
    Renommage("outlook_compose", "mode=send", "outlook_act", "send"),
)

#: L'annonce est servie aux agents (`instructions`, section « Ton contexte oto » : au
#: handshake et dans `oto_context`) jusqu'à ce jour inclus — deux mois, la durée du préavis
#: qu'elle remplace.
ANNONCE_ACT_JUSQU_AU = datetime.date(2026, 12, 17)


def remplacants(ancien: str) -> tuple[str, ...]:
    """Les nouveaux outils qui reprennent TOUT ce que faisait `ancien` — pour qu'une
    liste d'outils autorisés (ou masqués) garde exactement son sens. `()` si `ancien`
    n'est pas renommé."""
    return tuple(dict.fromkeys(r.outil for r in RENOMMAGES_ACT if r.ancien == ancien))


def _retrait_act(ancien: str) -> Retrait:
    lignes = [r for r in RENOMMAGES_ACT if r.ancien == ancien]
    gestes = "; ".join(
        (f"{r.selecteur} → " if r.selecteur else "")
        + f'`{r.outil}(op="{r.op}", …)`' for r in lignes)
    lecteur = ancien.split("_")[0]
    acteur = f"{lecteur}_act"
    return Retrait(
        message=(
            f"`{ancien}` is retired (17/10/2026, security): an MCP client authorizes "
            f"TOOLS, not arguments, so a tool that reads must not also send or delete. "
            f"`{lecteur}` reads and simulates (a draft is a simulation); `{acteur}` acts — "
            f"it sends, deletes or changes, and your client asks you to confirm it. Call "
            f"instead, with the same "
            f"other arguments: {gestes}. If a procedure or a page told you to call "
            f"`{ancien}`, it predates this change: take the path above — this name only "
            f"returns this refusal, called directly or through `oto_call`."),
        hint=gestes,
    )


def annonce_act_du_jour(aujourd_hui: Optional[datetime.date] = None) -> Optional[str]:
    """`annonce_act()` tant qu'elle est servie, None ensuite."""
    jour = aujourd_hui or datetime.date.today()
    return annonce_act() if jour <= ANNONCE_ACT_JUSQU_AU else None


def annonce_act() -> str:
    """La ligne servie aux agents : « ces outils ont changé », ancien → nouveau."""
    anciens = dict.fromkeys(r.ancien for r in RENOMMAGES_ACT)
    return ("Tools changed on 2026-10-17 (security: reading and acting are two tools): "
            + " · ".join(f"`{a}` → {' + '.join(f'`{o}`' for o in remplacants(a))}"
                         for a in anciens)
            + ". The old names refuse, naming the call to make instead.")


RETIRES: dict[str, Retrait] = {
    "oto_kb": KB,
    **{a: _retrait_act(a) for a in dict.fromkeys(r.ancien for r in RENOMMAGES_ACT)},
}


def retrait(nom: str) -> Optional[Retrait]:
    """Le refus servi pour un nom CANONIQUE retiré, ou None. L'appelant canonicalise
    d'abord (préfixe de tenant) : `acme_kb` d'un tenant arrive ici en `oto_kb`."""
    return RETIRES.get(nom)
