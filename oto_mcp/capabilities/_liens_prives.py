"""Un tableau ou une procédure lié à un projet PRIVÉ devient privé lui aussi.

Le défaut : lier (`oto_project op=link`) une procédure ou un tableau possédé par l'ORG à
un projet privé — possédé par une personne ou une équipe, et partagé à aucune org — le
DÉPLACE chez le propriétaire du projet, et donne aux bénéficiaires du projet le même
accès sur lui. Avant, le projet était privé mais ce qu'il liait restait lisible par
TOUS les membres de l'org : un membre sans aucun partage du projet lisait ses
procédures et ses tableaux par le catalogue de l'org, et rien ne le disait.

Le geste se rejoue : un lien déjà posé repasse par la même règle quand on le relie
(`link_status: unchanged`), ce qui ramène un projet existant dans le droit chemin sans
outil à part.

Ce qui reste org, et le dit (`privacy.kept_org_wide` + `reason`, jamais un refus — le
lien, lui, est posé) :

- `org_wide_requested` : l'appelant a passé `org_wide=true` ;
- `linked_by_other_projects` : un autre projet lie la même cible ; la rendre privée la
  retirerait à ce projet-là ;
- `needs_org_admin` : sortir une ressource de l'org est un transfert, réservé à qui
  administre l'org (`ownership.can_transfer`) ;
- `agents_depend_on_it` (procédure) : un agent hébergé ACTIF de l'org l'exécute et ne
  la résoudrait plus. Un agent lit sa procédure par slug, dans la copie personnelle de
  SON porteur puis dans l'org, jamais dans une équipe (`_procedure_du_travail`) : la
  déplacer chez une équipe coupe tous ses agents, chez une personne coupe ceux des
  autres porteurs ;
- `slug_taken_at_owner` / `name_taken_at_owner` : le propriétaire du projet a déjà une
  procédure de ce slug (le déplacement la suffixerait, et ses agents liraient l'autre)
  ou un tableau de ce nom.

⚠️ Limite connue : une procédure qu'une AUTRE procédure cite entre accents graves (la
chaîne d'un agent) n'est pas cherchée. Un agent d'un autre porteur qui l'atteindrait
par une chaîne ne la lirait plus.

Le code des raisons est le contrat ; la phrase ne l'est pas."""
from __future__ import annotations

import logging
from typing import Optional

from .. import db, org_store, ownership

logger = logging.getLogger(__name__)

_RTYPE = {"tableau": ownership.TYPE_RESSOURCE_DATASTORE,
          "procedure": ownership.TYPE_RESSOURCE_PROCEDURE}


def _grants_vivants(project_id: int) -> list[dict]:
    return [g for g in db.list_resource_grants("project", str(project_id))
            if not g.get("expired")]


def projet_prive(row: dict) -> bool:
    """Privé = possédé par une personne ou une équipe, et partagé à aucune org."""
    if row.get("owner_type") not in ("user", "group"):
        return False
    return not any(g["principal_type"] == "org" for g in _grants_vivants(int(row["id"])))


def _garde(reason: str) -> dict:
    return {"kept_org_wide": True, "reason": reason}


def _agents_qui_l_executent(org_id: Optional[int], slug: str) -> list[dict]:
    if org_id is None:
        return []
    return [t for t in db.list_triggers(int(org_id))
            if t.get("enabled") and (t.get("procedure") or "") == slug]


def _obstacle_procedure(ref: str, owner_type: str, owner_id: str) -> Optional[str]:
    instr = org_store.get_instruction_by_id(int(ref))
    if instr is None:
        return None
    agents = _agents_qui_l_executent(instr.get("org_id"), instr["slug"])
    if owner_type == "group" and agents:
        return "agents_depend_on_it"
    if owner_type == "user" and any(str(t.get("sub")) != owner_id for t in agents):
        return "agents_depend_on_it"
    if not org_store.instruction_slug_libre(owner_type, owner_id, instr["slug"]):
        return "slug_taken_at_owner"
    return None


def _obstacle_tableau(ref: str, owner_type: str, owner_id: str) -> Optional[str]:
    row = db.get_datastore_by_id(int(ref))
    if row is None:
        return None
    if db.datastore_name_taken(owner_type, owner_id, row["datastore"]):
        return "name_taken_at_owner"
    return None


def rendre_prive(sub: str, row: dict, target_type: str, target_ref: str, *,
                 org_wide: bool = False) -> Optional[dict]:
    """Applique la règle au lien `target_type`/`target_ref` du projet `row`, juste posé.

    Rend None quand il n'y a rien à dire (projet non privé, cible qui n'est pas à
    l'org) ; sinon le compte rendu servi sous `privacy`."""
    rtype = _RTYPE.get(target_type)
    if rtype is None or not str(target_ref).isdigit() or not projet_prive(row):
        return None
    ref = str(target_ref)
    owner = ownership.owner_of(rtype, ref)
    if owner is None or owner[0] != "org":
        return None
    if org_wide:
        return _garde("org_wide_requested")
    pid = int(row["id"])
    if any(p != pid for p in db.projects_linking(target_type, ref)):
        return _garde("linked_by_other_projects")
    if not ownership.can_transfer(sub, rtype, ref):
        return _garde("needs_org_admin")
    dest = (str(row["owner_type"]), str(row["owner_id"]))
    obstacle = (_obstacle_procedure if target_type == "procedure"
                else _obstacle_tableau)(ref, *dest)
    if obstacle:
        return _garde(obstacle)
    ownership.transfer(rtype, ref, *dest)
    if target_type == "tableau" and dest[0] == "user" and row.get("context_org_id"):
        # Un tableau perso se range dans l'org où il vit (oto#160) : celle du projet,
        # sinon il ne sortirait que dans la maison de son nouveau propriétaire.
        db.ranger_tableau(int(ref), int(row["context_org_id"]))
    partages = []
    for g in _grants_vivants(pid):
        if (g["principal_type"], str(g["principal_id"])) == dest:
            continue
        # Une procédure personnelle ne s'écrit que par son propriétaire : aucun chemin
        # servi n'édite la copie d'un autre (`op=set` écrit chez soi ou à l'org). Un
        # partage `editor` y serait lettre morte : on partage en lecture, et on le dit.
        ecrit = (g.get("role") in ("editor", "manager") or g.get("permission") == "write")
        role = "editor" if ecrit and target_type == "tableau" else "viewer"
        ownership.grant(rtype, ref, g["principal_type"], str(g["principal_id"]),
                        role=role, granted_by=sub)
        partages.append({"principal_type": g["principal_type"],
                         "principal_id": str(g["principal_id"]), "role": role})
    db.log_project_activity(pid, sub, "project.link_private",
                            f"{target_type}:{ref} → {dest[0]}:{dest[1]}")
    return {"made_private": True, "moved_from": {"owner_type": owner[0],
                                                 "owner_id": owner[1]},
            "owner": {"owner_type": dest[0], "owner_id": dest[1]},
            "shared_with": partages}
