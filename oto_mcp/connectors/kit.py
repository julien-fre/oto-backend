"""Le kit d'organisation — UNE fonction d'application (ADR 0050 §E8, oto#166).

Le kit (`orgs.default_connectors`) est la liste de connecteurs qu'une org installe
dans la boîte à outils de ses membres — ceux qui arrivent (au semis,
`session_visibility`) ET ceux qui sont déjà là (ici, au geste de l'admin). Tout
geste d'org sur ces boîtes passe par `appliquer` :

| geste (capacité)                                   | appel                              |
|----------------------------------------------------|------------------------------------|
| poser le kit en entier (`connectors.recommend`)    | `appliquer(org, kit=[…])`          |
| ajouter au kit (`connectors.bulk_select`)          | `appliquer(org, ajouter=[nom])`    |
| retirer du kit (`connectors.unset_default`)        | `appliquer(org, retirer=[nom])`    |
| pousser à UN membre (`connectors.force.member`)    | `appliquer(org, ajouter=[nom],`    |
|                                                    | `          pousser_a=sub)`         |

**Garde d'écriture (§E2)** : un AJOUT au kit nomme un connecteur connu du registre et
exposé pour l'org, sinon tout le geste est refusé, raison nommée, rien n'est écrit
(`AjoutRefuse`). Un connecteur coupé APRÈS sa mise au kit y reste : installé, masqué
chez tous, il revient seul à la réouverture — la réponse le liste (`cut`).

Seule la DIFFÉRENCE entre l'ancien et le nouveau kit s'applique aux membres
(décision Q4 du 11/09 : « une modification future s'applique à tous les membres,
anciens compris ; ce qui a été posé avant n'est pas rejoué »). Un connecteur nommé
par le geste mais déjà au kit n'est rejoué chez personne, et la réponse le dit
(`unchanged`).

Un ajout installe chez chaque membre de l'org, provenance `kit` (§E4), par
`selection.install_for_member` — jamais par-dessus le membre : une ligne existante
(active ou en pause) reste, un retrait du membre n'est pas défait. Le kit et les
boîtes s'écrivent dans UNE transaction, la ligne de l'org verrouillée (`FOR
UPDATE`) : deux admins qui modifient le kit en même temps ne calculent pas leur
différence sur le même « avant ».

Un RETRAIT du kit (décision Q1 du 11/09) désinstalle le connecteur chez chaque membre
dont la ligne porte la provenance `kit`, active ou en pause — et nulle part ailleurs :
installé ou repris par le membre (`membre`), poussé par un admin (`admin`), venu du
socle (`socle`) ou antérieur à la trace (`inconnue`), il reste.

La POUSSÉE à un membre (décision Q2) installe chez lui seul, provenance `admin`, avec
les mêmes exceptions — elle ne touche pas au kit, et ne défait jamais son retrait.

La réponse est chiffrée par connecteur : installé chez N, déjà actif chez M, laissé
chez P qui l'ont en pause, laissé chez R qui l'ont retiré eux-mêmes. Visible à l'écran tout de suite ; pour l'agent d'un
membre, à sa PROCHAINE conversation — le registre d'outils est figé à l'ouverture.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import selection as sel

ADDED = "added"
REMOVED = "removed"

AGENT_NOTE = ("Effet visible à l'écran tout de suite ; pour l'agent d'un membre, à sa "
              "PROCHAINE conversation — le registre d'outils d'une conversation ouverte "
              "est figé et aucune écriture n'y change rien.")
# Servi à l'ADMIN quand son geste nomme un connecteur déjà au kit (lecture de Q4
# retenue le 11/09/2026, ADR 0050 §E) : son clic a bien été reçu, il n'y avait rien à
# modifier. Le texte dit POURQUOI et COMMENT faire s'il le veut vraiment chez les
# membres actuels — une décision de l'admin, pas un rattrapage de la plateforme. Il
# est vrai avant comme après l'application de Q1 : un retrait du kit ne désinstalle
# jamais que ce que le kit a lui-même posé.
UNCHANGED_NOTE = (
    "Déjà dans le kit : ton geste a bien été reçu, mais il ne modifie pas le kit, donc "
    "il n'installe rien chez les membres actuels. Le kit n'applique aux membres déjà "
    "entrés que ses MODIFICATIONS faites depuis le 11/09/2026 ; ce qu'il contenait avant "
    "ne se rejoue pas. Pour l'installer malgré tout chez les membres actuels : retire-le "
    "du kit, puis remets-le. Ce retrait ne désinstalle rien que le kit n'ait posé "
    "lui-même ; la remise l'installe chez chaque membre qui ne l'a pas — sauf chez qui "
    "l'a retiré lui-même depuis le 11/09/2026, qui le garde retiré.")


CUT_NOTE = ("Coupé pour ton organisation : il reste dans le kit et installé chez tes "
            "membres, mais masqué chez tous tant qu'il est coupé ; il revient seul quand tu "
            "le rends de nouveau disponible.")

# Raison d'un refus d'ajout, telle que servie (E2 : « le refus dit pourquoi »).
RAISONS = {
    "unknown": "est inconnu du registre des connecteurs",
    "platform_disabled": "est coupé par la plateforme : ton organisation ne peut pas l'installer",
    "org_disabled": ("n'est pas disponible pour tes membres (ton organisation l'a coupé) : "
                     "rends-le disponible d'abord"),
}


class OrgInconnue(LookupError):
    """L'org visée n'existe pas."""


class AjoutRefuse(ValueError):
    """Un ajout nomme un connecteur que l'org ne peut pas installer (ADR 0050 §E2).
    `refus` = `[{"connector", "reason"}]`, raison ∈ `RAISONS`. Levé AVANT toute
    écriture, dans la transaction : rien n'est écrit, ni au kit ni chez les membres."""

    def __init__(self, refus: list[dict]):
        self.refus = refus
        super().__init__("; ".join(f"{r['connector']}: {r['reason']}" for r in refus))


def refus_d_ajout(org_id: int, noms: Iterable[str]) -> list[dict]:
    """E2 — on n'AJOUTE au kit qu'un connecteur connu du registre et exposé pour
    l'org. La garde vaut pour le geste qui ajoute ; un connecteur coupé APRÈS sa mise
    au kit y reste (cf. `coupes`). Rend les refus, raison nommée ; vide = tout passe.
    Coupé par l'org (le master l'expose, l'override d'org le retire) se distingue de
    coupé par la plateforme : ce ne sont pas les mêmes gestes pour le rouvrir."""
    from .. import providers
    from . import activation
    noms = list(noms)
    if not noms:
        return []
    expo = activation.exposed_connectors(org_id)
    refus = []
    for n in noms:
        if n not in providers.REGISTRY:
            refus.append({"connector": n, "reason": "unknown"})
        elif n not in expo:
            refus.append({"connector": n, "reason": "org_disabled"
                          if activation.is_exposed(n, None) else "platform_disabled"})
    return refus


def coupes(org_id: int, kit: Iterable[str]) -> list[str]:
    """Les connecteurs du kit que l'org n'expose plus : ils y restent (l'intention de
    l'admin), installés et masqués chez tous, et reviennent seuls à la réouverture."""
    from .. import providers
    from . import activation
    kit = list(kit)
    if not kit:
        return []
    expo = activation.exposed_connectors(org_id)
    return [n for n in kit if n in providers.REGISTRY and n not in expo]


def _dedupe(noms: Iterable[str]) -> list[str]:
    vus: set[str] = set()
    out: list[str] = []
    for n in noms:
        if n not in vus:
            vus.add(n)
            out.append(n)
    return out


def appliquer(org_id: int, *, kit: Optional[Iterable[str]] = None,
              ajouter: Iterable[str] = (), retirer: Iterable[str] = (),
              pousser_a: Optional[str] = None) -> dict:
    """Applique un geste d'org sur le kit et les boîtes de ses membres. Voir le module.
    `pousser_a=sub` = la poussée nominative : UN connecteur (`ajouter`), UN membre,
    provenance `admin`, kit intact."""
    from .. import db

    ajouter, retirer = _dedupe(ajouter), _dedupe(retirer)
    if kit is not None and (ajouter or retirer):
        raise ValueError("appliquer : `kit` (le kit entier) OU `ajouter`/`retirer`, pas les deux")
    if pousser_a is not None and (kit is not None or retirer or len(ajouter) != 1):
        raise ValueError("appliquer : une poussée ajoute UN connecteur à UN membre, sans toucher au kit")
    with db._connect() as conn:
        row = conn.execute("SELECT default_connectors FROM orgs WHERE id = %s FOR UPDATE",
                           (org_id,)).fetchone()
        if row is None:
            raise OrgInconnue(org_id)
        avant = list(row["default_connectors"] or [])
        if pousser_a is not None:
            nommes, apres = [], avant
        elif kit is not None:
            nommes = _dedupe(kit)
            apres = nommes
        else:
            nommes = ajouter + retirer
            apres = [n for n in avant if n not in set(retirer)] + [
                n for n in ajouter if n not in avant]
        ajouts = list(ajouter) if pousser_a is not None else [n for n in apres if n not in avant]
        retraits = [n for n in avant if n not in apres]
        refus = refus_d_ajout(org_id, ajouts)
        if refus:
            raise AjoutRefuse(refus)       # avant toute écriture : la transaction s'annule
        if pousser_a is None and (ajouts or retraits
                                  or (kit is not None and row["default_connectors"] is None)):
            conn.execute("UPDATE orgs SET default_connectors = %s WHERE id = %s",
                         (apres, org_id))
        membres = [pousser_a] if pousser_a is not None else [r["sub"] for r in conn.execute(
            "SELECT sub FROM org_members WHERE org_id = %s ORDER BY joined_at, sub",
            (org_id,)).fetchall()]
        origine = sel.ADMIN if pousser_a is not None else sel.KIT
        effets: list[dict] = []
        for c in ajouts:
            comptes = {"installed": 0, "already_active": 0, "paused": 0,
                       "removed_by_member": 0}
            retire_le = None
            for m in membres:
                issue = sel.install_for_member(conn, m, c, org_id, origine)
                comptes[issue] += 1
                if issue == "removed_by_member" and pousser_a is not None:
                    retire_le = conn.execute(
                        "SELECT removed_at FROM connector_selection_removed "
                        "WHERE sub = %s AND org_id = %s AND connector = %s",
                        (m, org_id, c)).fetchone()["removed_at"]
            effet = {"connector": c, "change": ADDED, **comptes}
            if retire_le is not None:
                effet["removed_at"] = str(retire_le)
            effets.append(effet)
        for c in retraits:
            # E5, décision Q1 : désinstallé là où le KIT l'a posé (actif ou en pause),
            # et nulle part ailleurs. On compte ce qui reste, par provenance.
            cur = conn.execute(
                "DELETE FROM user_selected_connectors WHERE org_id = %s AND connector = %s "
                "AND origin = %s AND sub = ANY(%s)", (org_id, c, sel.KIT, membres))
            reste = {r["origin"]: int(r["n"]) for r in conn.execute(
                "SELECT origin, count(*) AS n FROM user_selected_connectors "
                "WHERE org_id = %s AND connector = %s AND sub = ANY(%s) GROUP BY origin",
                (org_id, c, membres)).fetchall()}
            effets.append({"connector": c, "change": REMOVED,
                           "uninstalled": cur.rowcount or 0, "kept": reste})
    unchanged = [n for n in nommes if n not in ajouts and n not in retraits]
    cut = coupes(org_id, apres)
    out = {"org_id": org_id, "kit": apres, "members": len(membres),
           "changes": effets, "unchanged": unchanged, "cut": cut, "note": AGENT_NOTE}
    if unchanged:
        out["unchanged_note"] = UNCHANGED_NOTE
    if cut:
        out["cut_note"] = CUT_NOTE
    return out
