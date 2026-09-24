"""Déplace des clés de connecteur du niveau ORG d'une org vers le niveau MEMBRE d'un
de ses membres — la clé partagée devient la clé personnelle de ce membre.

Pourquoi un script : **restreindre un connecteur, c'est placer la clé au bon niveau**
(ADR 0053 D1). La réservation d'un connecteur à une partie des membres d'une org
(`connector_acl`) disparaît ; une org qui s'en servait pour réserver ses clés à UNE
personne doit voir ces clés devenir les clés personnelles de cette personne AVANT le
retrait, sinon elles s'ouvrent à tous ses membres.

Ce qu'il fait, pour chaque ligne de coffre `(org, <org>, <connecteur>, <compte>)` :

1. la DÉCHIFFRE avec l'AAD du niveau org (`credentials_store._aad`) ;
2. la RÉÉCRIT au niveau membre (`member`, `member_id(org, sub)`) par l'entonnoir unique
   du coffre (`credentials_store._upsert`), donc re-chiffrée avec l'AAD du niveau
   membre, `set_by` inchangé, `meta` inchangé plus une trace `_deplacement` ;
3. SUPPRIME la ligne d'org par `credentials_store._delete` (son instance s'archive,
   la nouvelle ligne reçoit la sienne — L6 pièce 2) ;
4. RELIT ce qu'il vient d'écrire avant de valider : déchiffrement au niveau membre,
   empreinte SHA-256 égale à celle du secret d'origine (on compare des empreintes,
   jamais des valeurs), ligne d'org absente. Un écart → ROLLBACK, sortie non nulle.

Tout dans **UNE transaction** : les lignes d'org visées sont verrouillées
(`FOR UPDATE`), `statement_timeout` et `lock_timeout` bornés. Pas de DDL.

**Dry-run par défaut** : il REJOUE toutes les écritures et la relecture dans la
transaction, puis l'ANNULE (même principe que `repoint_stale_org0_selection`) — une
contrainte qui casserait en vrai casse déjà ici. `--apply` valide.

Refus (sortie non nulle, rien d'écrit en `--apply`) :
- le sub n'est pas membre de l'org ;
- un connecteur demandé n'a pas de clé d'org, ou n'accepte pas de clé personnelle
  (`providers.require_credential("member", …)`) ;
- le membre a déjà une clé du même connecteur et le déplacement ne peut pas
  coexister avec elle (mono-compte, ou ligne sans nom d'un multi-compte) — le script
  n'écrase ni ne renomme JAMAIS la clé du membre ; un compte nommé déjà pris est
  RENOMMÉ côté clé déplacée (`<compte>-org-<org>`, `principal-org-<org>` pour la
  ligne sans nom), et la sortie le dit ;
- un objet désigne encore une instance d'org déplacée (lien de projet
  `instance_ref`/`identity_ref`, arête `grants`, déclencheur ou flotte qui la
  nomme) : il casserait après le déplacement. `--force-orphan-bindings` passe outre,
  en le disant.

Ne sont PAS repris : le partage de l'instance d'org (`share_mode`/`share_down`/
`share_side`) — prêter une clé devenue personnelle n'est pas l'intention ; la sortie
compte les lignes qui en portaient un.

**Aucun secret n'est affiché ni journalisé**, y compris en erreur : la sortie ne
contient que des comptes, des noms de connecteur et de compte, et (avec
`--show-members`) des subs.

    python -m scripts.deplacer_cles_org_vers_membre --org <id> --sub <sub> \\
        --connectors apify,attio,notion              # dry-run
    python -m scripts.deplacer_cles_org_vers_membre --org <id> --sub <sub> \\
        --connectors apify,attio,notion --apply       # valide

Sorties : 0 = fait (ou dry-run sans refus) ; 2 = paramètres ou sub hors de l'org ;
3 = refus (connecteur, collision, références) ; 4 = relecture en échec (ROLLBACK).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from oto_mcp import credentials_store as cs, crypto, instance_refs, providers
from oto_mcp.db import _connect, connector_instances

STATEMENT_TIMEOUT = "15s"
LOCK_TIMEOUT = "5s"

OK, ERR_PARAMS, ERR_REFUS, ERR_RELECTURE = 0, 2, 3, 4


class RelectureEchouee(RuntimeError):
    """La relecture avant validation n'a pas retrouvé ce qui vient d'être écrit."""


@dataclass
class Deplacement:
    connector: str
    source: str               # compte côté org ('' = mono-compte)
    cible: str                # compte côté membre
    renomme: bool = False


@dataclass
class Plan:
    deplacements: list = field(default_factory=list)
    refus: list = field(default_factory=list)        # (connecteur, motif)
    partages_non_repris: int = 0


def _empreinte(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _meta(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    return json.loads(raw or "{}")


def _table_existe(conn, nom: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) AS t", (nom,)).fetchone()
    return row["t"] is not None


def _compte_libre(base: str, pris: set, org: int) -> str:
    cible, i = f"{base}-org-{org}", 2
    while cible in pris:
        cible, i = f"{base}-org-{org}-{i}", i + 1
    return cible


def _planifier(conn, org: int, eid_membre: str, connectors: list) -> Plan:
    from oto_mcp.connectors import cardinality
    plan = Plan()
    for connector in connectors:
        try:
            providers.require_credential(cs.MEMBER, connector)
        except ValueError:
            plan.refus.append((connector, "n'accepte pas de clé personnelle"))
            continue
        lignes = conn.execute(
            "SELECT account, share_mode, share_down, share_side FROM connector_credentials "
            "WHERE entity_type = %s AND entity_id = %s AND connector = %s "
            "ORDER BY account FOR UPDATE",
            (cs.ORG, str(org), connector)).fetchall()
        if not lignes:
            plan.refus.append((connector, "aucune clé au niveau de l'org"))
            continue
        existants = {r["account"] for r in conn.execute(
            "SELECT account FROM connector_credentials "
            "WHERE entity_type = %s AND entity_id = %s AND connector = %s FOR UPDATE",
            (cs.MEMBER, eid_membre, connector)).fetchall()}
        multi = cardinality.is_multi_account(connector, org)
        if existants and (not multi or "" in existants):
            plan.refus.append((connector, "le membre a déjà une clé qui ne peut pas "
                               "coexister avec la clé déplacée (mono-compte ou ligne "
                               "sans nom) — à régler à la main"))
            continue
        pris = set(existants)
        for r in lignes:
            source = r["account"]
            cible, renomme = source, False
            if existants:  # multi-compte, uniquement des comptes nommés côté membre
                base = source or "principal"
                cible, renomme = base, bool(source == "")
                if cible in pris:
                    cible, renomme = _compte_libre(base, pris, org), True
            pris.add(cible)
            plan.deplacements.append(Deplacement(connector, source, cible, renomme))
            if (r["share_mode"] not in (None, "", "inherited")
                    or r["share_down"] or r["share_side"]):
                plan.partages_non_repris += 1
    return plan


def _impact_membres(conn, org: int, sub: str, connectors: list) -> tuple[list, dict]:
    """(autres membres de l'org, {connecteur: subs qui résolvent la clé d'org
    aujourd'hui hors la cible}). Aujourd'hui = org_admin, ou autorisé par
    `connector_acl` si le connecteur y est restreint, sinon tout membre."""
    autres = conn.execute(
        "SELECT sub, org_role FROM org_members WHERE org_id = %s AND sub <> %s ORDER BY sub",
        (org, sub)).fetchall()
    acl = _table_existe(conn, "connector_acl")
    par_connecteur = {}
    for connector in connectors:
        autorises = None
        if acl:
            regles = conn.execute(
                "SELECT principal_type, principal_id FROM connector_acl "
                "WHERE scope_type = 'org' AND scope_id = %s AND connector = %s",
                (str(org), connector)).fetchall()
            if regles:
                autorises = {r["principal_id"] for r in regles if r["principal_type"] == "user"}
                groupes = [r["principal_id"] for r in regles if r["principal_type"] == "group"]
                if groupes:
                    autorises |= {r["sub"] for r in conn.execute(
                        "SELECT gm.sub FROM org_group_members gm "
                        "JOIN org_groups g ON g.id = gm.group_id "
                        "WHERE g.org_id = %s AND gm.group_id::text = ANY(%s)",
                        (org, groupes)).fetchall()}
        par_connecteur[connector] = [
            r["sub"] for r in autres
            if autorises is None or r["org_role"] == "org_admin" or r["sub"] in autorises]
    return [r["sub"] for r in autres], par_connecteur


def _references(conn, org: int, deplacements: list) -> dict:
    """Objets qui désignent encore une instance d'org déplacée : {type: nombre}."""
    refs = set()
    for d in deplacements:
        refs.add(instance_refs.make_org_ref(org, d.connector, d.source))
        iid = connector_instances.instance_id_for_vault_row(
            cs.ORG, str(org), d.connector, d.source, conn=conn)
        if iid is not None:
            refs.add(instance_refs.make_instance_ref(iid))
    refs = sorted(refs)
    compte = {}
    if not refs:
        return compte
    if _table_existe(conn, "project_links"):
        compte["liens de projet"] = conn.execute(
            "SELECT count(*) AS n FROM project_links "
            "WHERE config->>'instance_ref' = ANY(%s) OR identity_ref = ANY(%s)",
            (refs, refs)).fetchone()["n"]
    if _table_existe(conn, "grants"):
        compte["arêtes grants"] = conn.execute(
            "SELECT count(*) AS n FROM grants WHERE resource_kind = 'connector_instance' "
            "AND revoked_at IS NULL AND resource_id = ANY(%s)", (refs,)).fetchone()["n"]
    for table, libelle in (("runner_triggers", "déclencheurs"), ("runner_fleets", "flottes")):
        if _table_existe(conn, table):
            compte[libelle] = conn.execute(
                f"SELECT count(*) AS n FROM {table} t WHERE EXISTS ("
                "SELECT 1 FROM unnest(%s::text[]) AS r(ref) "
                "WHERE strpos(row_to_json(t)::text, r.ref) > 0)", (refs,)).fetchone()["n"]
    return {k: v for k, v in compte.items() if v}


def _deplacer(conn, org: int, sub: str, eid_membre: str, deplacements: list) -> None:
    """Écrit puis relit. Lève `RelectureEchouee` sur tout écart (l'appelant annule)."""
    trace = {"from": f"org:{org}", "at": datetime.now(timezone.utc).isoformat(),
             "by": "script:deplacer_cles_org_vers_membre"}
    empreintes = {}
    for d in deplacements:
        row = conn.execute(
            "SELECT secret_enc, meta, set_by FROM connector_credentials "
            "WHERE entity_type = %s AND entity_id = %s AND connector = %s AND account = %s",
            (cs.ORG, str(org), d.connector, d.source)).fetchone()
        if conn.execute(
                "SELECT 1 FROM connector_credentials WHERE entity_type = %s "
                "AND entity_id = %s AND connector = %s AND account = %s",
                (cs.MEMBER, eid_membre, d.connector, d.cible)).fetchone():
            raise RelectureEchouee(f"{d.connector} : le compte cible est apparu pendant le déplacement")
        secret = crypto.decrypt(row["secret_enc"], cs._aad(cs.ORG, str(org), d.connector, d.source))
        empreintes[(d.connector, d.cible)] = _empreinte(secret)
        meta = _meta(row["meta"])
        meta["_deplacement"] = dict(trace)
        cs._upsert(conn, cs.MEMBER, eid_membre, d.connector, d.cible, secret,
                   row["set_by"], meta)
        cs._delete(conn, cs.ORG, str(org), d.connector, d.source)
        del secret
    for d in deplacements:
        row = conn.execute(
            "SELECT secret_enc FROM connector_credentials WHERE entity_type = %s "
            "AND entity_id = %s AND connector = %s AND account = %s",
            (cs.MEMBER, eid_membre, d.connector, d.cible)).fetchone()
        if row is None:
            raise RelectureEchouee(f"{d.connector} : ligne membre absente après écriture")
        try:
            relu = crypto.decrypt(row["secret_enc"],
                                  cs._aad(cs.MEMBER, eid_membre, d.connector, d.cible))
        except Exception:
            raise RelectureEchouee(f"{d.connector} : indéchiffrable au niveau membre") from None
        if _empreinte(relu) != empreintes[(d.connector, d.cible)]:
            raise RelectureEchouee(f"{d.connector} : empreinte différente de l'original")
        del relu
        if conn.execute(
                "SELECT 1 FROM connector_credentials WHERE entity_type = %s "
                "AND entity_id = %s AND connector = %s AND account = %s",
                (cs.ORG, str(org), d.connector, d.source)).fetchone():
            raise RelectureEchouee(f"{d.connector} : la ligne d'org est toujours là")


def deplacer(org: int, sub: str, connectors: list, *, apply: bool = False,
             show_members: bool = False, force_orphan_bindings: bool = False,
             out: Callable[[str], None] = print) -> int:
    connectors = sorted({c.strip() for c in connectors if c and c.strip()})
    if not connectors:
        out("aucun connecteur demandé (--connectors a,b,c)")
        return ERR_PARAMS
    eid_membre = cs.member_id(org, sub)
    mode = "APPLY" if apply else "DRY-RUN"
    with _connect() as conn:
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        membre = conn.execute(
            "SELECT org_role FROM org_members WHERE org_id = %s AND sub = %s",
            (org, sub)).fetchone()
        if membre is None:
            conn.rollback()
            out(f"[{mode}] refus : ce sub n'est pas membre de l'org {org}")
            return ERR_PARAMS

        plan = _planifier(conn, org, eid_membre, connectors)
        autres, perdent = _impact_membres(conn, org, sub, connectors)
        refs = _references(conn, org, plan.deplacements)

        out(f"[{mode}] org {org} → clés personnelles d'un membre ({membre['org_role']}), "
            f"{len(connectors)} connecteur(s) demandé(s)")
        for d in plan.deplacements:
            renom = " (renommé : compte déjà pris chez le membre)" if d.renomme else ""
            out(f"  {d.connector} : compte {d.source or '(sans nom)'!s} → "
                f"{d.cible or '(sans nom)'}{renom}")
        for connector, motif in plan.refus:
            out(f"  REFUS {connector} : {motif}")
        if plan.partages_non_repris:
            out(f"  partage d'instance non repris sur {plan.partages_non_repris} ligne(s) "
                "(une clé personnelle ne se prête pas par ce geste)")
        out(f"  autres membres de l'org : {len(autres)}")
        for connector in connectors:
            if perdent.get(connector):
                out(f"  {connector} : {len(perdent[connector])} membre(s) résolvent la clé "
                    "d'org aujourd'hui et la perdront")
        if show_members:
            for s in autres:
                out(f"    membre : {s}")
        if refs:
            out("  objets qui désignent une instance d'org déplacée : "
                + ", ".join(f"{k} {v}" for k, v in sorted(refs.items())))

        refuse = bool(plan.refus) or (bool(refs) and not force_orphan_bindings)
        if refs and force_orphan_bindings:
            out("  --force-orphan-bindings : ces objets désigneront une instance archivée")
        if apply and refuse:
            conn.rollback()
            out(f"[{mode}] refusé, rien n'est écrit"
                + (" (références : --force-orphan-bindings pour passer outre)" if refs else ""))
            return ERR_REFUS

        try:
            _deplacer(conn, org, sub, eid_membre, plan.deplacements)
        except RelectureEchouee as e:
            conn.rollback()
            out(f"[{mode}] relecture en échec, ROLLBACK : {e}")
            return ERR_RELECTURE

        if apply:
            conn.commit()
            out(f"[{mode}] {len(plan.deplacements)} clé(s) déplacée(s), relues, commitées")
            return OK
        conn.rollback()
        out(f"[{mode}] {len(plan.deplacements)} déplacement(s) rejoué(s) et relu(s) puis "
            "ANNULÉ(S) — --apply pour committer")
        return ERR_REFUS if refuse else OK


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--org", type=int, required=True)
    p.add_argument("--sub", required=True)
    p.add_argument("--connectors", required=True, help="liste séparée par des virgules")
    p.add_argument("--apply", action="store_true", help="valide (défaut : dry-run)")
    p.add_argument("--show-members", action="store_true")
    p.add_argument("--force-orphan-bindings", action="store_true")
    a = p.parse_args(argv)
    return deplacer(a.org, a.sub, a.connectors.split(","), apply=a.apply,
                    show_members=a.show_members,
                    force_orphan_bindings=a.force_orphan_bindings)


if __name__ == "__main__":
    sys.exit(main())
