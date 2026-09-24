"""Les clés d'un projet PARTAGÉ : ce que son bénéficiaire atteint (#480).

**La règle — arbitrage d'Alexis du 23/09/2026 (option A).** Quelqu'un à qui l'on
partage un projet y travaille **avec ses propres clés**. Les clés du propriétaire
(celles de son org, et de son équipe pour un projet d'équipe) ne lui sont prêtées que
si le partageur l'a **accordé explicitement**, au partage, et jamais au-delà de ses
propres droits. « Iso que le partage soit vers un membre, une team ou une org » : la
même règle, le même paramètre (`credentials`) et le même comportement pour les trois
types de bénéficiaire — rien ici ne regarde par quel type de grant l'appelant est
entré.

**Le trou qu'elle ferme.** `_project=` co-pose l'org propriétaire comme contexte de
l'appel (`call_axes._pin_project`), et le barreau ORG de la cascade n'était gardé par
aucune appartenance : un bénéficiaire hors de cette org agissait sous ses clés d'org,
sans que personne l'ait décidé. Le barreau équipe, lui, était déjà gardé
(`can_read_group` à la pose) ; l'org ne l'était pas.

**Ce que « ses propres clés » veut dire**, sous `_project=` d'une org dont il n'est pas
membre :
- sa clé membre dans l'org du projet, s'il l'y a posée sciemment ;
- sinon sa clé personnelle posée dans une autre de ses orgs — l'org perso d'abord,
  sinon la plus récente (l'instance cross-org de #172, étendue ici à tout connecteur à
  clé personnelle, multi-compte compris : même `sub`, zéro usurpation) ;
- son tenant et ses propres accès plateforme — jamais ceux que la plateforme accorde
  à l'org du projet (`org:<id>`), qui sont des droits de cette org.

**L'héritage** (`credentials="inherit"` au partage) est une arête de la chaîne de
grants (ADR 0053) — `grants.resource_kind = 'project_credentials'`, émise par le
partageur (`grantor = user:<sub>`), reçue par le principal du partage. Il ouvre le
barreau org (et le barreau de l'équipe propriétaire) **tant que le partageur les
atteint lui-même** : la borne se relit à chaque appel, elle ne se fige pas au partage.
Révoquer = archiver l'arête (`credentials="own"`, ou `unshare`). Le prêt ne survit pas
à l'échéance du partage qui le porte (otomata-tech/oto#39) : l'arête n'est lue que pour
un principal dont le partage du projet est vivant.

**Coût.** Le verdict se calcule UNE fois, à la pose de `_project=` (threadpool,
`call_axes`), et voyage dans un contextvar. Le walker ne fait qu'y lire : zéro requête
sur le chemin chaud, et un appel hors projet partagé ne paie rien.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import db, group_store, org_store, session_org

OWN = "own"
INHERIT = "inherit"
MODES = (OWN, INHERIT)

#: `grants.resource_kind` de l'héritage — le point d'extension prévu par 0053 §4.
RESOURCE_KIND = "project_credentials"


def ref_projet(project_id: int) -> str:
    """`grants.resource_id` de l'héritage d'un projet. Préfixé : les autres lecteurs
    de `grants` filtrent par préfixe (`platform:`, `tenant:`) et ne le croisent pas."""
    return f"project:{int(project_id)}"


@dataclass(frozen=True)
class ClesDuProjet:
    """Ce qu'un appelant atteint des clés PARTAGÉES du propriétaire d'un projet.

    N'existe que lorsqu'il y a quelque chose à dire : un appelant membre de l'org et,
    pour un projet d'équipe, lecteur de l'équipe, atteint déjà tout — pas de verdict."""
    sub: str
    projet: int
    org: int                          # org de credentials du projet (propriétaire)
    membre: bool                      # l'appelant est membre de cette org
    org_heritee: bool                 # barreau org prêté par héritage
    groupe_herite: Optional[int]      # barreau de l'équipe propriétaire prêté par héritage


def contexte_du_projet(project_id: int) -> Optional[tuple[Optional[int], Optional[int]]]:
    """`(org, équipe)` dont un projet résout les clés — None si le projet n'existe pas.

    Projet d'org : l'org. Projet d'équipe : son org parente + l'équipe. Projet perso :
    son org de contexte (`context_org_id`), repli sur l'org perso du propriétaire pour
    un perso legacy sans contexte (jamais `int(sub)`). Source UNIQUE : la pose de
    `_project=` et la borne du partageur lisent la même."""
    from .. import ownership
    owner = ownership.owner_of("project", str(project_id))
    if owner is None:
        return None
    owner_type, owner_id = owner
    if owner_type == "org":
        return int(owner_id), None
    if owner_type == "group":
        g = group_store.get_group(int(owner_id))
        return (g.get("org_id") if g else None), int(owner_id)
    if owner_type == "user":
        row = db.get_project_by_id(int(project_id))
        ctx = row.get("context_org_id") if row else None
        return (int(ctx) if ctx is not None else org_store.get_personal_org(owner_id)), None
    return None, None


def evaluer(sub: str, project_id: int, org: Optional[int], groupe_proprio: Optional[int],
            groupe_pose: Optional[int]) -> Optional[ClesDuProjet]:
    """Le verdict de l'appelant sur les clés du projet (chemin DB, threadpool).

    `groupe_pose` = l'équipe que la pose de `_project=` a co-posée (None si l'appelant
    ne lit pas l'équipe propriétaire). L'héritage est lu sur les arêtes vivantes qui
    visent l'un des principals de l'appelant — lui, ses orgs, ses équipes : c'est ce
    qui rend la règle iso sur les trois types de bénéficiaire."""
    from .. import ownership, roles
    if org is None:
        return None
    membre = roles.is_org_member(sub, int(org))
    manque_groupe = groupe_proprio is not None and groupe_pose is None
    if membre and not manque_groupe:
        return None
    from ..db import grants as db_grants
    pairs = ownership.accessor_scope(sub).principal_pairs()
    aretes = [e for e in db_grants.edges_for(ref_projet(project_id), pairs)
              if e.get("revoked_at") is None and e.get("resource_kind") == RESOURCE_KIND]
    # Le prêt vit sur une arête de `grants`, qui ne connaît pas l'échéance du partage
    # (otomata-tech/oto#39) : il ne vaut que pour un principal dont le partage du
    # projet est ENCORE vivant. Échu, le partage n'ouvre plus rien — les clés non plus.
    # Lu seulement s'il y a un prêt à borner : sans arête, la pose ne paie rien de plus.
    vivants = (db.principals_with_live_grant("project", str(int(project_id)))
               if aretes else set())
    org_heritee, groupe_herite = False, None
    for e in aretes:
        if (e.get("grantee_kind"), str(e.get("grantee_id"))) not in vivants:
            continue
        partageur = e.get("grantor_id") if e.get("grantor_kind") == "user" else None
        if not partageur:
            continue
        # La borne, relue à chaque pose : le partageur n'a prêté que ce qu'il atteint
        # AUJOURD'HUI. Sorti de l'org depuis, son prêt s'éteint sans rien réécrire.
        if not membre and roles.is_org_member(partageur, int(org)):
            org_heritee = True
        if manque_groupe and roles.can_read_group(partageur, int(groupe_proprio)):
            groupe_herite = int(groupe_proprio)
    return ClesDuProjet(sub=sub, projet=int(project_id), org=int(org), membre=membre,
                        org_heritee=org_heritee, groupe_herite=groupe_herite)


def du_contexte(sub: Optional[str], org: Optional[int]) -> Optional[ClesDuProjet]:
    """Le verdict de l'APPEL courant, s'il porte sur CETTE org et CE sub — sinon None
    (appel hors projet partagé, anonyme, ou walker interrogé pour un autre contexte)."""
    v = session_org.current_call_cles()
    if v is None or sub is None or org is None or v.sub != sub or v.org != int(org):
        return None
    return v


def hors_org(cles: Optional[ClesDuProjet]) -> bool:
    """L'appelant agit-il dans un projet d'une org dont il n'est PAS membre ?"""
    return cles is not None and not cles.membre


def org_partagee(org: Optional[int], cles: Optional[ClesDuProjet]) -> Optional[int]:
    """L'org dont l'appelant peut consommer les droits PARTAGÉS (clé d'org, accès
    plateforme accordés à l'org, plan de l'org) — l'org de contexte, sauf pour un
    bénéficiaire hors de l'org à qui rien n'a été prêté : None."""
    if hors_org(cles) and not cles.org_heritee:  # type: ignore[union-attr]
        return None
    return org


def org_du_lien(sub: str, org: Optional[int]) -> Optional[int]:
    """L'org du lien « où poser ta clé » d'un refus : pas celle d'un projet partagé
    dont l'appelant n'est pas membre (page qu'il n'ouvre pas) — None vise son compte."""
    return None if hors_org(du_contexte(sub, org)) else org


def instance_heritee(sub: str, ref) -> bool:
    """L'instance BINDÉE par le projet est-elle une clé que l'héritage prête à
    l'appelant ? Sinon la garde d'appartenance ordinaire s'applique."""
    v = session_org.current_call_cles()
    if v is None or v.sub != sub:
        return False
    level = getattr(ref, "level", None)
    if level == "org":
        return v.org_heritee and getattr(ref, "org_id", None) == v.org
    if level == "group":
        return v.groupe_herite is not None and getattr(ref, "group_id", None) == v.groupe_herite
    return False


def indice_refus(sub: str, org: Optional[int], provider: str) -> str:
    """Suffixe d'un refus « aucune clé » quand l'appelant travaille dans un projet
    partagé dont le propriétaire DÉTIENT cette clé sans la lui prêter : lui dire les
    deux sorties, sans le laisser croire que la clé n'existe pas."""
    v = du_contexte(sub, org)
    if v is None:
        return ""
    proprio_org = (not v.membre and not v.org_heritee
                   and org_store.has_org_secret(v.org, provider))
    proprio_groupe = False
    if v.groupe_herite is None:
        groupe = (contexte_du_projet(v.projet) or (None, None))[1]
        proprio_groupe = groupe is not None and group_store.has_group_secret(groupe, provider)
    if not (proprio_org or proprio_groupe):
        return ""
    return (
        f"\nTu travailles dans le projet #{v.projet}, qui t'est partagé : ses clés "
        f"`{provider}` appartiennent à son propriétaire et ne te sont pas prêtées. Deux "
        f"sorties — pose ta propre clé `{provider}` dans ton compte (elle te suit dans "
        f"ce projet), ou "
        f"demande au partageur de t'accorder l'héritage : oto_resource(op='share', "
        f"resource_type='project', resource_id='{v.projet}', <ton email|org_id|group_id>, "
        f"credentials='inherit')."
    )


# ── Écriture : le partage déclare, la lecture montre ──────────────────────────

def peut_accorder(partageur: str, project_id: int) -> bool:
    """La borne à la déclaration : on ne prête que ce qu'on atteint soi-même — être
    membre de l'org dont le projet résout les clés."""
    from .. import roles
    ctx = contexte_du_projet(project_id)
    return bool(ctx and ctx[0] is not None and roles.is_org_member(partageur, int(ctx[0])))


def mode_de(project_id: int, principal_type: str, principal_id: str) -> str:
    """`inherit` si une arête VIVANTE prête les clés du projet à ce principal."""
    from ..db import grants as db_grants
    for e in db_grants.edges_for(ref_projet(project_id), [(principal_type, principal_id)]):
        if e.get("revoked_at") is None and e.get("resource_kind") == RESOURCE_KIND:
            return INHERIT
    return OWN


def modes_du_projet(project_id: int) -> dict[tuple[str, str], str]:
    """`{(principal_type, principal_id): 'inherit'}` des arêtes vivantes — la lecture
    du partage (`op=get`) en une requête."""
    from ..db import grants as db_grants
    return {(e["grantee_kind"], str(e["grantee_id"])): INHERIT
            for e in db_grants.live_edges_for_resource(ref_projet(project_id))
            if e.get("resource_kind") == RESOURCE_KIND}


def declarer(project_id: int, principal_type: str, principal_id: str, mode: str,
             partageur: str) -> None:
    """Pose (`inherit`) ou retire (`own`) le prêt des clés à ce principal. Idempotent :
    l'arête vivante précédente est archivée avant d'en poser une neuve — c'est le
    partageur DU MOMENT qui borne le prêt."""
    from ..db import grants as db_grants
    ref = ref_projet(project_id)
    db_grants.revoke_edges(ref, principal_type, str(principal_id))
    if mode == INHERIT:
        db_grants.insert_grant(
            resource_id=ref, resource_kind=RESOURCE_KIND,
            grantor_kind="user", grantor_id=partageur,
            grantee_kind=principal_type, grantee_id=str(principal_id),
            source="manual", created_by=partageur)
