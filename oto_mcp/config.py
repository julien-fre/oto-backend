"""Env-var helper. Keep secrets out of the repo."""
import os


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
