"""Le nom d'un outil, tel que le PRODUIT de l'utilisateur l'affiche (ADR 0052).

Un client d'un partenaire voit, dans sa conversation, la liste des outils qu'il vient
d'appeler. Elle disait `Oto doc`, `Oto project`, `Oto search` — sous le connecteur du
partenaire, dans son produit à lui. C'est la MÊME famille de défaut que le socle
d'instructions (« Sur Tulina (Oto), tu es… », 13/08) et que les liens qui portaient
notre domaine : un texte au niveau plateforme alors qu'il décrit un produit. Sauf
qu'ici le texte n'est pas de la prose — c'est l'identifiant d'un outil, et il est
affiché à chaque appel.

Le préfixe `oto_` reste donc le nom CANONIQUE partout où un nom se stocke ou se
compare (registre, coffre de visibilité `user_disabled_tools`, journal `tool_calls`,
références `<tool:slug>` des procédures, gates par namespace). Ce module ne fait que
le TRADUIRE aux deux bords du protocole :

    tools/list   →  `oto_doc`     devient  `tulina_doc`   (ce que l'utilisateur voit)
    tools/call   ←  `tulina_doc`  redevient `oto_doc`     (ce que le serveur sait)

**Les deux noms sont acceptés à l'appel**, et ce n'est pas de la complaisance : la
prose déjà écrite (procédures d'org, guides, corps de doctrine, messages d'erreur)
cite les noms canoniques et personne ne peut la réécrire d'un coup. Un agent qui suit
une procédure de 2026-07 doit continuer à aboutir.

Trois choses qui coûteraient cher si on les oubliait :

- **Rien n'est renommé par défaut.** Le préfixe est DÉCLARÉ par le tenant
  (`tenants.tool_prefix`, NULL = inerte), jamais dérivé du slug. Renommer les outils
  d'un tenant est une rupture pour ses procédures et sa prose : ça se décide, ça ne
  s'attrape pas en existant. Même règle que `link_paths` — pas de patron, pas de lien.
- **Un préfixe ne peut pas être un namespace de connecteur** (`normalize_prefix`).
  Sinon `tulina_search` désignerait à la fois l'alias d'`oto_search` et un vrai outil
  du connecteur `tulina` : la traduction retour deviendrait ambiguë, et l'ambiguïté
  ici décide QUEL outil s'exécute. Le refus est loggé, jamais silencieux — un tenant
  dont le préfixe est refusé garde les noms canoniques, ce qui est visible.
- **La traduction est un PRÉFIXE, pas une table.** `oto_<reste>` ⇄ `<prefix>_<reste>`,
  total et réversible. Une table nom-à-nom aurait dérivé au premier outil ajouté.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Le namespace des outils de la plateforme — le seul renommé. `data_*` (substrat de
# stockage), `run_*`/`feedback` (boucle d'usage) et les connecteurs portent des noms
# de CAPACITÉ ou de FOURNISSEUR, pas notre marque : les renommer les rendrait
# méconnaissables sans rien corriger.
PRIMARY_PREFIX = "oto"

# Un préfixe entre dans un nom d'outil MCP : minuscules et chiffres, rien d'autre.
# Pas de `_` (il porte le découpage en namespace, cf. `tool_visibility.namespace_of`),
# pas de `-` (un slug de tenant en accepte un, un nom d'outil non).
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,23}$")

# Les namespaces SPINE de la plateforme : un préfixe qui vaudrait l'un d'eux ferait
# collisionner l'alias avec un outil réel (`data_write`, `run_start`, `feedback`).
_SPINE_PREFIXES = frozenset({PRIMARY_PREFIX, "data", "run", "feedback"})

# Un nom d'outil de la plateforme, tel qu'il apparaît DANS la prose injectée au
# handshake (`oto_doc`, `oto_connector`…). Le token `oto_<identifiant>` est réservé
# par construction — c'est un espace de noms que la plateforme s'est donné — donc le
# rencontrer dans l'artefact de session, c'est rencontrer un outil. Garde-fou :
# `tests/test_tool_alias_par_tenant.py` vérifie sur le MONTAGE RÉEL que chaque token
# de cette forme présent dans la prose servie EST un outil du registre.
_PROSE_RE = re.compile(r"\b" + PRIMARY_PREFIX + r"_([a-z][a-z0-9_]*)\b")


def normalize_prefix(value) -> str:
    """Le préfixe utilisable déclaré par un tenant, ou `""` (= aucun renommage).

    Refuse — en le LOGGANT — une forme qui ne peut pas être un nom d'outil, et un
    préfixe qui collisionnerait avec un namespace existant. Un refus rend `""`, donc
    les noms canoniques : la dégradation est le comportement d'avant ce lot.

    ⚠️ **Rien n'est réparé au passage** — ni espaces rognés, ni majuscules abaissées.
    Même règle que le slug d'un tenant (`tenancy.build`) : cette valeur entre dans un
    identifiant, donc elle vaut EXACTEMENT ce qui est déclaré. `Acme` corrigé en
    silence ferait dire `Acme` à l'écran de suivi et `acme_doc` aux outils, et
    personne n'irait chercher l'écart là.
    """
    prefix = str(value or "")
    if not prefix:
        return ""
    if not _PREFIX_RE.match(prefix):
        logger.warning(
            "préfixe d'outils %r refusé : un préfixe est [a-z][a-z0-9]{1,23} — ni `_` "
            "(il découpe le namespace), ni `-` (un slug en accepte, un nom d'outil "
            "non), ni une majuscule ou un espace, qu'on ne corrige pas en silence",
            prefix)
        return ""
    if prefix in _SPINE_PREFIXES or _is_connector_namespace(prefix):
        logger.warning(
            "préfixe d'outils %r refusé : ce namespace porte déjà des outils — "
            "`%s_…` désignerait à la fois un alias et un outil réel, et la traduction "
            "retour choisirait au hasard lequel exécuter", prefix, prefix)
        return ""
    return prefix


def _is_connector_namespace(prefix: str) -> bool:
    """Le préfixe est-il le namespace d'un connecteur déclaré ? Import LOCAL : ce
    module est importé par le middleware, `providers` tire tout le registre."""
    try:
        from . import providers
        return providers.connector_for_namespace(prefix) is not None
    except Exception:  # noqa: BLE001 — un registre illisible ne doit pas ouvrir la porte
        logger.warning("registre des connecteurs illisible : préfixe %r refusé par "
                       "précaution", prefix, exc_info=True)
        return True


def _tenant_entry(sub: Optional[str]):
    """L'entrée de registre du tenant TIERS de ce compte, ou None (compte de la
    plateforme, sub absent, registre illisible).

    **Aucun accès DB** — le registre d'émetteurs est en mémoire (bâti au boot). C'est
    ce qui autorise l'appel depuis un middleware, dans la boucle (serveur MONO-LOOP).
    """
    if not sub:
        return None
    try:
        from . import tenancy
        registre = tenancy.current()
        slug = registre.tenant_of(sub)
        if not slug or slug == tenancy.PRIMARY_SLUG:
            return None
        return next((e for e in registre.entries() if e.slug == slug), None)
    except Exception:  # noqa: BLE001 — un nom d'outil ne casse jamais un appel
        logger.warning("résolution du tenant impossible (fail-open)", exc_info=True)
        return None


def prefix_for(sub: Optional[str]) -> str:
    """Le préfixe d'outils du tenant de ce compte, ou `""`."""
    entry = _tenant_entry(sub)
    return normalize_prefix(getattr(entry, "tool_prefix", "")) if entry else ""


def server_identity_for(sub: Optional[str]) -> tuple[str, str]:
    """`(name, title)` que le handshake `initialize` doit annoncer — `("", "")` =
    inchangé (compte de la plateforme, tenant sans déclaration, erreur).

    Même défaut, dernier recoin : `serverInfo.name` valait `oto` dans le produit
    d'un partenaire, alors que les outils s'y appellent `<prefix>_…`. Même doctrine
    que le préfixe et les liens : **rien n'est renommé par défaut** — `name` suit le
    `tool_prefix` déclaré (l'identifiant, cohérent avec les noms d'outils), `title`
    suit le `name` du tenant (le libellé humain, celui du PRM). Un tenant qui n'a
    rien déclaré garde l'annonce d'avant, et ça se voit — pas de patron, pas de nom.
    """
    entry = _tenant_entry(sub)
    if entry is None:
        return ("", "")
    return (normalize_prefix(getattr(entry, "tool_prefix", "")),
            str(getattr(entry, "name", "") or ""))


def public(name: str, prefix: str) -> str:
    """Le nom MONTRÉ : `oto_doc` → `tulina_doc`. Tout autre nom passe inchangé."""
    if not prefix or not name or not name.startswith(PRIMARY_PREFIX + "_"):
        return name
    return prefix + name[len(PRIMARY_PREFIX):]


def canonical(name: str, prefix: str) -> str:
    """Le nom CONNU DU SERVEUR : `tulina_doc` → `oto_doc`.

    Le nom canonique passe inchangé — les deux formes sont acceptées à l'appel (cf.
    l'en-tête du module : la prose déjà écrite cite les canoniques).
    """
    if not prefix or not name or not name.startswith(prefix + "_"):
        return name
    return PRIMARY_PREFIX + name[len(prefix):]


def public_namespace(namespace: str, prefix: str) -> str:
    """Le namespace MONTRÉ : `oto` → `tulina`. Tout autre namespace passe inchangé.

    Un nom d'outil ne voyage jamais seul — `oto_tool_schema` en rend aussi le
    namespace, que l'agent relit pour se repérer. Le laisser en canonique ferait
    répondre « namespace: oto » à un compte dont tous les outils s'appellent
    `tulina_…`, soit le nom interne réintroduit par la porte de derrière.
    """
    return prefix if (prefix and namespace == PRIMARY_PREFIX) else namespace


def rewrite_prose(text: str, prefix: str) -> str:
    """Les noms d'outils cités dans un TEXTE servi à l'agent, au nom du produit.

    Sans ça le renommage se retourne contre lui-même : l'artefact de session prescrit
    `oto_doc`, l'agent l'appelle (le serveur l'accepte), et le client réaffiche
    `Oto doc` — le nom qu'on voulait faire disparaître, à l'endroit exact où il se
    voit. Traduire la liste sans traduire la consigne ne corrige donc rien.
    """
    if not prefix or not text or (PRIMARY_PREFIX + "_") not in text:
        # Sortie sèche avant toute regex : appelée sur les ~480 descriptions du
        # `tools/list` (200 Ko), dont 27 seulement citent un outil. Le `in` coûte un
        # scan mémoire, la substitution un automate — et le serveur est MONO-LOOP.
        return text
    return _PROSE_RE.sub(lambda m: f"{prefix}_{m.group(1)}", text)
