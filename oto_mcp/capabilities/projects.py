"""Projet — couche d'organisation (modèle produit 2026-06-27 ; owned resource ADR 0030).

Un **Projet** = un conteneur de travail POSSÉDÉ (owner_type/owner_id) : un nom + un
**brief** (le doc d'entrée, inline pour l'instant). CRUD co-déclaré MCP+REST (ADR 0009).
L'accès dérive du seam `ownership` : `can_access` (contenu, owner ∪ grants) pour
lire/écrire, `can_govern` (owner ∪ escalade `roles.py`) pour archiver.

Hors périmètre de cet incrément (suivants) : le **partage / transfert** (capacité
générique `oto_resource`, resource_type='project' déjà enregistré dans `ownership`),
les **liens** vers tableaux/procédures/connecteurs/bases, et le **Doc arborescent**
(le brief devient alors le Doc racine).
"""
from __future__ import annotations

import re
import secrets
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .. import (config, db, group_store, org_store, output_projection, ownership,
                roles, session_org, url_perimeter)
from ._authz import ORG_MEMBER, SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

RTYPE = "project"


# « doc » RETIRÉ (lot 3 chantier 0.4) : relier des pages = les
# backlinks `[[…]]` (Ship 4), pas un pointeur manuel affiché en groupe de rail.
_LINK_TYPES = ("tableau", "procedure", "connecteur")


class ProjectInput(BaseModel):
    op: Literal["create", "list", "list_templates", "get", "update", "archive",
                "copy", "handoff", "link", "unlink", "activity", "runs", "inventory",
                "lint", "publish_mcp", "unpublish_mcp"]
    project_id: Optional[int] = Field(default=None, description=(
        "OMIT it on op=runs and you get YOUR OWN still-open runs instead, across every "
        "org, each with its `run_id` — that is how you find a run whose id you lost."))
    stale_days: Optional[int] = None   # lint : seuil « pas retouché depuis » (défaut 90)
    name: Optional[str] = None
    icon: Optional[str] = Field(default=None, description=(
        "update: emoji shown in lists and headers. `\"\"` clears it."))
    brief_md: Optional[str] = None
    is_template: Optional[bool] = None   # update : publier/retirer le projet comme MODÈLE (ADR 0032 §7 B5a)
    # publish_mcp : publier le projet en endpoint MCP dédié `<mcp_slug>.mcp.oto.cx` (ADR 0032, amende #44).
    mcp_slug: Optional[str] = None       # label de sous-domaine (^[a-z0-9-]{3,}$) ; en `secret`, sert de préfixe optionnel (un suffixe aléatoire est ajouté serveur)
    mcp_access: Optional[Literal["anonymous", "secret", "org"]] = None  # anonymous = sans login + listé ; secret = sans login, non listé, slug non devinable ; org = JWT + org épinglée
    mcp_tools: Optional[list[str]] = None  # allowlist figée du preset (les seuls tools exposés sur le sous-domaine)
    mcp_expose_datastore: Optional[bool] = None  # `secret` uniquement : exposer les tools data_* en LECTURE (tableaux liés au projet, sous l'autorité de l'org). None = DÉFAUT exposé au partage secret (#193) ; passer False pour refermer
    mcp_expose_datastore_write: Optional[bool] = None  # opt-in ADDITIONNEL (#193) : autoriser l'ÉCRITURE (data_write/data_set_schema) ; sans objet si la lecture n'est pas exposée — défaut False (lecture seule)
    mcp_expose_docs: Optional[bool] = None  # `secret` uniquement : exposer les PAGES du projet (oto_doc en LECTURE) au destinataire. Défaut False — les pages portent des notes internes, les exposer par défaut serait une fuite par surprise.
    mcp_instructions_md: Optional[str] = None  # prose SERVIE AU DESTINATAIRE de l'endpoint (ce que son agent lit au branchement) — ≠ brief_md, qui reste interne. "" efface.
    # update : périmètre d'URL du projet (#605) — motifs `hôte/chemin/` (ex. `linkedin.com/in/`) que les outils de recherche ÉCARTENT (en le disant) et que les outils d'extraction REFUSENT ; un domaine entier s'écrit explicitement `hôte/*`, un hôte nu est refusé. `[]` retire. Porté aussi par l'endpoint publié du projet, sans republication.
    excluded_url_prefixes: Optional[list[str]] = Field(default=None, description=(
        "update: `host/path/` patterns search tools drop and extraction tools refuse "
        "under this project. A whole host must be written `host/*` — a bare host is "
        "refused. `[]` clears the list."))
    # create : SCOPE owner du projet (ADR 0049 — échelle platform/org/group/user).
    # 'user' (défaut) résout sur l'org ACTIVE ; 'org' = une org dont je suis membre ;
    # 'group' = un pôle/équipe (cloisonne le projet à ses membres + admins d'org) ;
    # 'platform' = projet bibliothèque (admin plateforme seulement).
    owner_type: Literal["user", "org", "group", "platform"] = "user"
    owner_id: Optional[str] = None   # org.id si owner_type='org' ; group.id si 'group' ; ignoré sinon
    # link / unlink : un pointeur typé vers une entité regroupée par le projet.
    target_type: Optional[Literal["tableau", "procedure", "connecteur"]] = None
    # Opt-ins d'op=get, cumulables : 'spine' (l'arbre des pages, Ship 2 lot 3, +
    # drill/bornage ci-dessous) · 'procedures' (le CORPS des procédures liées, #313).
    # Une valeur inconnue est ignorée en silence — l'absence d'un champ optionnel se
    # lit dans la réponse, et refuser ferait d'un ajout futur une rupture de contrat.
    include: Optional[list[str]] = None
    from_doc: Optional[int] = None     # enraciner l'épine sur un nœud (drill)
    depth: Optional[int] = None        # profondeur (défaut 2)
    target_ref: Optional[str] = None   # datastore.id | guide slug | connecteur name | doc.id (page Documents)
    label: Optional[str] = None        # nom d'affichage (link)
    role: Optional[str] = None         # pourquoi cette entité est ici / son rôle dans le projet (ADR 0032 §2)
    config: Optional[dict] = None      # surcharge contextuelle PRÉFAITE du lien (ADR 0032 §4) — connecteur : {identity_id?, instructions_md?} (legacy : identité dans config ; multi-binding : voir identity_ref) ; tableau : {provision?: "shared"|"empty"|"seeded"} = comment la COPIE de projet traite ce tableau (ADR 0032 §6)
    identity_ref: Optional[str] = None  # connecteur : identité (compte) du BINDING — clé de multiplicité (#57) ; N liens par connecteur, une identité par binding. link sans identity_ref = binding par défaut ; unlink sans identity_ref = TOUS les bindings du connecteur
    instance_ref: Optional[str] = None  # connecteur : ref d'INSTANCE (ADR 0038 B5, grammaire B4 via oto_instance op=list) — le binding désigne exactement CE credential ; la résolution le sert en dur (re-gardé pour l'appelant). Exclusif d'identity_ref (le ref porte déjà le compte). Stocké config.instance_ref.
    # list / list_templates : projection — omis = vue de tri, ["*"] = la fiche entière
    fields: Optional[list[str]] = Field(default=None, description=(
        "list/list_templates: output projection. Omitted returns the INDEX (no "
        "briefs); `[\"*\"]` returns whole records; a list of names picks columns."))
    slot: Optional[str] = None         # ADR 0035 (B2) : nom de SLOT que ce lien binde — vocabulaire DU PROJET (unicité (projet, slot) → 409 slot_taken). Fait correspondre le lien aux slots déclarés par les procédures (<slot:name>). Binder un slot TABLEAU dont la procédure déclare un `schema` cible provisionne le namespace vierge avec ce schéma (ADR 0046)


def _require(cond, code: str, msg: str, status: int = 400) -> None:
    if not cond:
        raise AuthzDenied(status, code, msg)


def _handoff_md(row: dict) -> str:
    """Texte copier-coller « reprendre dans Claude » (ADR 0032 §7 B5b) : un blob
    universel (Claude/GPT/markdown) qui pré-écrit « charge ce projet ». Pur (entrée
    = dict projet, sortie = str), sans I/O — testable isolément.

    SÉCURITÉ — n'embarque PAS le `brief_md` : un projet partagé/modèle peut porter un
    brief à contenu hostile (injection de prompt) qui, collé dans Claude, s'exécuterait
    comme une consigne. Le blob ne porte que l'instruction de CHARGEMENT (id + nom) ;
    l'agent lit le brief via `oto_project(op=get)` — donnée d'outil, pas texte pré-collé."""
    pid, name = row["id"], row.get("name") or f"#{row['id']}"
    return (
        f"Charge le projet Oto #{pid} « {name} » : appelle `oto_project(op=get, "
        f"project_id={pid})` pour son brief, ses pages et ses entités liées, puis "
        f"passe `project={pid}` sur CHAQUE appel de travail fait pour ce projet "
        f"(ses connecteurs préconfigurés, ses slots et ses tableaux en découlent — "
        f"aucun état de session, ADR 0038)."
    )


def _mcp_url(slug: object, access: str) -> object:
    """URL du connecteur MCP d'un projet publié : `secret` → `<slug>.share.<D>/mcp`
    (partage navigable), `anonymous`/`org` → `<slug>.mcp.<D>/mcp`. `<D>` = domaine de projet
    (PROD `oto.cx` / PREPROD `oto.ninja`, cutover ADR 0040). None si non publié."""
    if not slug or access == "off":
        return None
    d = config.project_domain()
    dom = f"share.{d}" if access == "secret" else f"mcp.{d}"
    return f"https://{slug}.{dom}/mcp"


def _project_web_url(sub: Optional[str], project_id) -> Optional[str]:
    """L'adresse du projet chez ce lecteur, ou None (signal #599).

    Même geste que `docs._doc_url`, et même raison : sans elle, « où je le lis ? » n'a
    pas de réponse, et l'agent se rabat sur un patron d'URL deviné. `None` quand le
    produit du lecteur n'a pas cette vue — jamais notre domaine servi à quelqu'un qui
    n'a pas notre produit (`links`)."""
    from .. import links
    return links.link_for("project", sub=sub, id=project_id)


def _visible_to(row: dict) -> str:
    """QUI voit ce projet, en une phrase — le fait que la réponse taisait.

    Vécu le 04/09/2026 : une DG demande à un agent de travailler sa base de
    connaissance ; il crée un projet PERSO (`owner_type='user'`, donc visible d'elle
    seule) dans le contexte de son org. Elle le voit apparaître « à la racine de
    l'organisation », en conclut qu'il est **visible par tous**, et passe la matinée à
    vérifier. L'agent lui-même s'est accusé à tort. Le projet était privé.

    Rien dans la réponse ne permettait de trancher : elle rend `owner_type`,
    `owner_id` et `context_org_id` — les faits TECHNIQUES — et laisse dériver la
    conséquence. Personne ne la dérive, et devant un doute sur la confidentialité on
    suppose le pire, avec raison.

    ⚠️ Le contexte n'est PAS la visibilité : un projet perso est listé dans son org de
    contexte et n'y est vu que de son propriétaire. C'est cette confusion qu'on paie."""
    otype = str(row.get("owner_type") or "user")
    org = row.get("context_org_id")
    if otype == "user":
        # Vérifié sur les CINQ chemins le 04/09 : liste (`list_member_projects` filtre
        # `owner_id = sub`), recherche (même seam, parité tenue par tripwire), ouverture
        # par id (`_owner_match_content` → `sub == owner_id`, « pas d'escalade
        # plateforme ici, privacy by default »), transfert (`sub == owner_id` ou admin
        # PLATEFORME), et console de gouvernance (un admin d'org n'y reçoit que
        # `("user", son_sub)` + ses orgs).
        # ⚠️ La seule exception est l'opérateur PLATEFORME, qui voit tous les projets en
        # MÉTADONNÉES (nom + propriétaire, jamais le contenu) via cette console. On le
        # dit : un nom de projet est parfois plus révélateur que son contenu.
        return ("toi seul — ni les autres membres, ni les administrateurs de ton org ne "
                "le voient, ni en liste, ni par recherche, ni en l'ouvrant par son id"
                + (f" ; il est rangé dans le contexte de l'org {org}, ce qui n'est PAS "
                   "la même chose qu'y être partagé" if org is not None else "")
                + ". Seul un opérateur de la plateforme en voit le NOM, jamais le "
                  "contenu.")
    if otype == "org":
        return (f"TOUS les membres de l'org {row.get('owner_id')} — ce projet n'est pas "
                "privé")
    if otype == "group":
        return (f"les membres de l'équipe {row.get('owner_id')}, et les administrateurs "
                "de l'org")
    return "tout le monde sur la plateforme (projet bibliothèque)"


def _view(row: dict, sub: Optional[str] = None) -> dict:
    return {
        "id": row["id"], "name": row["name"], "icon": row.get("icon"),
        # L'adresse web du projet, à côté de son id (#599).
        "url": _project_web_url(sub, row["id"]),
        "brief_md": row.get("brief_md", ""),
        "owner_type": row["owner_type"], "owner_id": row["owner_id"],
        # Qui voit ce projet, en clair. `owner_type` seul oblige à dériver, et personne
        # ne dérive — surtout pas sur une question de confidentialité (04/09).
        "visible_to": _visible_to(row),
        # Org de CONTEXTE d'un projet perso (ADR 0030 amendé) — « moi, org ». NULL sinon.
        "context_org_id": (str(row["context_org_id"])
                           if row.get("context_org_id") is not None else None),
        "is_template": bool(row.get("is_template")),
        # Publication MCP (ADR 0032) : présence + URLs dérivées. `secret` = partage
        # navigable `<slug>.share.oto.cx` (UI + /mcp) ; `anonymous`/`org` = `<slug>.mcp.oto.cx`.
        "mcp_slug": row.get("mcp_slug"),
        "mcp_access": row.get("mcp_access") or "off",
        "mcp_tools": list(row.get("mcp_tools") or []),
        "mcp_expose_datastore": bool(row.get("mcp_expose_datastore")),
        "mcp_expose_datastore_write": bool(row.get("mcp_expose_datastore_write")),
        "mcp_expose_docs": bool(row.get("mcp_expose_docs")),
        # Prose servie au DESTINATAIRE de l'endpoint — ≠ `brief_md`, qui reste interne.
        "mcp_instructions_md": row.get("mcp_instructions_md") or "",
        # Périmètre d'URL (#605) — ce que les outils de recherche/extraction n'atteignent
        # pas dans ce projet. Liste canonique, vide = aucune exclusion.
        "excluded_url_prefixes": list(row.get("excluded_url_prefixes") or []),
        "mcp_url": _mcp_url(row.get("mcp_slug"), row.get("mcp_access") or "off"),
        # Base de PARTAGE navigable (lecture seule, humain) — mode `secret` uniquement.
        "share_url": (f"https://{row['mcp_slug']}.share.{config.project_domain()}"
                      if row.get("mcp_slug") and (row.get("mcp_access") or "off") == "secret" else None),
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "archived_at": row.get("archived_at"),
    }


def _projected(rows: list[dict], fields: Optional[list[str]]) -> dict:
    """Vue de LISTE d'un index de projets : les proses deviennent leur taille.

    L'index sert à choisir quel projet ouvrir — 26 projets avec tout leur `brief_md`
    pesaient 73 K caractères, au-delà du plafond d'un tool result. Le brief entier se
    lit par `op=get`, ou ici par `fields=["*"]`.

    `mcp_instructions_md` (prose servie au destinataire d'un endpoint publié) suit la
    même règle : c'est un corps, pas une métadonnée de tri."""
    _require(fields is None or bool(fields), "empty_fields",
             "`fields` est une liste vide : omets-le pour la vue de tri, passe "
             '`["*"]` pour les fiches entières, ou nomme les colonnes voulues.')
    rows, notice = output_projection.summarize(
        rows, body_fields=("brief_md", "mcp_instructions_md"), fields=fields,
        # `url` est gardée quoi qu'on projette (#599) : « lequel j'ouvre ? » est
        # exactement la question de l'index, et l'adresse en est la réponse.
        always=("id", "name", "owner_type", "owner_id", "url"))
    return {"projects": rows, **({"projection": notice} if notice else {})}


def _require_active_org_visible(ctx: ResolvedCtx, row: dict) -> None:
    """Gate de CONTEXTE (ADR 0023) des accès par-id. Sans lui, une URL directe
    `/projects/<id>` (ou `oto_use_project`) atteint un projet d'une AUTRE de mes orgs —
    fuite hors contexte. Délègue la visibilité à `ownership.visible_in_org` (primitive
    partagée) ; ajoute un message ACTIONNABLE (bascule d'org) si l'acteur y a accès par
    une autre org, 404 non-disclosant sinon (ne révèle pas l'existence)."""
    if ownership.visible_in_org(ctx.sub, ctx.org_id, RTYPE, str(row["id"])):
        return
    rid = str(row["id"])
    if ownership.can_access(ctx.sub, RTYPE, rid, "read"):
        owner = ownership.owner_of(RTYPE, rid)
        oname = (org_store.get_org(int(owner[1])) or {}).get("name") \
            if owner and owner[0] == "org" else None
        hint = (f" Il appartient à l'org « {oname} » — passe `org=<id>` sur cet "
                "appel pour l'ouvrir." if oname else "")
        raise AuthzDenied(403, "wrong_org_context",
                          f"Projet #{rid} hors de l'org active.{hint}")
    raise AuthzDenied(404, "unknown_project", f"Projet #{rid} inconnu.")


def _procedure_ref_to_id(org_id: Optional[int], ref: str) -> str:
    """Réf de procédure (ADR 0032) → l'ID stable du guide. Accepte déjà un id
    (chiffres) ou un slug (résolu dans l'org du projet) ; fallback = laisser tel quel
    (guide introuvable / hors org → pas de casse, résolu à la lecture côté front)."""
    if not ref or ref.isdigit() or org_id is None:
        return ref
    inst = org_store.get_instruction("org", org_id, ref)
    return str(inst["id"]) if inst and inst.get("id") is not None else ref


def _ref_canonizer(row: dict, org_id: Optional[int], target_type: str):
    """La fonction qui ramène une réf de lien à son écriture CANONIQUE — celle que `link`
    STOCKE aujourd'hui (l'id stable). Identité pour les types sans normalisation."""
    if target_type == "tableau":
        return lambda r: _resolve_tableau_id(row, r) or str(r or "").strip()
    if target_type == "procedure":
        return lambda r: _procedure_ref_to_id(org_id, str(r or "").strip())
    return lambda r: str(r or "").strip()


def _unlink_refs(links: list[dict], target_type: str, given: str, canon) -> list[str]:
    """Les `target_ref` STOCKÉS que la réf demandée désigne — la cible réelle d'un unlink.

    Le stockage est canonique (id) DEPUIS que `link` normalise nom/slug→id, mais les
    lignes d'avant portent encore le NOM du namespace (tableau) ou le SLUG du guide
    (procédure) — et le chemin de LECTURE les résout toujours (#117) : ce sont des liens
    bien vivants, avec leur `namespace` et leur slot. L'unlink, lui, canonisait la réf
    demandée puis supprimait CET id : zéro ligne touchée quand la ligne porte l'autre
    écriture, et un `ok: true` par-dessus (#699). On confronte donc les deux côtés
    canonisés, dans les deux sens.

    Renvoie les refs BRUTES, sans doublon : c'est la valeur STOCKÉE qui doit matcher la
    clause SQL, jamais sa forme canonique. Vide = rien à délier (le caller refuse).

    UNE règle, pas deux : `canon` est déterministe, donc deux refs identiques canonisent
    pareil — un `stored == given` en OU ne déciderait jamais seul, et masquerait une
    canonisation cassée derrière un vert."""
    want = canon(given)
    out: list[str] = []
    for l in links:
        if l.get("target_type") != target_type:
            continue
        stored = str(l.get("target_ref") or "")
        if not stored or stored in out:
            continue
        if canon(stored) == want:
            out.append(stored)
    return out


def _rien_a_delier(links: list[dict], target_type: str, given: str) -> str:
    """Le message d'un unlink qui n'a RIEN retiré : ce qu'on cherchait, et ce que le projet
    porte vraiment pour ce type. Sans le second, l'agent ne peut que réessayer à l'identique."""
    presents = [str(l.get("target_ref")) for l in links
                if l.get("target_type") == target_type and l.get("target_ref")]
    tete = (f"Aucun lien `{target_type}` « {given} » sur ce projet : RIEN n'a été retiré "
            "(déjà délié ?).")
    if not presents:
        return f"{tete} Ce projet ne porte aucun lien `{target_type}`."
    montre = presents[:12]
    return (f"{tete} Liens `{target_type}` de ce projet : "
            + ", ".join(f"« {r} »" for r in montre)
            + ("…" if len(presents) > len(montre) else "")
            + " — reprends la réf telle que `op=get` la rend.")


def _tableau_owner_candidates(row: dict) -> list[tuple[str, str]]:
    """Où CHERCHER un tableau nommé, pour un projet donné — du plus spécifique au plus large.

    Le détenteur d'un projet n'est PAS le détenteur de ses tableaux : un projet PERSO
    (scope membre, ADR 0030 §8) appartient à `(user, sub)` tandis que ses tableaux naissent
    dans l'ORG de travail ; un projet d'ÉQUIPE appartient au groupe, mais l'équipe range
    couramment ses tableaux au niveau de l'org parente. Chercher contre le seul owner brut
    rendait le lien par NOM impossible dans les deux cas — la doc promet « id/slug/name »,
    seul l'id marchait (signaux #272 · #286 · #287)."""
    otype, oid = str(row.get("owner_type") or ""), str(row.get("owner_id") or "")
    cands: list[tuple[str, str]] = []
    if otype and oid:
        cands.append((otype, oid))
    if otype == "user" and row.get("context_org_id"):
        cands.append(("org", str(row["context_org_id"])))   # l'org de travail du projet perso
    elif otype == "group" and oid.isdigit():
        g = group_store.get_group(int(oid))
        if g and g.get("org_id"):
            cands.append(("org", str(g["org_id"])))          # l'org parente de l'équipe
    return cands


def _resolve_tableau_id(row: dict, ref: str) -> Optional[str]:
    """Réf de tableau → l'ID NUMÉRIQUE stable du namespace (comme les procédures). Accepte
    un id (chiffres, renvoyé tel quel) OU un nom de namespace, résolu contre les scopes du
    projet (`_tableau_owner_candidates`). Renvoie None si un nom ne résout nulle part — le
    caller décide (erreur au link, ref brute conservée à l'unlink). Stocker l'id garde la
    résolution cohérente (audit, list_project_links, share_ui l'attendent numérique)."""
    ref = str(ref or "").strip()
    if not ref or ref.isdigit():
        return ref or None
    for otype, oid in _tableau_owner_candidates(row):
        ns = db.get_datastore_namespace(otype, oid, ref)
        if ns and ns.get("id") is not None:
            return str(ns["id"])
    return None


def _mcp_unresolvable_tools(row: dict, tools: list[str],
                            expose_datastore: bool = False) -> list[str]:
    """Sonde de publication SANS LOGIN (anonymous/secret, ADR 0032) : un endpoint sans
    login n'a pas d'identité user → un tool n'est servi que s'il est résoluble SANS `sub`.
    Renvoie la liste des tools **non résolubles** pour l'org propriétaire : tool spine/méta
    (`oto_*`, `data_*`… — sans connecteur, exige une identité) ou credential absent
    (`access.connector_resolvable_for_org`). **Non bloquant** (choix produit) : on publie
    quand même, ces tools sont exposés mais **échouent proprement à l'appel** (McpError, pas
    de fallback) ; la liste remonte en warning pour que l'humain configure une clé d'org ou
    retire les outils.

    `expose_datastore` (opt-in `secret`) : les tools `data_*` agissent alors SOUS l'org
    propriétaire (pas de connecteur, pas de `sub` requis) → considérés résolubles."""
    from .. import access, providers
    from ..tool_visibility import namespace_of
    if row.get("owner_type") != "org":
        return list(tools)  # pas d'org propriétaire → rien ne résout
    org_id = int(row["owner_id"])
    bad = []
    for t in tools:
        if expose_datastore and namespace_of(t) == "data":
            continue  # datastore de l'org, servi sous son autorité (opt-in)
        con = providers.connector_for_namespace(namespace_of(t))
        if con is None or not access.connector_resolvable_for_org(con.name, org_id):
            bad.append(t)
    return sorted(bad)


def _gen_secret_slug(base: Optional[str]) -> str:
    """Slug NON DEVINABLE pour un endpoint `secret` (URL secrète). Un préfixe optionnel
    lisible (issu du slug saisi, réduit à `[a-z0-9-]`) aide à identifier l'endpoint ; le
    suffixe aléatoire garantit l'imprévisibilité. C'est le SEUL secret d'accès du mode
    `secret` (URL-as-capability, endpoint servant sous les credentials de l'org) → 128 bits
    d'entropie, dimensionné contre le bruteforce en ligne (le préfixe, dérivé du nom, est
    devinable : l'entropie doit tenir dans le suffixe seul). `token_hex` reste dans
    `[a-f0-9]` ⊂ charset `_MCP_SLUG_RE` (`token_urlsafe` introduirait `A-Z_-`, rejeté)."""
    prefix = re.sub(r"[^a-z0-9]+", "-", (base or "").strip().lower()).strip("-")[:24]
    token = secrets.token_hex(16)  # 32 chars hex = 128 bits
    return f"{prefix}-{token}" if prefix else f"mcp-{token}"


def unpublish_project_mcp(sub: str, project_id: int) -> dict:
    """Retire la publication MCP d'un projet. AUCUN contrôle d'autz (le caller a déjà
    gaté `can_govern`) — réutilisé par oto_project ET le « Partager » unifié (ADR 0048)."""
    db.set_project_mcp_publication(project_id, slug=None, access="off", tools=[])
    db.log_project_activity(project_id, sub, "project.unpublish_mcp", None)
    return _view(db.get_project_by_id(project_id), sub)


def publish_project_mcp(sub: str, row: dict, *, access_mode: str,
                        mcp_slug: Optional[str], mcp_tools: Optional[list[str]],
                        expose_datastore: Optional[bool] = None,
                        expose_datastore_write: Optional[bool] = None,
                        expose_docs: Optional[bool] = None,
                        instructions_md: Optional[str] = None) -> dict:
    """Cœur de la publication MCP d'un projet (ADR 0032). AUCUN contrôle d'autz (le
    caller a déjà gaté `can_govern`) — partagé par la capacité `oto_project` et par le
    « Partager » unifié (`oto_resource` audience public/secret/org, ADR 0048 B3). Lève
    `AuthzDenied` sur entrée invalide (tools vide en public/org, slug manquant, slug pris)."""
    project_id = int(row["id"])
    tools = [t for t in (mcp_tools or []) if t and t.strip()]
    # Un endpoint `anonymous`/`org` EST un preset d'outils figé → liste requise. Un lien
    # `secret` (UI navigable lecture seule) peut tout exposer → liste vide autorisée (le
    # front applique déjà la même règle avant l'appel).
    _require(bool(tools) or access_mode == "secret", "missing_tools",
             "`mcp_tools` (liste non vide) requis pour un endpoint public ou org — "
             "seul un lien « secret » peut tout exposer en lecture seule.", 400)
    # Datastore exposé (LECTURE) : DÉFAUT au partage `secret` (#193), refermable ;
    # réservé à `secret`. L'ÉCRITURE est un opt-in additionnel séparé.
    expose_ds = ((access_mode == "secret") if expose_datastore is None
                 else bool(expose_datastore))
    _require(not (expose_ds and access_mode != "secret"), "datastore_secret_only",
             "mcp_expose_datastore est réservé à l'accès `secret` (un endpoint "
             "`anonymous` est public, un endpoint `org` résout déjà data_* via le "
             "membre authentifié).", 400)
    expose_ds_write = bool(expose_datastore_write) and expose_ds
    # Pages : opt-in EXPLICITE (jamais un défaut), `secret` seulement. Régime INVERSE du
    # datastore ci-dessus, et c'est voulu : le datastore d'un projet est le plus souvent
    # le livrable qu'on partage, les pages sont de la doc interne.
    expose_docs_eff = bool(expose_docs) and access_mode == "secret"
    _require(not (expose_docs and access_mode != "secret"), "docs_secret_only",
             "mcp_expose_docs est réservé à l'accès `secret` (un endpoint "
             "`anonymous` est public ; un endpoint `org` résout oto_doc via le "
             "membre authentifié).", 400)
    # Slug effectif : `secret` → non devinable (réutilise l'existant pour ne pas casser
    # l'URL déjà distribuée) ; anonymous/org → slug saisi requis.
    if access_mode == "secret":
        slug = (row.get("mcp_slug") if row.get("mcp_access") == "secret" and row.get("mcp_slug")
                else _gen_secret_slug(mcp_slug))
    else:
        _require(bool(mcp_slug), "missing_slug", "`mcp_slug` requis.", 400)
        slug = mcp_slug
    unresolvable = (_mcp_unresolvable_tools(row, tools, expose_ds)
                    if access_mode in ("anonymous", "secret") else [])
    try:
        db.set_project_mcp_publication(project_id, slug=slug, access=access_mode, tools=tools,
                                       expose_datastore=expose_ds,
                                       expose_datastore_write=expose_ds_write,
                                       expose_docs=expose_docs_eff)
    except ValueError as e:
        code = "slug_taken" if str(e).startswith("slug_taken") else "bad_slug"
        _require(False, code, str(e), 409 if code == "slug_taken" else 400)
    # Endpoint AUTHED (#44) : enregistre l'API resource Logto (audience JWT). Best-effort.
    resource_registered = None
    if access_mode == "org":
        try:
            from ..auth import facade as oauth_facade
            oauth_facade.ensure_api_resource(
                f"https://{slug}.mcp.{config.project_domain()}/mcp",
                name=f"oto MCP — {row.get('name') or slug}")
            resource_registered = True
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("ensure_api_resource échoué pour %s", slug)
            resource_registered = False
    if instructions_md is not None:
        db.set_project_mcp_instructions(project_id, instructions_md)
    db.log_project_activity(project_id, sub, "project.publish_mcp", f"{access_mode}:{slug}")
    out = _view(db.get_project_by_id(project_id), sub)
    if resource_registered is not None:
        out["logto_resource_registered"] = resource_registered
    if unresolvable:
        out["mcp_unresolvable_tools"] = unresolvable
    warnings = []
    if not config.project_domain_is_production():
        # Les sous-domaines de projet hors prod n'ont PAS de certificat public (cert
        # interne Caddy) : l'URL est parfaitement fonctionnelle pour tester, et
        # REJETÉE par tout client MCP réel. Le dire ICI, pas au client à qui on
        # vient d'envoyer le lien (vécu, feedback #308).
        warnings.append(
            f"URL d'environnement de TEST ({config.project_domain()}) : son certificat "
            "TLS n'est pas reconnu publiquement, un client MCP refusera la connexion. "
            "Ne la transmets pas à un tiers — republie depuis la production.")
    if not (db.get_project_by_id(project_id) or {}).get("mcp_instructions_md"):
        warnings.append(
            "aucune instruction publiée : l'agent du destinataire se branchera sans "
            "savoir ce que contient ce projet ni comment le lire. Passe "
            "`mcp_instructions_md` (≠ le brief interne).")
    if warnings:
        out["warnings"] = warnings
    return out


def _project(ctx: ResolvedCtx, inp: ProjectInput) -> dict:
    sub = ctx.sub

    if inp.op == "create":
        _require(inp.name and inp.name.strip(), "missing_name", "`name` requis.")
        # `context_org` = l'org de CONTEXTE d'un projet PERSO (owner='user') : sépare la
        # propriété (la personne) du contexte de travail (ADR 0030 amendé). NULL pour un
        # projet non-perso (org/group/platform), dont le contexte se dérive de l'owner.
        context_org: Optional[int] = None
        if inp.owner_type == "org":
            _require(inp.owner_id, "missing_owner",
                     "`owner_id` (org) requis pour un projet d'org.")
            _require(roles.is_org_member(sub, int(inp.owner_id)), "forbidden",
                     "Tu n'es pas membre de cette org.", 403)
            owner_type, owner_id = "org", str(inp.owner_id)
        elif inp.owner_type == "group":
            # ADR 0049 : projet de PÔLE — cloisonné aux membres du groupe (+ escalade
            # org_admin, inaliénable). Créer = être du pôle ou l'administrer.
            _require(inp.owner_id, "missing_owner",
                     "`owner_id` (group) requis pour un projet d'équipe.")
            gid = int(inp.owner_id)
            _require(group_store.get_group(gid) is not None, "unknown_group",
                     f"Groupe #{gid} inconnu.", 404)
            _require(roles.can_read_group(sub, gid), "forbidden",
                     "Tu n'es pas membre de cette équipe.", 403)
            owner_type, owner_id = "group", str(gid)
        elif inp.owner_type == "platform":
            # ADR 0049 : projet BIBLIOTHÈQUE — gouverné par la plateforme, lisible de tous.
            _require(roles.is_platform_admin(sub), "forbidden",
                     "Un projet plateforme est réservé aux admins plateforme.", 403)
            owner_type, owner_id = "platform", "platform"
        else:
            # Défaut = PERSO (ADR 0030 amendé 2026-07-17) : le projet naît possédé par le
            # créateur `(user, sub)`, PRIVÉ, dans le CONTEXTE de son org active. L'org/team
            # deviennent des cibles de partage/transfert explicites, plus le défaut. Owner
            # = « moi » ; `context_org` = « mon org » — l'identité `(moi, org)`.
            _require(ctx.org_id is not None, "no_active_org", "Aucune org active.", 400)
            owner_type, owner_id, context_org = "user", sub, int(ctx.org_id)
        pid = db.create_project(owner_type, owner_id, inp.name.strip(),
                                inp.brief_md or "", created_by=sub,
                                context_org_id=context_org)
        db.log_project_activity(pid, sub, "project.create", inp.name.strip())
        return _view(db.get_project_by_id(pid), sub)

    if inp.op == "list":
        # Scopé à l'org active (seam `ownership.active_owner`) : charger une org ne
        # montre QUE ses projets (l'org est le contexte, ADR 0023). Un projet d'une
        # autre org ne fuite plus. S'y AJOUTENT les projets PARTAGÉS à cette org, à
        # mes équipes DANS cette org, ou à moi (grant `resource_grants`, livraison
        # #52 / partage d'équipe) — marqués `shared` (l'owner reste l'org émettrice ;
        # ce n'est pas une fuite, c'est un don d'accès). Les groupes sont ceux de
        # l'org active seulement : pas de fuite cross-org.
        from .. import project_audit
        owner = ownership.active_owner(ctx.org_id)
        _require(owner is not None, "no_active_org", "Aucune org active.", 400)
        # ADR 0049 : les projets de PÔLE (group-owned) de l'org active s'ajoutent aux
        # projets d'org — pour mes équipes (membre), ou TOUS les groupes de l'org si
        # j'en suis admin (gouvernance inaliénable, même règle que `can_read_group`).
        # Le scope reste borné à l'org active (pas d'`owner_pairs`, ADR 0023).
        if roles.is_org_admin(sub, int(ctx.org_id)):
            group_ids = [int(g["id"]) for g in group_store.list_groups(int(ctx.org_id))]
        else:
            group_ids = [int(g["group_id"])
                         for g in group_store.list_groups_for_user(sub, ctx.org_id)]
        owners = [owner] + [("group", str(g)) for g in group_ids]
        own_rows = db.list_projects_for_owners(owners)
        # Scope MEMBRE (ADR 0030 amendé) : mes projets PERSO de CETTE org (`context_org`),
        # possédés par moi seul → owned (jamais `shared`). Parité stricte avec la recherche
        # (`accessible_project_ids` appelle le même `db.list_member_projects`) : « cherchable
        # ⇔ lisible » (tripwire `test_search_scope_tripwire`).
        _seen_own = {r["id"] for r in own_rows}
        own_rows += [r for r in db.list_member_projects(sub, int(ctx.org_id))
                     if r["id"] not in _seen_own]
        # Pastilles d'ÉTAT de l'index (refonte UX, ADR 0032) : nb d'entités liées +
        # partagé + « à vérifier » (audit). Le nb de grants est batché (1 requête) ; les
        # liens/audit sont par projet (les listes d'org sont petites) et best-effort.
        grant_counts = db.project_grant_counts([r["id"] for r in own_rows])

        def _enrich(r: dict, shared: bool) -> dict:
            links = db.list_project_links(r["id"])
            # LISTE : audit LÉGER (checks en mémoire seuls) — l'audit complet par projet
            # (résolution procédures + résolvabilité connecteur + run_stats) produisait un
            # N+1 → timeout 180 s (oto/#6 A7). Le badge « à vérifier » reste sur les liens
            # morts détectables sans requête ; le détail complet est servi par op=get.
            aud = project_audit.audit_project(r["id"], links, light=True)
            has_audit = bool(aud.get("dead_links") or aud.get("unbound_slots")
                             or aud.get("inert_procedures"))
            return {**_view(r, sub), "entity_count": len(links), "has_audit": has_audit,
                    "shared": shared or grant_counts.get(r["id"], 0) > 0,
                    # `can_write` sur la LISTE (pastille « lecture ») — même source que op=get :
                    # un projet partagé en lecture seule remonte false. Own = accès effectif.
                    "can_write": ownership.can_access(sub, RTYPE, str(r["id"]), "write")}

        own = [_enrich(r, False) for r in own_rows]
        seen = {p["id"] for p in own}
        principals = ownership.active_org_principals(ctx.sub, ctx.org_id)
        # #5.1 : un partage PERSONNEL (principal ('user', sub)) est org-agnostique → il
        # remontait dans « Partagés » de CHAQUE org de l'utilisateur. On ne l'AFFICHE que
        # dans l'org de RATTACHEMENT (la maison) → une seule fois, pas dupliqué partout.
        # L'ACCÈS reste intact ailleurs (can_access/recherche : un partage personnel est
        # cross-org par nature) — c'est le LISTING qui se dé-duplique.
        from .. import org_store  # noqa: org_store est shadowé en local par d'autres branches de cette fonction
        if ctx.org_id != org_store.get_active_org(ctx.sub):
            principals = [p for p in principals if p[0] != "user"]
        shared = [{**_enrich(r, True), "permission": r.get("permission")}
                  for r in db.list_projects_granted_to(principals)
                  if r["id"] not in seen]
        return _projected(own + shared, inp.fields)

    if inp.op == "list_templates":
        # Modèles (is_template) lisibles par l'acteur — la bibliothèque copiable (B5a).
        # ADR 0049 : + les modèles PLATFORM-owned (bibliothèque plateforme), pour tous.
        owners = ownership.accessor_scope(sub).owner_pairs() + [("platform", "platform")]
        return _projected([_view(r, sub) for r in
                           db.list_projects_for_owners(owners, templates_only=True)],
                          inp.fields)

    if inp.op == "runs" and inp.project_id is None:
        # « Fermer un déroulé dont on a perdu l'identifiant » (#473). `run_finish`
        # n'accepte qu'un `run_id`, et rien ne le rendait à son propriétaire : le bloc
        # de contexte annonce les derniers déroulés par leur INTITULÉ, et un run ouvert
        # hors projet n'était énumérable nulle part. Il restait donc « en cours » pour
        # toujours — le régime dominant, pas le cas rare (15 des 16 runs ouverts
        # mesurés en prod, cf. `run_status`).
        #
        # Ce n'est PAS un repli silencieux du cas « projet manquant » : c'est une
        # portée DÉCLARÉE, que la réponse nomme (`scope`, `open_only`). Toutes les
        # autres ops par-id continuent de refuser en nommant leur manque, juste en
        # dessous.
        #
        # Ouverts SEULEMENT, parce que c'est la question posée — « qu'est-ce qu'il me
        # reste à refermer ? » — et qu'un historique complet noierait deux runs à clore
        # sous vingt runs déjà clos. Les runs d'un PROJET, eux, se demandent en nommant
        # le projet, et rendent tout (c'est la pastille ok/échec du viewer).
        from .. import run_status
        runs = db.my_runs(sub, limit=20, open_only=True)
        return {"scope": "mine", "open_only": True,
                "runs": [{**r, "status": run_status.describe(r)} for r in runs]}

    # ops ciblées : project_id requis + existence
    _require(inp.project_id is not None, "missing_project", "`project_id` requis.")
    rid = str(inp.project_id)
    row = db.get_project_by_id(int(inp.project_id))
    _require(row is not None, "unknown_project", f"Projet #{inp.project_id} inconnu.", 404)

    # Gate de CONTEXTE d'org (ADR 0023) — UNE fois pour toutes les ops par-id : un projet
    # n'est atteignable (lecture comme mutation) que DANS l'org qui le possède, jamais
    # depuis une AUTRE de mes orgs. Le pendant par-id du scoping de `op=list`. SEUL `copy`
    # y échappe : copier un MODÈLE (ou un projet lisible) cross-org est une feature (B5a).
    if inp.op != "copy":
        _require_active_org_visible(ctx, row)

    if inp.op == "get":
        from .. import project_audit
        links = db.list_project_links(int(inp.project_id))
        out = {**_view(row, sub),
               "can_write": ownership.can_access(sub, RTYPE, rid, "write"),
               "links": links,
               # B5 : liens vérifiés comme des refs — le lien mort remonte à l'agent
               # qui LIT le projet (brief), pas seulement à op=inventory (curation).
               # `links` réutilisé : pas de double chargement.
               "audit": project_audit.audit_project(int(inp.project_id), links)}
        # ÉPINE (lot 3 Ship 2) — opt-in, bornée, enracinable : l'arbre des pages dans
        # l'ordre curé avec chapôs (fallback dérivé). C'est la CARTE que l'agent lit
        # pour se repérer, puis oto_doc(op=get) la page — jamais op=list de tout.
        if inp.include and "spine" in inp.include:
            out["spine"] = db.project_spine(
                int(inp.project_id), from_doc=inp.from_doc,
                depth=(inp.depth if inp.depth is not None else 2))
        # PROCÉDURES (#313) — opt-in : le CORPS des procédures liées, pour qu'on
        # puisse lire la règle qui a produit une fiche sans quitter l'écran.
        if inp.include and "procedures" in inp.include:
            out["procedures"] = _linked_procedures(sub, links)
        return out

    if inp.op == "lint":
        # B1 (#6) : santé des pages du projet (stale / vides / titres en double) — un
        # seul list_docs_for_project, checks en mémoire (pas de N+1). Accès = gate org
        # ci-dessus (comme op=get). L'agent/l'user curent ensuite.
        from datetime import datetime, timedelta
        from .. import doc_lint
        days = inp.stale_days if inp.stale_days is not None else 90
        cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d %H:%M:%S")
        docs = db.list_docs_for_project(int(inp.project_id))
        return {"project_id": int(inp.project_id), "stale_days": days,
                **doc_lint.lint_docs(docs, stale_before=cutoff)}

    if inp.op == "activity":
        # Chaque événement porte l'IDENTITÉ de son auteur (`actor`, résolue du sub loggé)
        # → l'Historique dashboard affiche « par X » réel (refonte UX, ADR 0032).
        rows = db.list_project_activity(int(inp.project_id))
        activity = [{
            "sub": r.get("sub"), "action": r["action"], "detail": r.get("detail"),
            "created_at": r.get("created_at"),
            "actor": ({"name": r.get("actor_name"), "email": r.get("actor_email")}
                      if r.get("actor_name") or r.get("actor_email") else None),
        } for r in rows]
        return {"id": inp.project_id, "activity": activity}

    if inp.op == "runs":
        # Derniers runs (ADR 0017) d'une procédure liée — pastille ok/échec du viewer.
        # `target_ref` = id stable du guide → résolu en slug (clé de `runs.doctrine`) ;
        # omis = tous les runs du projet. Read seul (gate de contexte d'org déjà passée).
        from .. import org_store  # local (org_store est shadowé en local par d'autres branches)
        slug: Optional[str] = None
        if inp.target_ref:
            ref = str(inp.target_ref)
            instr = org_store.get_instruction_by_id(int(ref)) if ref.isdigit() else None
            slug = (instr or {}).get("slug") or ref
        return {"id": inp.project_id, "target_ref": inp.target_ref,
                "runs": db.project_runs(int(inp.project_id), guide=slug)}

    if inp.op == "handoff":
        # « Reprendre dans Claude » (B5b) : blob copier-coller qui charge ce projet.
        return {"id": inp.project_id, "markdown": _handoff_md(row)}

    if inp.op == "inventory":
        # Inventaire DÉRIVÉ du projet (ADR 0035 B4) — jamais déclaré : surface d'outils
        # = refs <tool:> des procédures liées ∪ usage observé des runs (0017), plus les
        # connecteurs (liens ∪ slots connecteur des procédures). Sert le préremplissage
        # de publish_mcp (l'humain cure) + le manifeste dashboard.
        from .. import org_store, providers, tool_registry
        from ..tool_visibility import namespace_of
        links = db.list_project_links(int(inp.project_id))
        procedures, proc_tools, slot_connectors = [], [], set()
        for l in links:
            if l["target_type"] != "procedure":
                continue
            ref = str(l["target_ref"])
            instr = org_store.get_instruction_by_id(int(ref)) if ref.isdigit() else None
            if not instr:
                procedures.append({"ref": ref, "resolved": False})
                continue
            refs = tool_registry.ref_names(instr.get("body_md") or "")
            slots = instr.get("slots") or []
            procedures.append({"ref": ref, "slug": instr["slug"], "resolved": True,
                               "tools": refs, "slots": slots})
            proc_tools += refs
            slot_connectors |= {s.get("connector") or s["name"] for s in slots
                                if s.get("type") == "connecteur"}
        run_tools = db.project_run_tools(int(inp.project_id))
        # Union suggérée : refs des procédures d'abord (l'intention), puis l'usage ;
        # les tools spine/méta (sans connecteur au registre : oto_*, run_*, data_*…)
        # sont écartés de la suggestion (non publiables), les sources restent brutes.
        seen, tools, connectors = set(), [], set(slot_connectors)
        for t in proc_tools + run_tools:
            if t in seen:
                continue
            seen.add(t)
            con = providers.connector_for_namespace(namespace_of(t))
            if con is None:
                continue
            tools.append(t)
            connectors.add(con.name)
        connectors |= {l["target_ref"] for l in links if l["target_type"] == "connecteur"}
        # Source de CHAQUE connecteur (pour distinguer dans l'UI « déclaré au projet »
        # vs « requis par une procédure » vs « vu en run ») — additif, `connectors` reste
        # la liste plate rétro-compatible.
        csources: dict[str, set] = {}

        def _tag(con, src):
            if con:
                csources.setdefault(con, set()).add(src)
        for l in links:
            if l["target_type"] == "connecteur":
                _tag(l["target_ref"], "declared")
        for p in procedures:
            if not p.get("resolved"):
                continue
            slug = p.get("slug")
            for s in (p.get("slots") or []):
                if s.get("type") == "connecteur":
                    _tag(s.get("connector") or s.get("name"), f"procedure:{slug}")
            for t in (p.get("tools") or []):
                con = providers.connector_for_namespace(namespace_of(t))
                _tag(con.name if con else None, f"procedure:{slug}")
        for t in run_tools:
            con = providers.connector_for_namespace(namespace_of(t))
            _tag(con.name if con else None, "run")
        from .. import project_audit
        return {"id": inp.project_id, "tools": tools, "connectors": sorted(connectors),
                "connector_sources": {k: sorted(v) for k, v in csources.items()},
                "sources": {"procedures": procedures, "runs": run_tools,
                            "tableaux": [{"slot": l.get("slot"), "namespace": l.get("namespace"),
                                          "ref": l["target_ref"]}
                                         for l in links if l["target_type"] == "tableau"]},
                # B5 : liens vérifiés comme des refs — morts / slots non bindés / inertes.
                "audit": project_audit.audit_project(int(inp.project_id), links)}

    if inp.op == "update":
        _require(ownership.can_access(sub, RTYPE, rid, "write"), "forbidden", "Écriture refusée.", 403)
        # Publier/retirer comme MODÈLE est un acte de gouvernance (visible aux autres
        # membres de l'org comme bibliothèque) → can_govern, pas un simple write.
        if inp.is_template is not None:
            _require(ownership.can_govern(sub, RTYPE, rid), "forbidden",
                     "Publier un modèle est réservé au propriétaire / admin.", 403)
        if inp.excluded_url_prefixes is not None:
            # Périmètre d'URL (#605) : normalisé À LA POSE, refus nommé sur un motif
            # trop large (un hôte nu) ou malformé — rien n'est stocké d'un lot faux.
            try:
                prefixes = url_perimeter.normalize_prefixes(inp.excluded_url_prefixes)
            except url_perimeter.PerimeterError as e:
                raise AuthzDenied(400, "invalid_url_prefix",
                                  f"`excluded_url_prefixes` : {e.message}")
            db.set_project_excluded_url_prefixes(int(inp.project_id), prefixes)
        db.update_project(int(inp.project_id),
                          name=(inp.name.strip() if inp.name else None),
                          brief_md=inp.brief_md, is_template=inp.is_template,
                          icon=inp.icon)
        db.log_project_activity(int(inp.project_id), sub, "project.update", inp.name or None)
        return _view(db.get_project_by_id(int(inp.project_id)), sub)

    if inp.op == "copy":
        # Copier un projet qu'on peut LIRE (le sien ou un modèle) → nouveau projet
        # possédé par l'org active (ADR 0032 §7 B5a). L'original reste intact.
        _require(ownership.can_access(sub, RTYPE, rid, "read"), "forbidden", "Accès refusé.", 403)
        _require(inp.name and inp.name.strip(), "missing_name", "`name` (cible) requis.")
        _require(ctx.org_id is not None, "no_active_org", "Aucune org active.", 400)
        new_id, warnings = db.duplicate_project(int(inp.project_id), inp.name.strip(),
                                                "org", str(ctx.org_id), copied_by=sub)
        return {**_view(db.get_project_by_id(new_id), sub),
                "links": db.list_project_links(new_id), "copied_from": inp.project_id,
                "warnings": warnings}

    if inp.op in ("link", "unlink"):
        _require(ownership.can_access(sub, RTYPE, rid, "write"), "forbidden", "Écriture refusée.", 403)
        _require(inp.target_type and inp.target_ref, "missing_target",
                 "`target_type` et `target_ref` requis.")
        # ADR 0032 « stop using slug » : une procédure est référencée par l'ID STABLE de
        # le guide. On accepte un slug (naturel côté agent) OU un id et on stocke l'id
        # (idem à l'unlink pour matcher les lignes migrées).
        target_ref = inp.target_ref
        identity_ref = inp.identity_ref
        config = dict(inp.config) if inp.config else None
        proj_org = int(row["owner_id"]) if row.get("owner_type") == "org" else ctx.org_id
        if inp.target_type == "procedure":
            target_ref = _procedure_ref_to_id(proj_org, target_ref)
        elif inp.target_type == "tableau":
            # Normalise nom→id (le datastore du propriétaire du projet). Stocker l'id garde
            # la résolution cohérente ; un nom introuvable au LINK = erreur (pas de lien mort
            # silencieux), mais un unlink d'une réf legacy/supprimée passe avec la réf brute.
            resolved = _resolve_tableau_id(row, target_ref)
            if resolved is not None:
                target_ref = resolved
            elif inp.op == "link":
                _require(False, "unknown_tableau",
                         f"Aucun tableau nommé « {target_ref} » dans le datastore du projet.", 404)
        elif inp.target_type == "connecteur":
            # L'identité est la clé du BINDING (#57). Fin du doublon : on la sort de
            # config.identity_id vers `identity_ref`. `identity_ref` explicite (front B4 /
            # agent) = multi-binding ; sinon on prend l'identité du config (chemin legacy).
            legacy_id = config.pop("identity_id", None) if config else None
            if identity_ref is None:
                identity_ref = legacy_id or None
            # Binding à INSTANCE (ADR 0038 B5) : le lien désigne exactement UN credential
            # (ref B4). Validé + gardé AU LINK (le lieur doit avoir accès à l'instance ;
            # la résolution RE-gardera l'appelant). Exclusif d'identity_ref.
            if inp.op == "link" and inp.instance_ref:
                _require(identity_ref is None, "conflicting_binding",
                         "Donne `instance_ref` OU `identity_ref`, pas les deux "
                         "(le ref d'instance porte déjà le compte).")
                from ..mcp_errors import McpError
                from .. import access as access_mod, instance_refs
                try:
                    iref = instance_refs.parse_ref(inp.instance_ref)
                except ValueError:
                    _require(False, "invalid_instance_ref",
                             f"`instance_ref` invalide : {inp.instance_ref!r} "
                             "(un ref s'obtient via oto_instance(op='list')).")
                # Lot L6 : `inst:{id}` se PARSE mais ne se résout pas encore (L7).
                # Le refuser nommément évite le message « une instance `None` » que
                # produirait la garde de connecteur juste en dessous.
                _require(iref.level != "inst", "instance_not_pinnable",
                         "L'identifiant d'instance `inst:` n'est pas encore bindable : "
                         "repasse le `ref` rendu par oto_instance(op='list').")
                _require(iref.connector == target_ref, "instance_mismatch",
                         f"Ce ref est une instance `{iref.connector}`, pas "
                         f"`{target_ref}` (le connecteur du lien).")
                try:
                    access_mod.guard_instance_access(sub, iref)
                except McpError as e:
                    _require(False, "instance_forbidden", e.error.message, 403)
                config = dict(config or {})
                config["instance_ref"] = inp.instance_ref
            # Édition legacy (front actuel, pas d'identity_ref explicite) : s'il existe UN
            # binding unique avec une AUTRE identité, on le DÉPLACE (delete+insert) au lieu
            # d'en créer un 2e — préserve la sémantique « éditer le connecteur du projet ».
            if inp.op == "link" and inp.identity_ref is None:
                existing = [l for l in db.list_project_links(int(inp.project_id))
                            if l["target_type"] == "connecteur" and l["target_ref"] == target_ref]
                if len(existing) == 1 and existing[0].get("identity_ref") != identity_ref:
                    db.remove_project_link(int(inp.project_id), "connecteur", target_ref,
                                           identity_ref=existing[0].get("identity_ref"))
        # ADR 0035 (B2) : nom de slot bindé par ce lien — validé (hygiène de clé) puis
        # unicité (projet, slot) imposée par la DB (ValueError slot_taken → 409).
        slot = None
        if inp.op == "link" and inp.slot is not None:
            from .. import slots as slots_mod
            try:
                slot = slots_mod.normalize_name(inp.slot)
            except ValueError as e:
                _require(False, "invalid_slot", str(e), 400)
        if inp.op == "link":
            try:
                db.add_project_link(int(inp.project_id), inp.target_type, target_ref,
                                    inp.label, role=inp.role, config=config,
                                    identity_ref=identity_ref, slot=slot)
            except ValueError as e:
                code = "slot_taken" if str(e).startswith("slot_taken") else "bad_link"
                _require(False, code, str(e), 409 if code == "slot_taken" else 400)
        else:
            # #699 — un unlink ne mime plus un succès. Il vise TOUTES les écritures que la
            # réf demandée désigne (id canonique / nom-slug d'avant la normalisation), COMPTE
            # ce qu'il a retiré, et refuse nommément si ce compte est nul : `ok: true` sur un
            # no-op est pire qu'un refus — le lien restait dans les `links` de la réponse.
            demande = str(inp.target_ref).strip()
            existants = db.list_project_links(int(inp.project_id))
            refs = _unlink_refs(existants, inp.target_type, demande,
                                _ref_canonizer(row, proj_org, inp.target_type))
            removed = sum(db.remove_project_link(int(inp.project_id), inp.target_type, r,
                                                 identity_ref=identity_ref)
                          for r in refs)
            _require(removed, "link_not_found",
                     _rien_a_delier(existants, inp.target_type, demande), 404)
        db.log_project_activity(int(inp.project_id), sub, f"project.{inp.op}",
                                f"{inp.target_type}:{inp.label or target_ref}")
        out = {"ok": True, "id": inp.project_id,
               "links": db.list_project_links(int(inp.project_id))}
        if inp.op == "unlink":
            out["removed"] = removed
        # 0035 × 0046 — schéma CIBLE au binding d'un slot tableau : si une procédure
        # liée déclare ce slot avec un `schema`, un namespace vierge est PROVISIONNÉ
        # (le tableau naît avec son contrat — validation/lifecycle/clé) ; un schéma
        # déjà posé différent = warning non bloquant. Best-effort : jamais un link raté.
        if inp.op == "link" and inp.target_type == "tableau" and slot:
            try:
                from .. import slots as slots_mod
                target = slots_mod.target_schema_for(slot, out["links"])
                if target is not None:
                    res = slots_mod.provision_tableau_schema(int(target_ref), target)
                    out["slot_schema"] = res["status"]
                    if res.get("warning"):
                        out["warning"] = res["warning"]
            # noqa: SILENT — dette déclarée : le lot d'avertissements tombe d'un bloc (#424, verdict C)
            except Exception:  # noqa: BLE001 — provisionnement opportuniste
                pass
        # #218/#219 — lier un connecteur « aveugle » à un projet ORG-owned : si le
        # credential n'existe qu'au niveau d'une équipe de l'org (donc irrésoluble en
        # contexte projet), WARNING immédiat pointant le remède (transfert à l'équipe /
        # instance_ref). Non bloquant : on lie, mais l'intention incohérente est dite.
        if (inp.op == "link" and inp.target_type == "connecteur"
                and row.get("owner_type") == "org"):
            try:
                from .. import project_audit
                new_link = next((l for l in out["links"]
                                 if l.get("target_type") == "connecteur"
                                 and l.get("target_ref") == target_ref
                                 and l.get("identity_ref") == identity_ref), None)
                why = (project_audit._unresolvable_connector_why(
                    str(target_ref), int(row["owner_id"]), new_link)
                    if new_link else None)
                if why:
                    out["unresolvable_connector"] = why
                    out["warning"] = why
            # noqa: SILENT — dette déclarée : le lot d'avertissements tombe d'un bloc (#424, verdict C)
            except Exception:  # noqa: BLE001 — warning best-effort, le link a réussi
                pass
        # B5 — complétude au link : lier une procédure dont des slots ne sont pas
        # bindés ⇒ WARNING immédiat (non bloquant), le pendant des refs mortes 0014.
        if inp.op == "link" and inp.target_type == "procedure":
            try:
                from .. import org_store, project_audit
                instr = (org_store.get_instruction_by_id(int(target_ref))
                         if str(target_ref).isdigit() else None)
                missing = project_audit.unbound_slots_for(instr, out["links"]) if instr else []
                if missing:
                    out["unbound_slots"] = missing
                    out["warning"] = (
                        f"la procédure `{instr['slug']}` déclare des slots non bindés dans ce "
                        f"projet : {', '.join(missing)} — binde chacun "
                        f"(`oto_project op=link project_id={inp.project_id} target_type=… "
                        "target_ref=… slot='<name>'`) avant de l'exécuter ici.")
            # noqa: SILENT — dette déclarée : le lot d'avertissements tombe d'un bloc (#424, verdict C)
            except Exception:  # noqa: BLE001 — warning best-effort, le link a réussi
                pass
        return out

    if inp.op in ("publish_mcp", "unpublish_mcp"):
        # Publier un endpoint MCP = acte de gouvernance (URL publique au nom de l'org).
        _require(ownership.can_govern(sub, RTYPE, rid), "forbidden",
                 "Publier un endpoint MCP est réservé au propriétaire / admin.", 403)
        if inp.op == "unpublish_mcp":
            return unpublish_project_mcp(sub, int(inp.project_id))
        return publish_project_mcp(
            sub, row, access_mode=inp.mcp_access or "anonymous", mcp_slug=inp.mcp_slug,
            mcp_tools=inp.mcp_tools, expose_datastore=inp.mcp_expose_datastore,
            expose_datastore_write=inp.mcp_expose_datastore_write,
            expose_docs=inp.mcp_expose_docs,
            instructions_md=inp.mcp_instructions_md)

    # archive
    _require(ownership.can_govern(sub, RTYPE, rid), "forbidden",
             "Archivage réservé au propriétaire / admin.", 403)
    db.archive_project(int(inp.project_id))
    db.log_project_activity(int(inp.project_id), sub, "project.archive", row.get("name"))
    return {"ok": True, "id": inp.project_id, "archived": True}


# ── Procédures liées, servies sur demande (#313) ─────────────────────────────

# ⚠️ **L'ALLOWLIST EST LE MÉCANISME DE SÉCURITÉ, pas une commodité de sérialisation.**
# La contrainte est produit et non négociable : une procédure servie ne dit RIEN de
# son exécution — ni le modèle qui l'exécute, ni ceux qu'on essaie. Le rendu est donc
# CONSTRUIT champ par champ à partir de cette liste, jamais dérivé de la ligne
# (`{**instr}`) : une colonne ajoutée demain à `org_instructions` n'a aucun chemin
# pour atteindre le client. C'est ce que fige `test_procedure_payload_is_an_allowlist`.
#
# `slots` en est absent à dessein, bien qu'inoffensif : c'est de la mécanique
# d'exécution (quelle instance branchée où), pas la règle que le lecteur veut lire.
_PROCEDURE_FIELDS = ("ref", "slug", "title", "version", "body_md")


def _linked_procedures(sub: str, links: list[dict]) -> list[dict]:
    """Le corps des procédures liées au projet, dans l'ordre des liens.

    **Chaque procédure est regardée par le seam `ownership`, une par une**, et ce
    n'est pas une précaution redondante avec le gate du projet : un projet peut lier
    une procédure d'une AUTRE org (partage cross-org par grant, #52). Lire le projet
    n'emporte donc pas le droit de lire tout ce qu'il désigne — une procédure
    inaccessible est simplement ABSENTE du rendu, sans erreur : elle reste visible
    comme lien (titre, ref), c'est son corps qui ne suit pas.

    Un lien mort (procédure supprimée) est sauté de la même façon — `audit.dead_links`
    est l'endroit qui le SIGNALE ; ce rendu-ci n'a pas à le redire, et surtout pas à
    faire échouer la lecture du projet pour autant."""
    out = []
    for l in links:
        if l.get("target_type") != "procedure":
            continue
        ref = str(l.get("target_ref") or "")
        if not ref.isdigit():          # l'ADR 0032 a fixé l'id ; un slug résiduel n'est pas résolu ici
            continue
        if not ownership.can_access(sub, "doctrine", ref, "read"):
            continue
        instr = org_store.get_instruction_by_id(int(ref))
        if not instr:
            continue
        out.append({"ref": ref, "slug": instr["slug"], "title": instr["title"],
                    "version": instr["version"], "body_md": instr["body_md"]})
    return out


class ProjectReadInput(BaseModel):
    """Lire UN projet, désigné par l'URL."""
    project_id: int
    # ⚠️ `| str` n'est pas une facilité, c'est la FORME RÉELLE de cette entrée : cette
    # capacité n'a qu'une face REST en GET, donc son `include` arrive de la QUERY
    # STRING, où une URL ne sait pas dire « liste ». L'adaptateur verse
    # `{"include": "procedures"}` — une chaîne — et un champ `list[str]` nu la refuse.
    # Conséquence vécue (#367) : `GET /api/me/projects/12?include=procedures`, la
    # requête littérale du besoin partenaire ET celle que la description de cette
    # capacité annonce, répondait `400 invalid_input` depuis sa livraison du 13/08
    # (c46d81e) — livrée, testée, et inatteignable. Les tests d'alors vérifiaient que
    # le champ était DÉCLARÉ ; ils décrivaient l'intention, pas le montage.
    # Même patron que `node_rows.filter`, pour la même raison.
    include: Optional[list[str] | str] = None

    @field_validator("include", mode="after")
    @classmethod
    def _en_liste(cls, v):
        """La query string normalisée UNE fois, ici, pour que le handler ne voie
        jamais qu'une liste.

        La virgule sépare une valeur unique ; la forme répétée (`?include=a&include=b`)
        arrive déjà en liste depuis l'adaptateur (#418, 29/08 — avant, il ne gardait
        que la DERNIÈRE valeur, et ce docstring disait la forme inutilisable). Les
        deux se combinent : chaque entrée est découpée à son tour."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]


class ProjectAudit(BaseModel):
    """Santé des LIENS du projet — un contrôle, pas un blocage : un projet dont
    l'audit est plein reste parfaitement lisible et utilisable.

    **Les quatre listes sont toujours présentes, vides quand il n'y a rien.** Mais
    « vide » ne prouve pas « sain » : l'audit est best-effort, chaque vérification
    est enveloppée et un lien qui fait lever la sienne est simplement SAUTÉ (log
    serveur, pas d'erreur ici). Un audit tout vert peut donc être un audit partiel —
    ne pas en faire un feu vert automatique.

    ⚠️ Ce modèle décrit l'audit COMPLET, celui de la lecture par id. La LISTE de
    projets sert le même nom sous une forme allégée (checks en mémoire seuls, pour
    éviter un N+1) : `inert_procedures` y est toujours vide et les procédures
    cassées n'y remontent pas. Deux payloads, deux profondeurs."""
    # Lien dont la cible n'existe plus : namespace de tableau disparu, procédure qui
    # ne résout plus, connecteur absent du registre. `{target_type, target_ref, why}`.
    dead_links: list[dict]
    # Procédure liée dont des slots ne sont bindés par aucun lien de CE projet —
    # elle est exécutable mais incomplète. `{procedure, ref, slots}`.
    unbound_slots: list[dict]
    # Connecteur lié qu'AUCUN credential de l'org ne résoudrait aujourd'hui.
    # Calculé seulement pour un projet possédé par une ORG (le seul cas où « l'org »
    # désigne quelqu'un) : toujours vide sur un projet perso ou d'équipe — ce n'est
    # donc pas un signal d'absence de problème. `{target_ref, why}`.
    unresolvable_connectors: list[dict]
    # Slugs de procédures liées que les runs du projet n'ont jamais empruntées.
    # ⚠️ Reste vide tant que le projet n'a AUCUN run : sans historique, « inerte »
    # ne veut rien dire (un projet jeune n'est pas un projet mort).
    inert_procedures: list[str]


class ProjectRead(BaseModel):
    """UN projet lu par son id — exactement le payload d'`oto_project op=get`, même
    handler (ADR 0009 : la lecture par URL ne doit pas être une seconde
    implémentation qui dérive de la première).

    Deux choses que la forme ne dit pas d'elle-même :

    - **`can_write` est l'accès de l'APPELANT, recalculé à chaque lecture**, pas une
      propriété du projet. Un projet partagé en lecture seule le rend `false` alors
      que tout le reste du payload est identique à ce que voit son propriétaire —
      c'est le seul champ qui répond « et moi, j'ai le droit ? ».
    - **Un projet n'est lisible que DEPUIS l'org qui le possède** (gate de contexte,
      ADR 0023). Y accéder depuis une autre de ses orgs donne 404, pas 403 : le
      message dit alors de basculer d'org quand l'appelant y a bien accès, et reste
      non-disclosant sinon. Un 404 n'est donc PAS une preuve d'inexistence.

    Champs `mcp_*` : ils décrivent la PUBLICATION du projet (endpoint MCP dédié,
    partage navigable). `mcp_access == "off"` ⟹ rien n'est publié et `mcp_url` /
    `share_url` sont `null`."""
    id: int
    name: str
    # L'adresse web du projet chez CE lecteur (#599) — `null` quand son produit n'a
    # pas cette vue. Servie par le serveur, jamais reconstruite par l'appelant : un
    # patron d'URL appris par cœur fabrique des liens plausibles et faux le jour où
    # la route bouge. À ne pas confondre avec `mcp_url`/`share_url`, qui décrivent la
    # PUBLICATION du projet vers l'extérieur ; `url`, c'est où on le lit chez soi.
    url: Optional[str] = None
    icon: Optional[str] = None
    brief_md: str = ""
    owner_type: str                              # user | org | group | platform
    owner_id: str
    # Org de CONTEXTE d'un projet PERSO (« moi, dans cette org ») — `null` pour tout
    # projet non-perso, dont le contexte se dérive de l'owner. Servi en CHAÎNE.
    context_org_id: Optional[str] = None
    is_template: bool = False
    mcp_slug: Optional[str] = None
    mcp_access: str = "off"                      # off | anonymous | secret | org
    mcp_tools: list[str] = []
    mcp_expose_datastore: bool = False
    mcp_expose_datastore_write: bool = False
    mcp_expose_docs: bool = False
    # Prose servie au DESTINATAIRE de l'endpoint — ≠ `brief_md`, qui reste interne.
    mcp_instructions_md: str = ""
    # Périmètre d'URL (#605) : motifs canoniques `hôte/chemin/` (ou `hôte/*`) que les
    # outils de recherche écartent et que les outils d'extraction refusent sous ce projet.
    excluded_url_prefixes: list[str] = []
    mcp_url: Optional[str] = None                # dérivé du slug + du mode, jamais stocké
    share_url: Optional[str] = None              # mode `secret` uniquement
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    archived_at: Optional[str] = None            # non-null ⟹ projet archivé
    # Droit d'écriture de l'APPELANT sur ce projet (cf. docstring).
    can_write: bool
    # Liens typés (tableau | procedure | connecteur) enrichis à la lecture :
    # `namespace` (tableau) / `title` (procédure) résolus, et `cross_project: true`
    # quand la même cible est liée par un AUTRE projet — la toucher retombe ailleurs.
    links: list[dict]
    audit: ProjectAudit
    # Présent SEULEMENT si `include=['spine']` a été demandé — l'arbre des pages dans
    # l'ordre curé : `{pages, root_doc, tree[]}` (+ `more_roots` si des racines ont
    # été coupées). Borné (profondeur, 200 nœuds) : un nœud porte `more` = nombre de
    # descendants NON rendus. Une branche sans `children` n'est donc pas forcément
    # une feuille — vérifier `more`.
    spine: Optional[dict] = None
    # Présent SEULEMENT si `include=['procedures']` a été demandé (#313) — le CORPS
    # des procédures liées : `{ref, slug, title, version, body_md}`, et rien d'autre
    # (cf. `_PROCEDURE_FIELDS` : aucune métadonnée d'exécution, jamais).
    # ⚠️ La liste peut être plus COURTE que les liens de type `procedure` : une
    # procédure liée mais inaccessible à l'appelant (partage cross-org) ou supprimée
    # est absente ici tout en restant présente dans `links`. Apparier par `ref`.
    procedures: Optional[list[dict]] = None


def _project_read(ctx: ResolvedCtx, inp: ProjectReadInput) -> dict:
    """`op=get`, servi par une URL qui NOMME son projet.

    Même handler, mêmes gates : la lecture par id ne doit pas être une seconde
    implémentation qui dérive de la première (c'est tout l'objet de l'ADR 0009).

    Pourquoi une route de plus, alors que `POST /api/me/projects {"op":"get"}` fait
    déjà le travail : parce qu'un jeton PORTÉ ne peut pas être borné sur ce POST —
    sa cible vit dans le corps, et `token_scopes` ne lit que la méthode et le
    chemin. Une intégration à qui l'on confie un projet (et lui seul) a besoin que
    le projet s'adresse. C'est le même geste que pour les tableaux, qui se nomment
    déjà dans l'URL.
    """
    return _project(ctx, ProjectInput(op="get", project_id=inp.project_id,
                                      include=inp.include))


class ImportedProject(BaseModel):
    """Résultat d'un « Ajouter à mon Oto ». **`imported: false` n'est pas un échec** —
    l'opération est idempotente : re-forker un projet déjà importé, ou forker le sien,
    renvoie 200 en désignant l'existant. `reason` dit lequel des deux cas."""
    project_id: int
    imported: bool
    name: Optional[str] = None
    reason: Optional[str] = None                 # own_project | already_imported
    copied_from: Optional[int] = None            # présent seulement sur une vraie copie
    # Ce que la duplication n'a pas pu reprendre (structure copiée, jamais de secret).
    warnings: Optional[list] = None


CAPABILITIES += [
    Capability(
        key="me.project_read", handler=_project_read, Input=ProjectReadInput,
        authz=SUB_ONLY, Output=ProjectRead,
        description=(
            "Read ONE project by id (its brief, links and audit) — the same payload as "
            "oto_project op=get, on a URL that names its target. REST-only: agents "
            "already have oto_project. Exists so a SCOPED api token can be granted one "
            "project and nothing else ({\"projects\": {\"12\": \"read\"}}), which the "
            "POST form cannot express — its target sits in the body. "
            "Optional ?include=procedures adds the BODY of the linked procedures "
            "(title, version, body_md) so a reader can see the rule that produced a "
            "record; omitted, the response is byte-for-byte unchanged. Ask for "
            "several with ONE comma-separated value (?include=spine,procedures) — "
            "repeating the parameter keeps only the last one."
        ),
        mcp=None,
        # `:int` et non `{project_id}` nu : le motif de portée (`token_scopes`)
        # ne reconnaît qu'un id numérique. Une route plus permissive que sa
        # portée se laisserait atteindre pour être refusée juste après.
        rest=RestBinding("GET", "/api/me/projects/{project_id:int}"),
    ),
    Capability(
        key="me.project", handler=_project, Input=ProjectInput, authz=SUB_ONLY,
        description=(
            "Projects (organization layer, ADR 0030 owned resource). EVERY project carries "
            "`url` — the web address to OPEN it, in the reader's own product; hand it over "
            "as-is when asked \"where is it?\", never rebuild one from a pattern (`null` = "
            "that reader's product has no such view). op=create (name, "
            "optional brief_md; owner_type user|org + owner_id for a team project) / list "
            "(ORG-SCOPED: the ACTIVE org's projects + projects shared with it or with you — "
            "pass `org=<id>` to see another org's; every response echoes the "
            "effective org in `_org`. An INDEX: names and `brief_md_length`, NOT the briefs — "
            'read one with op=get, or pass `fields=["*"]` for whole records) / '
            "list_templates (published MODEL projects you can copy) / "
            "get (project + its links + an `audit` of those links: dead_links / unbound_slots / "
            "inert_procedures — a linked entity that no longer resolves surfaces HERE, act on it) / "
            "update (name, icon = an emoji shown in the lists and headers (\"\" clears it), brief_md, is_template = publish/unpublish "
            "as a copyable model, excluded_url_prefixes = URL prefixes such as `linkedin.com/in/` "
            "that search tools drop and extraction tools refuse under this project — a whole "
            "host must be written `host/*`, `[]` clears) / copy (deep-copy a project you can read — its own or a model "
            "— into a NEW project in your active org: brief + doc tree + links + raw files; "
            "a tableau link stays a POINTER to the same namespace by default (config.provision "
            "absent/`shared`), but with config.provision=`empty`|`seeded` it is PROVISIONED — a "
            "FRESH namespace (same schema, rows only if `seeded`) so each copy gets its own "
            "isolated table (e.g. a campaign template's lead pool). A `shared` tableau owned by "
            "ANOTHER org is re-provisioned EMPTY (never a pointer to the source's private data), "
            "and links whose namespace no longer resolves are skipped — both surfaced in the "
            "response `warnings`. Pass project_id = source + name = target) / handoff (a copy-paste « resume in Claude » blob "
            "that pre-writes the per-call `_project=` token for this project) / archive / link & unlink "
            "(attach an entity: "
            "target_type tableau|procedure|connecteur + target_ref = its id/slug/name, "
            "optional label + optional "
            "role = why this entity belongs to the project + optional config = the entity's "
            "PRE-MADE per-project override; for a connecteur: {identity_id?, instructions_md?} "
            "= which account to act as + prose instructions to apply (e.g. 'only filter "
            "agreements by the mutuelle theme'), or `instance_ref` (a ref from "
            "oto_instance op=list, ADR 0038 B5) to bind EXACTLY that credential — calls "
            "carrying this project's token then resolve it hard, no fallback; "
            "for a tableau: {provision?: shared|empty|seeded} "
            "= how a project copy treats it (empty/seeded = each copy gets its own fresh table). "
            "Optional `slot` = the SLOT NAME this link BINDS for the project (ADR 0035): "
            "procedures declare required entities as slots and reference them <slot:name> "
            "in their prose — the project maps each name to a concrete entity via its links. "
            "Slot names are a PROJECT-wide vocabulary (unique per project → 409 slot_taken; "
            "two linked procedures sharing `sortie` share the binding). "
            "Re-linking without role/config/slot preserves the "
            "existing ones. unlink returns `removed` = how many bindings it actually took "
            "out, and REFUSES (`link_not_found`) when it matched none — it never answers ok "
            "on a link it did not find. Give the `target_ref` as op=get renders it: an older "
            "link may still carry the NAME of its tableau (or the SLUG of its procedure) "
            "instead of the id, and unlink takes back either spelling. "
            "get/link return each link's role + slot + config + a derived "
            "`cross_project` flag (the same entity is linked by another project → avoid brutal "
            "edits / ask); a tableau link also returns its resolved `namespace` — address THIS "
            "project's table by that name with the data_* tools (never hardcode a namespace). "
            "Share & transfer go through oto_resource (resource_type='project') — this "
            "includes RE-PARENTING a project in place (same id, links, runs preserved): "
            "op=transfer new_owner_group=<id> hands it to a TEAM so the project and its "
            "connector credentials sit at the SAME level (the team's secrets then resolve "
            "when you open it), new_owner_org=<id> to an org, new_owner_email to a user. "
            "(op=update only changes name/icon/brief_md/is_template — never the owner; op=copy "
            "makes a NEW id.) "
            "inventory = the project's DERIVED surface (union of the linked procedures' "
            "<tool:> refs + tools actually used by the project's runs, plus connectors "
            "from links & declared slots) — never retype a tool list: derive, then curate. "
            "runs (optional target_ref = a linked procedure's stable id) = the project's "
            "recent runs (label/guide/outcome), filtered to that procedure when given. "
            "OMIT project_id on op=runs and you get YOUR OWN still-open runs instead, "
            "each with its `run_id` — that is how you find a run you opened and lost "
            "the id of, so you can finally close it with run_finish. Across every org, "
            "since a run you cannot find is usually one you opened elsewhere. "
            "lint (optional stale_days, default 90) = KB health of this project's pages: "
            "stale (untouched since), empty (trivial body), duplicate_titles (likely merges). "
            "publish_mcp (mcp_slug + mcp_access anonymous|secret|org + mcp_tools = the fixed "
            "tool allowlist) publishes the project as a dedicated MCP endpoint "
            "`<mcp_slug>.mcp.oto.cx/mcp`, the toolset served under the OWNER ORG's credentials — "
            "`anonymous` = no login + LISTED in the public directory; `secret` = no login but "
            "UNLISTED, the slug is server-generated & unguessable (a secret URL; mcp_slug is an "
            "optional readable prefix); `org` = Logto JWT + pins the org. For anonymous/secret, "
            "tools that aren't credential-less or resolvable for the org are published anyway but "
            "FAIL cleanly at call time — they come back in `mcp_unresolvable_tools` (configure an "
            "org key or drop them). mcp_expose_datastore (SECRET only) opts the `data_*` tools "
            "in: they then act under the OWNER ORG's authority (read/write the org's namespaces) "
            "without a login — off by default (the datastore stays private); refused on "
            "anonymous/org. unpublish_mcp removes it. get returns "
            "mcp_slug/mcp_access/mcp_tools/mcp_expose_datastore/mcp_url."
        ),
        mcp="oto_project",
        rest=RestBinding("POST", "/api/me/projects"),
    ),
]


# ── « Projet actif » = jeton d'appel (ADR 0038 B3b — le bracelet est retiré) ──
# `oto_use_project` ne pose PLUS d'état de session : le contexte projet est porté
# par le jeton `_project=` de CHAQUE appel de travail (l'axe co-pose l'org du projet,
# résout les slots et épingle les identités connecteur préfaites). Ce tool valide
# l'accès et renvoie le geste fiable + les surcharges préfaites (informatif).


class UseProjectInput(BaseModel):
    project_id: int   # id d'un projet auquel tu as accès (cf. oto_project op=list)


class NoInput(BaseModel):
    pass


def _use_project(ctx: ResolvedCtx, inp: UseProjectInput) -> dict:
    """Hint SANS ÉTAT (ADR 0038 B3b) : valide l'accès au projet et renvoie le geste
    fiable (`_project=` par appel) + ses surcharges connecteur préfaites."""
    row = db.get_project_by_id(inp.project_id)
    _require(row is not None, "unknown_project", f"Projet #{inp.project_id} inconnu.", 404)
    _require_active_org_visible(ctx, row)
    # Surcharges connecteur préfaites portées par ce projet (informatif pour l'agent).
    overrides = [{"connector": l["target_ref"], "config": l.get("config") or {}}
                 for l in db.list_project_links(inp.project_id)
                 if l.get("target_type") == "connecteur" and (l.get("config") or {})]
    return {
        "project": inp.project_id, "name": row.get("name"),
        "connector_overrides": overrides, "session_state": None,
        "how_to": (f"Aucun état de session (ADR 0038) : passe `project={inp.project_id}` "
                   "sur CHAQUE appel de travail fait pour ce projet (connecteurs et "
                   "data_* l'acceptent — l'org du projet, ses slots et ses identités "
                   f"préfaites en découlent). Puis recharge le contexte de l'org du "
                   f"projet (readme d'org+équipe, guides, procédures — figé à la "
                   f"connexion) via `oto_context(project={inp.project_id})`."),
    }


def _clear_project(ctx: ResolvedCtx, inp: NoInput) -> dict:
    """Hint sans état (ADR 0038 B3b) : hors projet = simplement ne pas passer `_project=`."""
    return {"session_state": None,
            "how_to": ("Aucun état de session à effacer (ADR 0038) : un appel sans "
                       "`_project=` est hors projet par construction.")}


# ── « Ajouter à mon Oto » : forker un projet PUBLIÉ par slug (canal d'acquisition) ──
class ImportProjectInput(BaseModel):
    slug: str   # mcp_slug d'un projet publié (partage `<slug>.share.oto.cx` / `<slug>.mcp.oto.cx`)


def _import_project(ctx: ResolvedCtx, inp: ImportProjectInput) -> dict:
    """« Ajouter à mon Oto » : forke un projet PUBLIÉ (résolu par slug) dans l'org ACTIVE
    de l'appelant, ou RÉCUPÈRE la copie déjà présente (idempotent). Copie la STRUCTURE
    (brief + docs + liens + fichiers ; un tableau d'une autre org est re-provisionné à
    vide par `duplicate_project` — anti-fuite) — JAMAIS les credentials (org-scopés). Le
    slug d'un partage `secret` est non devinable → le posséder = consentement au fork ;
    `anonymous` est déjà listé publiquement. La source reste intacte."""
    slug = (inp.slug or "").strip().lower()
    _require(bool(slug), "missing_slug", "`slug` requis.", 400)
    src = db.get_project_by_mcp_slug(slug)
    _require(src is not None, "unknown_project", "Aucun projet partagé pour ce lien.", 404)
    _require((src.get("mcp_access") or "off") in ("anonymous", "secret"), "not_importable",
             "Ce projet n'est pas partagé publiquement (import réservé aux partages "
             "anonymous/secret).", 403)
    src_id = int(src["id"])
    org_id = ctx.org_id
    # Déjà à moi : la source EST possédée par mon org active → rien à forker, on l'ouvre.
    if src.get("owner_type") == "org" and str(src.get("owner_id")) == str(org_id):
        return {"project_id": src_id, "imported": False, "reason": "own_project",
                "name": src.get("name")}
    # Idempotent : une copie déjà forkée dans cette org → on la récupère (« si déjà dans
    # ton compte »), pas de doublon.
    existing = db.find_copied_project("org", str(org_id), src_id)
    if existing is not None:
        return {"project_id": int(existing["id"]), "imported": False,
                "reason": "already_imported", "name": existing.get("name")}
    new_id, warnings = db.duplicate_project(
        src_id, src.get("name") or "Projet importé", "org", str(org_id),
        copied_by=ctx.sub, track_source=True)
    db.log_project_activity(new_id, ctx.sub, "project.import", f"from #{src_id} ({slug})")
    return {"project_id": new_id, "imported": True, "name": src.get("name"),
            "copied_from": src_id, "warnings": warnings}


CAPABILITIES += [
    Capability(
        key="me.import_project", handler=_import_project, Input=ImportProjectInput,
        Output=ImportedProject,
        authz=ORG_MEMBER,
        description=(
            "« Add to my Oto »: FORK a PUBLISHED project (resolved by its share slug) into "
            "your ACTIVE org, or RETURN the copy you already imported (idempotent). Copies the "
            "STRUCTURE (brief + docs + links + files; a tableau owned by another org is "
            "re-provisioned EMPTY) — NEVER credentials. Source stays intact. Powers the public "
            "share page's acquisition CTA; the dashboard calls it after login."
        ),
        # Canal d'acquisition dashboard-only (login géré côté dashboard) — pas d'outil MCP.
        mcp=None,
        rest=RestBinding("POST", "/api/me/projects/import"),
    ),
    Capability(
        key="me.use_project", handler=_use_project, Input=UseProjectInput, authz=SUB_ONLY,
        description=(
            "Resolve a project you can access (project_id from oto_project op=list) and "
            "get the RELIABLE way to work in it. NO session state (ADR 0038): pass "
            "`project=<id>` directly on each work call — the project's org, slot "
            "bindings and PRE-MADE connector identities all derive from that token. "
            "Returns the project's connector overrides."
        ),
        mcp="oto_use_project",
    ),
    Capability(
        key="me.clear_project", handler=_clear_project, Input=NoInput, authz=SUB_ONLY,
        description=("No-op hint (ADR 0038: no session state — a call without "
                     "`_project=` is out of any project by construction)."),
        mcp="oto_clear_project",
    ),
]
