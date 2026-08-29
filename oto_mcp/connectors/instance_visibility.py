"""Qui VOIT une instance de connecteur — R9, tranché par Alexis le 2026-08-27.

**Le verdict.** « La visibilité est une propriété de l'INSTANCE », dérivée de la chaîne
d'accès : *découvrable par les scopes sous son propriétaire, dans la même org, jamais
cross-org*, avec **surcharge explicite** par le propriétaire. Ce module est cette
dérivation, et rien d'autre.

**Ce qu'il ne fait PAS, et c'est la moitié du lot.** Il ne filtre rien, il ne gate
aucun appel, il n'élargit aucune liste. Un non-membre continue de voir *aucune clé
configurée* — la question de la divulgation (« il existe un accès à demander, et chez
qui ») reste **produit**, et R9 la range dans un réglage d'org opt-in, plus tard. Ce
qui est livré ici est **descriptif** : la même liste qu'avant, où chaque instance dit
désormais qui la voit.

**Pourquoi la question a une réponse dérivable.** D2 a retiré l'enjeu de protection :
masquer ne protège de rien, tout se refuse à l'appel. Ce qui reste est donc une
question d'ergonomie, et elle a une réponse honnête — *qui peut la résoudre la voit*.
La résolution, elle, est déjà écrite une fois pour toutes dans le walker
(`access.cascade.walk_cascade`) ; ce module l'INVERSE.

⚠️ **Inverser un walker, c'est risquer d'en écrire une deuxième copie** — le défaut
exact que `keyStack.ts` porte déjà côté dashboard, et dont on sait qu'il ne casse pas :
il MENT. Deux garde-fous, tous deux mécaniques :

1. **Aucune règle n'est recopiée.** Les gates sont lus à leur SOURCE — le registre
   (`providers.is_org_shareable`, `auth_modes`), le partage du coffre
   (`share_mode`/`share_down`/`share_side`, mêmes colonnes que
   `access.cascade._platform_instance_usable`), et la chaîne (`grants_chain`). Un
   registre consulté deux fois n'est pas une duplication ; une liste recopiée en
   serait une.
2. **Un test les confronte** (`tests/test_instance_visibility.py`) : pour chaque
   palier, l'audience dérivée ici et le verdict réel de `walk_cascade` doivent
   s'accorder sur un vrai PostgreSQL. C'est ce test, pas ce commentaire, qui empêche
   la divergence.

**Le vocabulaire des scopes** est celui des arêtes de `grants` et de `share_down` :
`user:<sub>` · `group:<id>` · `org:<id>` · `platform` (tout le monde — le free-tier).
Il n'y a pas de scope « personne » : l'absence d'audience est la liste vide.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from .. import credentials_store, grants_chain, providers

logger = logging.getLogger(__name__)

# L'audience « tout le monde » : une clé plateforme en free-tier, qu'aucune allowlist
# ne referme. Ce n'est pas un scope de `grants` (elle n'y désigne personne) — c'est le
# mot que rend une audience NON BORNÉE, et il fallait qu'elle en ait un : rendre la
# liste de tous les subs serait faux (elle change à chaque inscription) et rendre la
# liste vide serait un contresens (vide = personne).
EVERYONE = "platform"

# Les trois surcharges du propriétaire (colonne `connector_instances.visibility`).
# ⚠️ **Rien ne les POSE encore** : aucune surface n'écrit cette colonne, elle vaut
# `inherited` partout. Les deux autres branches sont écrites et testées, pas servies —
# le geste qui les pose est un lot produit (R9 : « un réglage d'org, opt-in »).
INHERITED, HIDDEN, ORG_WIDE = "inherited", "hidden", "org"


def _owner_scope(owner_type: str, owner_id: str) -> Optional[str]:
    """Le scope du PROPRIÉTAIRE — celui qui voit son instance quoi qu'il arrive.

    `member` porte `{org}:{sub}` : le propriétaire est la PERSONNE, pas le couple. Un
    `user` (résidu des mounts OAuth, ADR 0033) est déjà un sub nu. `platform` n'a pas
    d'id (convention maison) et n'a donc pas de propriétaire nommable."""
    if owner_type == credentials_store.MEMBER:
        _, _, sub = (owner_id or "").partition(":")
        return f"user:{sub}" if sub else None
    if owner_type == "user":
        return f"user:{owner_id}" if owner_id else None
    if owner_type in ("org", "group", credentials_store.TENANT):
        # Le scope du tenant est celui des arêtes de `grants` (`tenant:<slug>`, L-clés).
        return f"{owner_type}:{owner_id}" if owner_id else None
    return None


def _owner_org(owner_type: str, owner_id: str) -> Optional[str]:
    """L'org du propriétaire, quand elle se lit SANS requête.

    Sert la seule surcharge `org` (« que toute l'org la découvre »). Un `group` ne
    porte pas son org dans son id : il faudrait un lookup, et il n'y a aujourd'hui
    aucune ligne à surcharger — on rend None plutôt que d'ouvrir un N+1 pour un cas
    que rien ne produit. Le lot qui POSE la surcharge fera ce lookup à l'écriture, où
    il coûte une fois."""
    if owner_type == credentials_store.MEMBER:
        org, _, _ = (owner_id or "").partition(":")
        return f"org:{org}" if org.isdigit() else None
    if owner_type == "org":
        return f"org:{owner_id}" if str(owner_id).isdigit() else None
    return None


def _platform_audience(connector: str, share_mode: str, share_down: Sequence,
                       label: str) -> list[str]:
    """Qui résout une clé PLATEFORME — miroir de `access.cascade._platform_instance_usable`
    et de `_platform_grant_meta`, lu sur les MÊMES colonnes, jamais sur une copie.

    Trois issues, dans l'ordre où la résolution les prend :

    - **la chaîne ACCORDE** (0053, L5) — l'audience EST l'ensemble des bénéficiaires
      des arêtes vivantes ;
    - **la chaîne REFUSE** — des arêtes existent, toutes révoquées : plus personne, et
      **sans repli** (c'est ce qui rend une révocation vraie) ;
    - **la chaîne est MUETTE** (connecteur non basculé, ou aucune arête n'a jamais visé
      cette clé) — l'ancien chemin, à l'identique : `closed` ⟹ l'allowlist et rien
      d'autre ; `open` ⟹ l'allowlist si elle existe, sinon **tout le monde** (le
      free-tier).
    """
    con = providers.REGISTRY.get(connector)
    if not (con and "platform" in con.auth_modes):
        # Le palier plateforme de la cascade est gaté sur `auth_modes` : un connecteur
        # byo-only ne résout JAMAIS une clé plateforme. En annoncer une audience serait
        # le mensonge que la revue B4 avait déjà relevé sur la projection.
        return []
    down = [str(s) for s in (share_down or [])]
    if grants_chain.is_chained(connector):
        ref = grants_chain.instance_ref(label, connector)
        vivantes, existent = _chain_grantees(ref)
        if existent:
            return vivantes
        # MUETTE → l'ancien chemin, sans une branche de plus.
    if share_mode == "closed":
        return down
    return down or [EVERYONE]


def _chain_grantees(resource_id: str) -> tuple[list[str], bool]:
    """(scopes des arêtes VIVANTES, « des arêtes existent-elles, révoquées comprises »).

    Le second terme est ce qui distingue REFUSE de MUETTE, et il ne se déduit pas du
    premier : « aucun bénéficiaire vivant » veut dire *plus personne* si des arêtes ont
    existé, et *l'ancien chemin* si aucune n'a jamais existé.

    Fail-open loggé, comme le reste de cette surface : `visible_to` est descriptif et
    n'est consommé par rien: faire tomber le listing des clés de quelqu'un parce que la
    table des arêtes n'a pas répondu serait hors de proportion. On rend alors « aucune
    arête », ce qui renvoie au chemin legacy — l'audience la plus large, donc jamais un
    faux « personne ne la voit »."""
    from ..db import grants as db_grants
    try:
        return (db_grants.live_grantees_for_resource(resource_id),
                bool(db_grants.resource_ids_with_edges([resource_id])))
    except Exception:
        logger.warning("visibilité d'instance : arêtes indisponibles (fail-open)",
                       exc_info=True)
        return ([], False)


def derive(owner_type: str, owner_id: str, connector: str, *, account: str = "",
           visibility: str = INHERITED, share_mode: str = "open",
           share_down: Sequence = (), share_side: Sequence = ()) -> list[str]:
    """Les scopes qui DÉCOUVRENT cette instance. Trié, dédoublonné, jamais None.

    Pure au sens qui compte : tout ce qui varie est un ARGUMENT, sauf le registre (une
    constante du process) et — pour une clé plateforme d'un connecteur basculé — les
    arêtes. Le reste (le partage, la surcharge) est lu par l'appelant, en lot.

    ⚠️ `visible_to` répond « qui la DÉCOUVRE », pas « qui l'utilise ». Les deux
    coïncident par défaut (`inherited`) — c'est tout l'intérêt d'une visibilité
    dérivée. Une surcharge `hidden` les sépare volontairement : celui qui résout
    continue de résoudre, il cesse seulement de la voir listée comme un objet
    partageable. Masquer ne protège de rien (D2), donc ce n'est pas un cran de
    sécurité : c'est un cran d'ERGONOMIE, et il est dit ici pour qu'on ne le prenne
    jamais pour l'autre."""
    proprietaire = _owner_scope(owner_type, owner_id)
    if visibility == HIDDEN:
        return [proprietaire] if proprietaire else []

    if owner_type == credentials_store.PLATFORM:
        audience = _platform_audience(connector, share_mode, share_down,
                                      label=str(owner_id))
    elif owner_type in ("org", "group", credentials_store.TENANT):
        # Les paliers PARTAGÉS (équipe, org, tenant — L-clés PR 1) ne sont traversés
        # par le walker que pour un connecteur org-partageable : une clé d'équipe sur
        # un connecteur par-personne existe au coffre et n'est lue par personne.
        # L'annoncer visible serait faux.
        audience = [proprietaire] if (proprietaire and
                                      providers.is_org_shareable(connector)) else []
    else:
        # Membre (et le résidu `user`) : la clé de quelqu'un n'est vue que de lui.
        # Jamais cross-org — l'instance cross-org de #172 est la MIENNE vue d'ailleurs,
        # donc le même scope `user:`, pas un scope de plus.
        audience = [proprietaire] if proprietaire else []

    # Les prêts nominatifs (ADR 0044 `share_side`) sont une EXTENSION : ils s'ajoutent
    # toujours, à tous les paliers, et peuvent viser hors de l'org du propriétaire —
    # c'est un acte explicite de celui-ci, pas une découverte.
    audience += [str(s) for s in (share_side or [])]

    if visibility == ORG_WIDE:
        org = _owner_org(owner_type, owner_id)
        if org:
            audience.append(org)
    return sorted(set(a for a in audience if a))
