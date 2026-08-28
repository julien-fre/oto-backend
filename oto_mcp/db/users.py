"""Utilisateurs : identité, migration tenant Logto, accès plateforme & quota, rôle, avatar, profil onboarding.

Extrait de l'ex-monolithe `db.py` (barreau final). Fonctions de domaine — la
plomberie est dans `_conn`. Ré-exporté par `db/__init__`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, Iterator, Optional

import psycopg

logger = logging.getLogger(__name__)

from ._conn import _connect


class OnboardingIncomplet(RuntimeError):
    """Une première inscription n'a pas produit ce qu'elle promet.

    Le compte existe (la ligne `users` est écrite et validée), mais l'un de ses deux
    effets de naissance a échoué : l'org maison, ou l'invitation d'org à honorer.
    Ces deux échecs étaient avalés — `except Exception: pass` — jusqu'au 2026-08-27
    (`docs/silences-2026-08-27.md`, sites B8 et B9). Un compte sans org maison ne
    plante pas là où il naît : il plante plus tard, ailleurs, et sans cause
    remontable (cf. `backfill_member_scope`, qui logue « pas d'org maison pour %s »
    sans jamais pouvoir dire pourquoi).

    ⚠️ **Ce que ce refus ne fait PAS** : annuler la ligne `users`. Elle est validée
    par sa propre transaction avant que les effets ne tournent, et le gate
    `inserted` ne se re-déclenche pas au login suivant — donc un échec DURABLE
    laisse un compte sans espace après ce seul cri. Le rattrapage reste
    `org_store.backfill_personal_orgs`, rejoué à chaque boot. Rendre la naissance
    ATOMIQUE est un lot à part, hors du périmètre de la correction des silences.
    """

    def __init__(self, sub: str, manques: list):
        self.sub, self.manques = sub, list(manques)
        super().__init__(
            f"inscription incomplète pour {sub} : " + ", ".join(self.manques) +
            " — le compte existe mais pas ce qui devait naître avec lui")


def upsert_user(sub: str, email: Optional[str] = None, name: Optional[str] = None,
                iss: Optional[str] = None) -> None:
    """Create the user row if missing, refresh email/name if known.

    Le `(xmax = 0)` distingue insert/update sans SELECT préalable : 0 sur une ligne
    fraîchement insérée, ≠ 0 sur un UPDATE — ce qui permet de ne déclencher les
    effets de première inscription (réconciliation d'invitation, org maison) qu'au
    vrai INSERT.
    """
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO users (sub, email, name)
            VALUES (%s, %s, %s)
            ON CONFLICT(sub) DO UPDATE SET
                email = COALESCE(EXCLUDED.email, users.email),
                name  = COALESCE(EXCLUDED.name,  users.name),
                updated_at = NOW()
            RETURNING (xmax = 0) AS inserted
            """,
            (sub, email, name),
        ).fetchone()
    # Les DEUX effets de première inscription sont tentés, PUIS l'échec est rendu :
    # que l'un tombe ne dispense pas de l'autre, et l'erreur finale dit lesquels ont
    # manqué. Ils n'étaient ni journalisés ni remontés jusqu'au 2026-08-27 (sites B8
    # et B9 de `docs/silences-2026-08-27.md`) — un compte naissait alors à moitié, et
    # tout ce qui en dépendait échouait plus tard, ailleurs, sans cause remontable.
    manques: list = []
    if row and row.get("inserted") and email:
        # Réconciliation invitation↔signup : un invité d'org qui s'inscrit (par
        # n'importe quel chemin, pas seulement le lien /invite) voit son invitation
        # d'org en attente honorée par l'email vérifié → il rejoint directement
        # l'org au lieu de rester avec une invitation orpheline. Synchrone (une
        # fois, au 1er insert).
        try:
            from .. import org_store
            org_store.reconcile_signup_with_invitation(sub, email)
        except Exception:
            logger.error("upsert_user: invitation d'org NON honorée au signup "
                         "(sub=%s email=%s) — l'invité ne rejoint pas son org",
                         sub, email, exc_info=True)
            manques.append("reconcile_signup_with_invitation")
    if row and row.get("inserted"):
        # Suppression du perso (otomata-private) : tout user a TOUJOURS une org maison.
        # Si l'inscription ne l'a pas déjà rattaché à une org (invitation d'org
        # ci-dessus), on lui crée son espace. Idempotent, hors gate email.
        try:
            from .. import org_store
            org_store.ensure_personal_org(sub, email=email, name=name)
        except Exception:
            logger.error("upsert_user: org maison NON créée (sub=%s email=%s) — "
                         "le compte naîtrait sans espace", sub, email, exc_info=True)
            manques.append("ensure_personal_org")
    if manques:
        raise OnboardingIncomplet(sub, manques)
    # Bascule de tenant (B1, otomata#35) : sur un login du NOUVEAU tenant, fusionner
    # l'ancien compte (même email) → ce sub. Gaté par env `OTO_MCP_TENANT_MIGRATION_ISS`
    # (dormant hors fenêtre de bascule). Idempotent, best-effort, à chaque login
    # new-tenant (pas que au 1er insert → couvre les retries / l'ordre des logins).
    # ⚠️ SÉCU (account takeover) : la décision de merge se prend sur l'email
    # AUTORITATIF lu de Logto (Management API), JAMAIS sur le claim email/email_verified
    # du token — un token forgé pourrait revendiquer l'email d'autrui pour absorber son
    # compte (rôle, coffre). reconcile_tenant_migration récupère lui-même cet email ;
    # le claim `email` n'est passé que comme PRÉ-FILTRE cheap (éviter un appel Logto à
    # chaque requête quand rien ne matche).
    if iss:
        _mig = os.environ.get("OTO_MCP_TENANT_MIGRATION_ISS", "").strip().rstrip("/")
        if _mig and iss.rstrip("/") == _mig:
            try:
                reconcile_tenant_migration(sub, email_hint=email)
            # noqa: SILENT — réconciliation de tenant dormante (gate env), idempotente au login suivant
            except Exception:
                pass


def get_user(sub: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE sub = %s", (sub,)).fetchone()
        return dict(row) if row else None


_MAX_EMAILS_BY_SUBS = 200


def emails_by_subs(subs: list) -> dict:
    """`{sub: email}` pour un LOT de subs, en UNE requête.

    Sert les surfaces qui rendent « qui a fait ce geste » à partir d'un journal
    (`tool_calls.email` n'est pas peuplé à l'insert : le sink ne connaît que le
    `sub` du JWT). Résoudre à la LECTURE plutôt qu'à l'écriture vaut aussi pour
    les lignes déjà en base — aucun backfill — et garde le chemin chaud à zéro
    requête. Un sub inconnu (compte supprimé) est simplement absent. Lot borné."""
    wanted = [str(s) for s in dict.fromkeys(subs or []) if s][:_MAX_EMAILS_BY_SUBS]
    if not wanted:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT sub, email FROM users WHERE sub = ANY(%s)", (wanted,)
        ).fetchall()
        return {r["sub"]: r["email"] for r in rows if r.get("email")}


# --- Bascule de tenant Logto (B1, otomata#35) -------------------------------
# Tables d'APPARTENANCE `(scope_id, sub)` portant un `is_active` unique par sub
# (index partiel `*_one_active`) : elles ne peuvent PAS passer par l'UPDATE nu de
# `_SUB_COLUMNS` — cf. l'étape 2 bis de `migrate_sub`. Toute nouvelle table de ce
# genre s'ajoute ICI (garde-fou : `tests/test_migrate_sub_inventory.py`).
_MEMBERSHIP_TABLES = (("org_members", "org_id"), ("org_group_members", "group_id"))

# Tables dont la **clé primaire CONTIENT** une colonne de sub : l'`UPDATE` nu de
# `_SUB_COLUMNS` y lève `UniqueViolation` dès que les deux comptes portent la même
# ligne (même canal opéré, même prêt) — et cette exception fait échouer TOUT le merge,
# pas seulement cette table. Même patron que les appartenances : on jette la ligne de
# l'ancien (le canonique est le nouveau), puis on repointe.
#
# ⚠️ Ces quatre colonnes portent des données que personne ne peut recréer de mémoire :
# le canal de messagerie opéré et les prêts de compte (ADR 0044 §H / #55). Elles
# étaient **hors inventaire** — donc emportées par le `DELETE FROM users` de l'étape 4,
# en silence. Trouvées le 13/08 en dérivant les FK `ON DELETE CASCADE` du DDL plutôt
# qu'en relisant la liste (garde-fou `tests/test_migrate_sub_cascade.py`).
# `(table, colonne de sub, reste de la PK)`.
_PK_SUB_TABLES = (
    ("unipile_operated_accounts", "sub", ("provider",)),
    ("connector_account_grants", "owner_sub", ("provider", "grantee_sub")),
    ("connector_account_grants", "grantee_sub", ("owner_sub", "provider")),
    # Dossier du 23/08 (les colonnes à sub que le merge ABANDONNAIT — cf. le tripwire
    # `test_migrate_sub_sub_bearing_columns_are_triaged`) :
    # (`legal_acceptances` était ici jusqu'au 28/08 ; elle est passée en simple
    #  repointage — voir `_SUB_COLUMNS`.)
    # - la réservation de connecteur (gouvernance d'équipe/org) visait un identifiant
    #   mort : le membre re-fusionné perdait l'accès réservé. `principal_id` mélange
    #   group_id numérique et sub — un sub Logto n'est jamais un entier, l'UPDATE
    #   `col=old_sub` ne peut toucher que les lignes user (même argument que
    #   `resource_grants.principal_id`).
    ("connector_acl", "principal_id",
     ("scope_type", "scope_id", "connector", "principal_type")),
    # - une option offerte (comp) cessait de s'appliquer au compte fusionné — le
    #   symptôme nommé par la carte CLAUDE.md. `entity_id` mélange org_id numérique
    #   et sub : même argument de non-collision.
    ("option_comps", "entity_id", ("entity_type", "option")),
)

# Inventaire des colonnes keyed-by-sub à repointer (issue oto-backend#56). Plain
# UPDATE : le nouveau sub est frais → aucun conflit de PK, SAUF user_account_profile
# (PK sub), les appartenances ci-dessus et connector_credentials (coffre user),
# traités à part.
_SUB_COLUMNS = [
    # ⚠️ Chaque entrée DOIT exister en DB : la boucle fait des UPDATE nus dans UNE
    # transaction — une table absente fait échouer TOUT le merge (vécu : `user_grants`,
    # droppée par 0044 §F mais restée listée → migrate_sub cassé jusqu'au nettoyage
    # Phase H B1 du 10/07, qui a aussi sorti les reliques datastore `user_datastores.sub`
    # et `datastore_shares` : colonnes mortes, plus rien ne les lit, DROP en B2).
    # données de l'user
    ("usage", "sub"), ("tool_calls", "sub"), ("usage_signals", "sub"),
    ("user_disabled_tools", "sub"), ("user_enabled_tools", "sub"),
    ("org_members", "sub"), ("org_group_members", "sub"),
    ("user_api_tokens", "sub"), ("unipile_accounts", "sub"), ("unipile_pending", "sub"),
    # Le PROPRIÉTAIRE d'un canal opéré : hors PK `(sub, provider)`, donc UPDATE nu
    # (le TITULAIRE, lui, est en PK → `_PK_SUB_TABLES`).
    ("unipile_operated_accounts", "owner_sub"),
    # ressources possédées + grants (ère ownership 0030/0042/0048 — ajoutées Phase H B1 :
    # l'inventaire n'avait jamais suivi, une bascule de tenant orphelinait les ressources
    # user-owned et les grants nominatifs). `owner_id`/`principal_id` mélangent sub et
    # ids numériques d'org/groupe : un sub Logto n'est jamais un entier → l'UPDATE nu
    # `col=old_sub` ne peut toucher que les lignes user.
    ("user_datastores", "owner_id"), ("projects", "owner_id"),
    ("resource_grants", "principal_id"), ("resource_grants", "granted_by"),
    # `guides` est gelée depuis le lot M1 (ses lignes vivent dans `nodes`) mais elle
    # existe encore et la prod y écrit pendant la fenêtre : les DEUX se repointent,
    # sinon une bascule de tenant orphelinerait ce que la conversion recopiera après.
    ("guides", "owner_id"), ("nodes", "owner_id"),
    # l'HISTORIQUE de la personne (dossier du 23/08 — ces lignes survivaient au merge
    # rattachées à un identifiant mort, donc invisibles au compte fusionné : déroulés
    # et activité perdus de vue, déclencheurs orphelins) :
    ("runs", "sub"), ("project_activity", "sub"), ("runner_triggers", "sub"),
    ("tool_calls", "effective_sub"),
    # L'acceptation des documents légaux suit la personne : sans repointage, le
    # compte fusionné se voyait redemander des CGU déjà acceptées. Elle était traitée
    # comme une clé (déduplication sur `(sub, doc_slug)`) tant que la table portait
    # UNE ligne par doc ; depuis #487 c'est un HISTORIQUE, sans plus aucune unicité —
    # un UPDATE nu est donc à la fois suffisant et le seul geste correct : la
    # déduplication SUPPRIMERAIT des preuves de consentement pour cause de doublon,
    # alors que deux acceptations du même document par deux comptes de la même
    # personne sont deux faits distincts, tous deux vrais.
    ("legal_acceptances", "sub"),
    # attribution (soft)
    ("projects", "created_by"),
    ("orgs", "created_by"),
    ("org_invitations", "invited_by"), ("org_invitations", "accepted_sub"),
    ("org_groups", "created_by"), ("org_instructions", "set_by"),
    ("org_instruction_revisions", "set_by"), ("doctrine_library", "published_by"),
    # attribution (soft), dossier du 23/08 — qui a écrit/résolu/accordé quoi. Sans
    # repointage ces signatures pointaient un compte supprimé (affichage « inconnu »
    # au mieux, jointure vide au pire). `set_by` du coffre est HORS AAD (`_aad` =
    # entity/connector/account) : le repointer ne rend rien indéchiffrable.
    ("usage_signals", "resolved_by"), ("docs", "created_by"),
    ("project_files", "created_by"),
    ("doc_change_requests", "requested_by"), ("doc_change_requests", "resolved_by"),
    ("scheduled_emails", "created_by"), ("connector_credentials", "set_by"),
    ("connector_account_grants", "granted_by"), ("connector_acl", "granted_by"),
    ("option_comps", "granted_by"), ("grants", "created_by"),
]


def resolve_sub(sub: str) -> str:
    """Canonicalise un sub via sub_aliases (vieux token d'un tenant en drain →
    compte migré). Renvoie le sub inchangé si pas d'alias (cas normal).

    ⚠️ **Ne rattrape RIEN.** Rendre le sub d'entrée sur un hoquet DB, c'est servir la
    requête sous le compte d'AVANT migration — coffre, org, projets — et sans une
    ligne de trace (`docs/silences-2026-08-27.md`, site B5). « Je ne sais pas qui tu
    es » et « tu es celui-ci » ne sont pas la même réponse : la première se lève, elle
    ne se devine pas."""
    if not sub:
        return sub
    with _connect() as conn:
        row = conn.execute("SELECT new_sub FROM sub_aliases WHERE old_sub=%s", (sub,)).fetchone()
    return row["new_sub"] if row else sub


_ROLE_RANK = {"member": 0, "admin": 1, "super_admin": 2}


def _stronger_role(a: Optional[str], b: Optional[str]) -> str:
    """Le plus haut des deux rôles (une fusion n'enlève pas un privilège)."""
    ra, rb = _ROLE_RANK.get(a or "member", 0), _ROLE_RANK.get(b or "member", 0)
    return (a if ra >= rb else b) or "member"


def migrate_sub(old_sub: str, new_sub: str, *, operator_source: str = "") -> bool:
    """MERGE transactionnel ancien→nouveau compte (bascule de tenant, issue #56).
    Hérite les champs d'accès de l'ancien, repointe TOUTES les tables keyed-by-sub
    (les 3 FK `ON DELETE CASCADE` incluses, AVANT de supprimer l'ancien → pas de
    cascade destructrice) **et la marque d'espace personnel** (`orgs.personal_of`,
    hors inventaire car son index unique interdit l'UPDATE nu — étape 2 quater),
    supprime l'ancienne ligne users, pose l'alias. Idempotent
    (no-op si l'ancien sub n'existe plus). True si une migration a eu lieu.

    ⚠️ Le merge **par email** est borné à UN MÊME tenant (ADR 0052, R3 tranché le
    08/08). Entre deux émetteurs, il serait une fédération d'identités — ce que le §6
    interdit nommément : quiconque s'inscrit chez un tenant tiers sous l'adresse d'un
    autre absorberait son compte oto (rôle, orgs, coffre). Le garde-fou est ici plutôt
    qu'à l'appelant parce que c'est le SEUL endroit qui écrit `sub_aliases` : un alias
    cross-tenant ne peut donc pas naître d'un login, et `resolve_sub` ne peut pas en
    drainer un.

    `operator_source` est la SEULE porte cross-tenant, et elle n'est pas atteignable
    depuis un login : c'est un acte d'opérateur (déclarer un tenant qualifie ses subs
    ⟹ il faut repointer ce qui existait sous la forme nue). Elle ouvre le passage
    délibéré, jamais le merge automatique — l'appelant du chemin chaud
    (`reconcile_tenant_migration`) ne la renseigne pas, donc reste fermé. Ce qui la
    distingue d'un contournement : la décision « ces deux subs sont la même personne »
    est prise HORS du code, et la trace de qui l'a prise part au journal."""
    if not old_sub or not new_sub or old_sub == new_sub:
        return False
    from ..tenancy import current as _tenants
    if not _tenants().same_tenant(old_sub, new_sub) and not operator_source:
        logger.warning(
            "tenant migration REFUSÉE : %s et %s ne relèvent pas du même tenant "
            "(ADR 0052 §6 — pas de fédération d'identités entre tenants)",
            old_sub, new_sub)
        return False
    with _connect() as conn:
        old = conn.execute("SELECT * FROM users WHERE sub=%s", (old_sub,)).fetchone()
        if not old:
            return False  # déjà migré / inexistant
        # 1. fusionner le rôle SANS JAMAIS RÉTROGRADER : on prend le rôle le plus
        #    fort. ⚠️ Le naïf « hérite de l'ancien » downgrade le nouveau si l'ancien
        #    est un stub frais (member) re-fusionné par-dessus un compte établi
        #    (vécu 2026-06-23 : alexis super_admin repassé member).
        new = conn.execute(
            "SELECT role FROM users WHERE sub=%s", (new_sub,)
        ).fetchone() or {}
        conn.execute(
            """UPDATE users SET
                 role = %(role)s,
                 avatar_url = COALESCE(users.avatar_url, %(av)s), updated_at = NOW()
               WHERE sub = %(new)s""",
            {"role": _stronger_role(old["role"], new.get("role")),
             "av": old.get("avatar_url"), "new": new_sub},
        )
        # 2. user_account_profile (PK sub) : retirer le frais du new PUIS repointer
        #    l'ancien (garde l'historique). DELETE d'abord → pas de conflit PK.
        #    (La NOTE de l'user suit désormais par `("guides", "owner_id")` dans
        #    `_SUB_COLUMNS` — elle a quitté `user_agent_readme` avec l'ADR 0042.)
        conn.execute("DELETE FROM user_account_profile WHERE sub=%s", (new_sub,))
        conn.execute("UPDATE user_account_profile SET sub=%s WHERE sub=%s", (new_sub, old_sub))
        # 2 bis. APPARTENANCES (org_members / org_group_members) : elles ne se repointent
        #    pas en bloc, à cause de DEUX invariants que l'`UPDATE … SET sub=` de l'étape 3
        #    violerait. Vécu prod 2026-07-28 (julien@folk.app, 2 comptes) : merge en échec
        #    à CHAQUE requête de l'user, donc jamais fusionné + un round-trip Logto et un
        #    traceback par appel.
        #    (a) PK (org_id, sub) : si les deux comptes sont dans la MÊME org, repointer
        #        crée un doublon → on garde la ligne du compte canonique (le new, dont le
        #        rôle vient d'être fusionné au plus fort) et on jette celle de l'ancien.
        #    (b) index partiel `*_one_active` (≤ 1 appartenance ACTIVE par sub) : l'ancien
        #        apporte SA ligne active → deux actives après repointage. Le contexte
        #        courant appartient au compte canonique : les appartenances reprises
        #        arrivent INACTIVES (elles restent accessibles via `oto_use_org`).
        #        ⚠️ Désactivation CONDITIONNELLE : si le new n'a AUCUNE active (stub frais),
        #        celle de l'ancien est la seule → la garder, sinon le compte fusionné se
        #        retrouverait sans org maison.
        for table, key in _MEMBERSHIP_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE sub=%s AND {key} IN "
                f"(SELECT {key} FROM {table} WHERE sub=%s)", (old_sub, new_sub))
            conn.execute(
                f"UPDATE {table} SET is_active=FALSE WHERE sub=%s "
                f"AND EXISTS (SELECT 1 FROM {table} WHERE sub=%s AND is_active)",
                (old_sub, new_sub))
        # 2 ter. Colonnes de sub ENTRANT DANS UNE PK (canal opéré, prêts de compte) :
        #    même raison qu'en 2 bis — l'UPDATE nu violerait la PK quand les deux
        #    comptes portent la même ligne. On jette celle de l'ancien, puis on
        #    repointe. Sans ce pré-traitement, ces lignes partaient en CASCADE avec
        #    l'ancien compte à l'étape 4 : un canal de messagerie à reconnecter et
        #    des prêts à re-consentir, sans trace de ce qui a disparu.
        for table, col, reste in _PK_SUB_TABLES:
            meme_ligne = " AND ".join(f"a.{c} = b.{c}" for c in reste)
            conn.execute(
                f"DELETE FROM {table} a WHERE a.{col}=%s AND EXISTS ("
                f"SELECT 1 FROM {table} b WHERE b.{col}=%s AND {meme_ligne})",
                (old_sub, new_sub))
            conn.execute(f"UPDATE {table} SET {col}=%s WHERE {col}=%s",
                         (new_sub, old_sub))
        # 2 quater. La MARQUE d'espace personnel (`orgs.personal_of`) : hors de
        #    `_SUB_COLUMNS` parce qu'un UPDATE nu y violerait l'index unique
        #    `uq_orgs_personal_of` — et pas dans un cas tordu, dans le cas NOMINAL :
        #    le login crée le stub (donc son espace) AVANT que le merge ne le fusionne,
        #    si bien que les deux comptes en ont un.
        #    Sans ce traitement, la marque restait sur un identifiant qui n'existe plus.
        #    `get_personal_org` ne trouvait donc plus rien pour le compte survivant, et
        #    `ensure_personal_org` fabriquait un espace NEUF au boot suivant : deux
        #    organisations au même nom dans la liste de l'utilisateur, dont l'ancienne —
        #    celle qui porte son historique — n'est plus reconnue comme son espace.
        #    Constaté le 2026-08-14 sur 14 comptes, dont les 9 de la bascule de tenant
        #    du 13/08 (un espace en double par personne migrée).
        #    Règle : l'espace de l'ANCIEN compte porte l'historique ⟹ c'est lui qui
        #    reste l'espace personnel. Celui du nouveau est simplement DÉMARQUÉ — il
        #    redevient une organisation ordinaire, que son propriétaire peut supprimer.
        #    On ne l'archive pas ici : « cet espace n'a jamais servi » ne se décide pas
        #    au fond d'une transaction de merge, et un archivage automatique effacerait
        #    de la vue un espace qui, lui, aurait servi. L'avertissement ci-dessous le
        #    nomme pour que le ménage reste un acte explicite.
        perso_ancienne = conn.execute(
            "SELECT id FROM orgs WHERE personal_of=%s AND archived_at IS NULL",
            (old_sub,)).fetchone()
        if perso_ancienne:
            demarquees = conn.execute(
                "UPDATE orgs SET personal_of=NULL WHERE personal_of=%s "
                "AND archived_at IS NULL RETURNING id", (new_sub,)).fetchall()
            conn.execute("UPDATE orgs SET personal_of=%s WHERE id=%s",
                         (new_sub, perso_ancienne["id"]))
            if demarquees:
                logger.warning(
                    "tenant migration: espace personnel conservé = org #%s (celui de %s) ; "
                    "org(s) %s démarquée(s), à archiver si elles n'ont jamais servi",
                    perso_ancienne["id"], old_sub, [r["id"] for r in demarquees])
        # 3. repointer toutes les colonnes sub.
        for table, col in _SUB_COLUMNS:
            conn.execute(f"UPDATE {table} SET {col}=%s WHERE {col}=%s", (new_sub, old_sub))
        # 3 bis. Les ARÊTES du modèle d'accès (blueprint ADR 0053, L5) : `grantee_id`
        #    porte un sub quand `grantee_kind='user'` — sans repointage, un compte
        #    fusionné perdait ses grants de clé plateforme (la chaîne dit MUET, repli
        #    free-tier au mieux, rien au pire). Filtré par kind, pas dans
        #    `_SUB_COLUMNS` : `grantee_id` porte aussi des ids d'org. Pas de contrainte
        #    unique sur (resource, grantee) : si les DEUX comptes portaient une arête
        #    vivante vers la même instance, les deux survivent et « la plus favorable
        #    gagne » (sémantique 0053-D5, déjà celle des arêtes multiples). Les
        #    compteurs suivent l'arête par id — rien à toucher.
        conn.execute(
            "UPDATE grants SET grantee_id=%s WHERE grantee_kind='user' AND grantee_id=%s",
            (new_sub, old_sub))
        # coffre user : on repointe l'AUTEUR, jamais l'ENTITÉ.
        #
        # `_aad(entity_type, entity_id, connector, account)` — l'entité entre dans l'AAD,
        # pas l'auteur. Repointer `entity_id` sans rechiffrer donnait donc une ligne
        # que plus rien ne peut ouvrir : la fiche affiche « clé posée », chaque appel
        # échoue en `InvalidTag`, et le diagnostic accuse le connecteur. Une clé
        # ABSENTE se voit et se repose en dix secondes ; une clé présente-et-morte se
        # débogue une demi-journée (mode d'échec déjà vécu, cf. coffre / clé périmée).
        #
        # On abandonne donc la ligne user derrière : l'utilisateur repose sa clé et
        # l'interface dit la vérité. La ligne orpheline n'est pas supprimée — elle
        # reste rechiffrable à la main si on décide un jour de la récupérer.
        # ⚠️ Toute bascule de tenant doit donc s'accompagner de la LISTE des clés
        # personnelles à reposer, prévenue avant la fenêtre (ADR 0052 §Migrer).
        conn.execute("UPDATE connector_credentials SET set_by=%s WHERE set_by=%s", (new_sub, old_sub))
        # 4. supprimer l'ancienne ligne users (enfants FK déjà repointés).
        conn.execute("DELETE FROM users WHERE sub=%s", (old_sub,))
        # 5. alias (drain des vieux tokens → compte canonique).
        conn.execute(
            "INSERT INTO sub_aliases (old_sub, new_sub) VALUES (%s,%s) "
            "ON CONFLICT (old_sub) DO UPDATE SET new_sub=EXCLUDED.new_sub, migrated_at=NOW()",
            (old_sub, new_sub),
        )
    logger.info("tenant migration: merged %s → %s (%s)", old_sub, new_sub,
                operator_source or "par email")
    return True


def reconcile_tenant_migration(new_sub: str, email_hint: Optional[str] = None) -> bool:
    """Au login sur le nouveau tenant : récupère l'email AUTORITATIF du compte depuis
    Logto (Management API — le `primaryEmail` n'existe qu'après vérification, donc
    fiable même si le token ment) puis, si EXACTEMENT un autre compte partage cet email
    (l'ancien sub), le migre vers new_sub. No-op si email introuvable, 0 (rien à migrer)
    ou >1 (ambigu — on ne touche pas). Idempotent (l'ancien disparaît après migration).

    `email_hint` (claim email du token) n'est qu'un PRÉ-FILTRE pour éviter un appel
    Logto à chaque requête : si aucun autre compte ne porte cet email, rien à migrer →
    on ne sollicite pas Logto. Il n'entre JAMAIS dans la décision de merge (sécurité)."""
    if not new_sub:
        return False
    try:
        # Pré-filtre cheap sur le claim (non fiable) : court-circuite le cas courant
        # (déjà migré / rien à fusionner) sans round-trip Logto.
        if email_hint:
            with _connect() as conn:
                pre = conn.execute(
                    "SELECT 1 FROM users WHERE lower(email)=lower(%s) AND sub<>%s LIMIT 1",
                    (email_hint, new_sub),
                ).fetchone()
            if not pre:
                return False
        # Email AUTORITATIF (source de vérité) — la décision de merge se prend ici.
        from ..auth.facade import logto_user_primary_email
        from ..tenancy import ForeignTenantDirectory
        try:
            email = logto_user_primary_email(new_sub)
        except ForeignTenantDirectory as e:
            # Compte d'un tenant tiers : son email autoritatif vit dans SON annuaire,
            # que nous n'administrons pas. Rien à réconcilier ici — et on le dit en
            # une ligne (sans traceback) plutôt qu'à chaque requête de l'user, ce
            # chemin étant sur le trajet chaud d'`upsert_user`.
            logger.warning("reconcile_tenant_migration ignorée pour %s : %s", new_sub, e)
            return False
        if not email:
            return False
        with _connect() as conn:
            rows = conn.execute(
                "SELECT sub FROM users WHERE lower(email)=lower(%s) AND sub<>%s",
                (email, new_sub),
            ).fetchall()
        if len(rows) != 1:
            return False
        return migrate_sub(rows[0]["sub"], new_sub)
    except Exception:
        logger.warning("reconcile_tenant_migration échoué pour %s", new_sub, exc_info=True)
        return False


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT sub, email, name, role, created_at, updated_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def set_user_role(sub: str, role: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET role = %s, updated_at = NOW() WHERE sub = %s",
            (role, sub),
        )


def set_avatar_url(sub: str, url: Optional[str]) -> None:
    """Pose (ou efface si url=None) l'URL publique de l'avatar du user.

    URL publique servie depuis l'Object Storage — pas un secret, colonne en
    clair (hors coffre chiffré)."""
    upsert_user(sub)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET avatar_url = %s, updated_at = NOW() WHERE sub = %s",
            (url, sub),
        )


def set_user_locale(sub: str, locale: str) -> None:
    """Pose la préférence de langue de l'UI dashboard ('en'|'fr').

    La validation de l'énum vit dans la capacité `me.locale.set` (Input pydantic) —
    ici on écrit la valeur telle quelle. Colonne en clair (préférence, pas un secret)."""
    upsert_user(sub)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET locale = %s, updated_at = NOW() WHERE sub = %s",
            (locale, sub),
        )


def get_account_profile(sub: str) -> dict:
    """Fiche « situation avec oto » de l'user : {profile, updated_at}.

    Jamais None — un sub sans ligne renvoie l'état par défaut (profile vide).
    Lecture seule (ne crée pas la ligne)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT profile, updated_at FROM user_account_profile WHERE sub = %s",
            (sub,),
        ).fetchone()
    if not row:
        return {"profile": {}, "updated_at": None}
    profile = row["profile"]
    if isinstance(profile, str):  # selon le driver, JSONB peut revenir en texte
        try:
            profile = json.loads(profile)
        # noqa: SILENT — profil d'onboarding illisible ⇒ fiche sans profil, jamais d'échec d'auth
        except Exception:
            profile = {}
    return {"profile": profile or {}, "updated_at": row["updated_at"]}


def update_account_profile(sub: str, fields: Optional[dict] = None) -> dict:
    """Met à jour la fiche « situation avec oto » (upsert). `fields` est **shallow-mergé**
    dans le JSONB `profile` (clés existantes écrasées, les autres conservées). Renvoie
    l'état résultant (comme `get_account_profile`)."""
    upsert_user(sub)
    patch = json.dumps(fields or {})
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_account_profile (sub, profile, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (sub) DO UPDATE SET
                profile = user_account_profile.profile || EXCLUDED.profile,
                updated_at = NOW()
            """,
            (sub, patch),
        )
    return get_account_profile(sub)
