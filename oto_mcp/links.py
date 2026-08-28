"""Les liens qu'on rend à un utilisateur — et à quoi ils ressemblent CHEZ LUI.

Un client d'un partenaire recevait des liens vers notre tableau de bord : un produit
qu'il n'a pas. La première correction a fait suivre l'ADRESSE au tenant — insuffisant,
et le code du partenaire le prouve : **aucun de ses chemins ne ressemble aux nôtres**
(`/network/<org>/knowledge/<id>` là où nous servons `/docs/<id>`), et pour certaines
de nos vues il **n'a aucun équivalent** — ses tableaux n'existent pas. Coller nos
chemins sous son domaine aurait donc fabriqué des liens morts, ce qui est pire qu'un
lien à notre marque : un lien mort ne se diagnostique pas, il se subit.

D'où un patron par TYPE de lien, déclaré par le tenant, et une règle simple :

    pas de patron ⟹ pas de lien.

**Deux familles, et les confondre casse un parcours** :

- un lien **AFFICHÉ** (un tableau, une page partagée) peut ne pas exister : on n'écrit
  rien plutôt que d'envoyer quelque part. `link_for` rend alors `None`, et l'appelant
  omet le lien — il a toujours de quoi se rendre utile sans lui.
- une **REDIRECTION** (le retour d'une connexion OAuth) doit TOUJOURS aboutir : on ne
  peut pas « ne pas rediriger ». Sans patron, elle retombe sur la nôtre — l'utilisateur
  voit notre marque une fois, ce qui vaut mieux qu'une page blanche au milieu d'une
  connexion. C'est `redirect_for`, et c'est le seul chemin qui replie ainsi.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import config

logger = logging.getLogger(__name__)

# Nos propres chemins, par type. Source unique : ce que le tenant `oto` sert, et le
# défaut de tout tenant qui ne déclare rien. `{…}` = paramètres nommés du lien.
DEFAULT_PATHS: dict[str, str] = {
    "home": "",
    "table": "/data/{id}",
    "public_doc": "/p/d/{token}",
    # ⚠️ A dit `/docs/{id}` du 2026-08-13 (41e7928) au 2026-08-28 — un chemin que
    # NOTRE tableau de bord ne route pas : son routeur ne connaît que la section
    # `/documents` (sans id), et son attrape-tout renvoie tout chemin inconnu sur
    # `/overview`. Le lien n'aurait donc pas affiché d'erreur : il aurait ouvert la
    # page d'accueil en se faisant passer pour la page demandée. Il n'a jamais eu
    # d'appelant, ce qui l'a gardé invisible — et c'est très exactement le lien mort
    # que ce module existe pour interdire, posé chez nous. Le VRAI chemin d'une page,
    # celui que le front lui-même écrit (`searchNav.ts`, `InboxCard.vue`,
    # `ProjectDetailView`), l'ouvre DANS son projet : une page n'a pas d'écran à elle.
    # Conséquence : ce patron réclame `project_id`, et un appel qui ne le passe pas ne
    # rend aucun lien (garde de `_render`) — jamais une adresse à trous.
    "doc": "/projects/{project_id}?doc={id}",
    "project": "/projects/{id}",
    # L'espace facturation du tableau de bord (`/org/billing`, cf. le routeur
    # d'oto-dashboard) — c'est là que les factures se téléchargent. Un tenant qui
    # ne déclare pas ce patron n'a pas cette vue : l'e-mail part alors sans bouton,
    # plutôt qu'avec un lien mort.
    "billing": "/org/billing",
    "connectors": "/connectors",
    "connector_return": "/connectors?connector={connector}",
    "import_project": "/import?slug={slug}",
    "marketplace": "/connectors?tab=marketplace",
}


def _tenant_of(sub: Optional[str]):
    """L'entrée de registre du tenant de ce compte, ou None (tenant primaire inclus)."""
    if not sub:
        return None
    try:
        from . import tenancy
        registre = tenancy.current()
        slug = registre.tenant_of(sub)
        if not slug or slug == tenancy.PRIMARY_SLUG:
            return None
        return next((e for e in registre.entries() if e.slug == slug), None)
    except Exception:  # noqa: BLE001 — un lien ne casse jamais un appel
        logger.warning("résolution du tenant impossible pour un lien (fail-open)",
                       exc_info=True)
        return None


def _render(base: str, path: str, params: dict) -> Optional[str]:
    """Assemble base + patron. Un paramètre manquant ANNULE le lien plutôt que de
    produire une adresse à trous (`/network//knowledge/12`), qui mènerait à une page
    d'erreur en se faisant passer pour un lien valide."""
    try:
        rendu = path.format(**{k: v for k, v in params.items() if v is not None})
    except (KeyError, IndexError):
        logger.warning("lien non rendu : le patron %r attend un paramètre absent", path)
        return None
    if "{" in rendu or "//" in rendu.lstrip("https:").lstrip("/"):
        return None
    return f"{base}{rendu}" if rendu else base


def link_for(kind: str, *, sub: Optional[str] = None, **params: Any) -> Optional[str]:
    """Le lien de type `kind` à MONTRER à ce compte, ou **None** s'il n'existe pas
    chez lui.

    `None` n'est pas une erreur : c'est la réponse juste quand le produit de
    l'utilisateur n'a pas cette vue. L'appelant écrit alors sa réponse sans lien.
    """
    entry = _tenant_of(sub)
    if entry is None:
        chemin = DEFAULT_PATHS.get(kind)
        return None if chemin is None else _render(config.dashboard_url(), chemin, params)

    patrons = getattr(entry, "link_paths", None) or {}
    if kind not in patrons:
        return None                       # le tenant n'a pas cette vue : pas de lien
    base = (entry.dashboard_url or "").rstrip("/")
    if not base:
        return None                       # un patron sans adresse ne mène nulle part
    return _render(base, str(patrons[kind]), params)


def redirect_for(kind: str, *, sub: Optional[str] = None, **params: Any) -> str:
    """Le lien de type `kind` vers lequel on REDIRIGE. Toujours une adresse.

    Utilisé au retour d'un consentement OAuth : à ce moment le navigateur DOIT
    atterrir quelque part. Sans patron chez le tenant, on sert le nôtre — voir notre
    marque une fois vaut mieux qu'une page blanche au milieu d'une connexion.
    """
    return (link_for(kind, sub=sub, **params)
            or _render(config.dashboard_url(), DEFAULT_PATHS.get(kind, ""), params)
            or config.dashboard_url())
