"""Env-var helper. Keep secrets out of the repo."""
import os
from typing import Optional


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing env var '{name}'. Set it in the process environment "
            f"(systemd EnvironmentFile in prod, .env in dev)."
        )
    return val


def project_domain() -> str:
    """Domaine racine des endpoints de PROJET publiés — `<slug>.mcp.<D>` (annuaire, mode
    anonymous) et `<slug>.share.<D>` (partage navigable, mode secret). **PROD = `oto.cx`,
    PREPROD = `oto.ninja`** (cutover ADR 0040) : sans ça le routing par Host et les URLs
    dérivées restaient figés sur la prod (`.oto.cx`), rendant les endpoints de projet
    injoignables en preprod. Env `OTO_PROJECT_DOMAIN` (défaut `oto.cx`)."""
    return os.environ.get("OTO_PROJECT_DOMAIN", _PROD_PROJECT_DOMAIN).strip().lower().lstrip(".")


# Domaine de projet de PRODUCTION : le seul dont les sous-domaines obtiennent un vrai
# certificat (Caddy ACME on-demand sur `*.mcp.oto.cx` / `*.share.oto.cx`). Hors prod,
# Caddy sert sa CA interne — pratique pour tester, rejeté par tout client MCP réel.
_PROD_PROJECT_DOMAIN = "oto.cx"


def project_domain_is_production() -> bool:
    """L'URL d'un projet publié ici est-elle distribuable à un tiers ? Faux en preprod :
    le certificat de `*.share.<D>` y est interne, donc un lien envoyé à un client sera
    refusé par son client MCP (feedback #308 — découvert en livrant un vrai client)."""
    return project_domain() == _PROD_PROJECT_DOMAIN


def mcp_audience_alts() -> frozenset[str]:
    """Audiences MCP canoniques SECONDAIRES (coexistence multi-domaine, ex.
    `https://mcp.oto.cx/mcp` en plus de `MCP_AUDIENCE`=`https://mcp.oto.ninja/mcp`).

    Env `MCP_AUDIENCE_ALT` = liste séparée par des virgules (resource indicators
    complets, sans slash final). Vide/absent = frozenset vide → **no-op** (le
    comportement mono-audience de mcp.oto.ninja est byte-à-byte inchangé)."""
    raw = os.environ.get("MCP_AUDIENCE_ALT", "")
    return frozenset(a.strip() for a in raw.split(",") if a.strip())


def mcp_audience_alt_hosts() -> frozenset[str]:
    """Les HOSTS des audiences alt — pour le PRM Host-aware (un client qui tape
    `mcp.oto.cx` doit se voir annoncer `resource=https://mcp.oto.cx/mcp`)."""
    from urllib.parse import urlparse
    hosts = (urlparse(a).hostname for a in mcp_audience_alts())
    return frozenset(h for h in hosts if h)


def dashboard_url() -> str:
    """L'adresse du tableau de bord servie AUX UTILISATEURS (liens de tableaux, de
    connexion, pages publiques).

    Source unique. TROIS variables ont longtemps coexisté pour la même chose —
    `OTO_APP_URL`, `OTO_DASHBOARD_URL`, `OTO_DASHBOARD_BASE_URL` — et la production ne
    posait que la première :
    tout ce qui lisait la seconde retombait sur un défaut EN DUR pointant la
    **preprod**. Un client d'un partenaire s'est ainsi vu servir un lien vers un
    environnement qui n'est pas le sien (13/08). Le défaut, lui, vise désormais la
    PROD : un environnement mal configuré doit dégrader vers le vrai produit, jamais
    vers un bac à sable.
    """
    for var in ("OTO_APP_URL", "OTO_DASHBOARD_URL", "OTO_DASHBOARD_BASE_URL"):
        valeur = os.environ.get(var, "").strip().rstrip("/")
        if valeur:
            return valeur
    return "https://manage.oto.cx"


def dashboard_url_for(sub: Optional[str]) -> str:
    """L'adresse du tableau de bord servie À CE COMPTE.

    Un compte de tenant tiers reçoit celle de SON produit : lui envoyer la nôtre
    revient à lui proposer un service qu'il n'a pas — constaté chez un client le
    13/08, qui s'est vu offrir un lien vers notre tableau de bord au milieu d'une
    conversation avec l'assistant de son fournisseur.

    Repli sur la nôtre à chaque détente (pas de compte, tenant primaire, pas
    d'adresse déclarée, registre illisible) : ce chemin sert des liens dans des
    réponses d'outils, il ne doit jamais lever.
    """
    if not sub:
        return dashboard_url()
    try:
        from . import tenancy
        registre = tenancy.current()
        slug = registre.tenant_of(sub)
        if not slug or slug == tenancy.PRIMARY_SLUG:
            return dashboard_url()
        propre = next((e.dashboard_url for e in registre.entries()
                       if e.slug == slug and e.dashboard_url), "")
        return propre or dashboard_url()
    except Exception:  # noqa: BLE001
        return dashboard_url()


def front_for(sub: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Le front qui héberge les orgs créées par CE COMPTE : `(base_url, brand)`.

    Alimente `orgs.front_base_url` / `front_brand` à la création (cf.
    `capabilities/orgs.py::_create_org`). `(None, None)` = oto, le défaut — et le
    seul résultat possible pour un compte de la plateforme.

    **Dérivé, jamais déclaré**, comme la lecture qu'il alimente (b6e1d27) : le
    tenant vient de l'émetteur du jeton, donc un appelant ne peut pas revendiquer
    un front auquel il n'appartient pas. Rien à retirer plus tard du contrat des
    intégrateurs.

    Pourquoi `dashboard_url` comme source : c'est déjà l'adresse du produit du
    tenant, celle que `dashboard_url_for` sert à ses comptes. Une invitation ne
    passe pas par `links` (son chemin `/invitation/<code>` est le même partout),
    donc l'adresse suffit ici là où un lien de tableau exigerait un patron.

    Inertie volontaire, même règle que `dashboard_url_for` : un tenant déclaré
    SANS `dashboard_url` ne pose rien, et l'org retombe sur oto — le comportement
    d'avant. Jamais une adresse devinée.

    Ne lève pas : on est sur le chemin de création d'une org, un registre illisible
    doit dégrader vers oto, pas refuser la création.
    """
    if not sub:
        return (None, None)
    try:
        from . import tenancy
        registre = tenancy.current()
        slug = registre.tenant_of(sub)
        if not slug or slug == tenancy.PRIMARY_SLUG:
            return (None, None)
        base = next((e.dashboard_url for e in registre.entries()
                     if e.slug == slug and e.dashboard_url), "")
        return (base, slug) if base else (None, None)
    except Exception:  # noqa: BLE001
        return (None, None)
