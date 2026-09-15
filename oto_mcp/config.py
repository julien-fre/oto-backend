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


# Les deux origines possibles d'une écriture sur la base PARTAGÉE prod/preprod.
PROD, PREPROD = "prod", "preprod"


def origine_du_process() -> Optional[str]:
    """**Quel environnement ce process sert-il ?** `prod`, `preprod`, ou None s'il ne
    peut pas le savoir (dev, tests) — auquel cas on n'invente rien.

    Prod et preprod partagent la MÊME base : sans cette réponse, ce que les deux
    écrivent se mélange et l'on ne peut plus lire une fenêtre « en prod ». Le besoin
    est né le 2026-08-29, en lisant le premier compteur de la fenêtre L7.

    **Dérivé de `OTO_MCP_PUBLIC_URL`, et c'est le choix qui compte.** C'est l'URL que
    ce process annonce de lui-même, elle est `require_env` (donc jamais absente là où
    ça compte) et elle diffère par environnement — `mcp.oto.cx` en prod,
    `mcp.oto.ninja` en preprod. **Surtout, elle ne peut pas se DÉFAUTER** : c'est ce
    qui la sépare de `project_domain()`, dont le défaut est le domaine de PRODUCTION
    et qui n'est posé qu'en preprod. Un environnement qui oublierait la variable y
    serait classé « prod » en silence ; ici il est classé « inconnu », et un inconnu
    se voit.

    Aucun nouveau réglage à poser à la main : on lit ce que le process sait déjà."""
    url = os.environ.get("OTO_MCP_PUBLIC_URL")
    if not url:
        return None
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return None
    return PROD if host == f"mcp.{_PROD_PROJECT_DOMAIN}" else PREPROD


class EnvironnementAmbigu(RuntimeError):
    """Ce process ne sait pas s'il est la production, alors qu'il doit le savoir."""


def est_la_production() -> bool:
    """**Ce process est-il LA production ?** Oui ou non, jamais « probablement » : lève
    `EnvironnementAmbigu` plutôt que de deviner.

    Une question en dépend, et elle engage de l'argent : qui draine vers un TIERS
    (prélèvement, email) le travail en attente dans la base PARTAGÉE
    (`boucles_de_fond`). Deux erreurs possibles, deux coûts. Une préprod prise pour la
    prod prélève et écrit à des clients avec son code et ses clés. Une prod prise pour
    la préprod cesse de prélever sans que rien n'échoue. Aucune des deux ne se rattrape
    par une valeur par défaut.

    Deux déclarations déjà posées, qui doivent CONCORDER :
    - `origine_du_process()`, dérivée de `OTO_MCP_PUBLIC_URL` : prod SEULEMENT si l'hôte
      est exactement `mcp.oto.cx` ;
    - `OTO_SENTRY_ENV` quand il est posé (`production` en prod, `canari` en préprod,
      relevés le 10/09/2026). Absent, il ne vote pas : son défaut côté Sentry
      (`production`) n'est pas une déclaration.

    Une URL sans hôte, ou deux déclarations qui se contredisent, lèvent. Le cas visé :
    un renommage de domaine qui changerait l'une sans l'autre. La prod refuse alors de
    démarrer (le bleu/vert garde l'ancienne couleur) au lieu de cesser de prélever en
    silence.
    """
    origine = origine_du_process()
    if origine is None:
        raise EnvironnementAmbigu(
            "impossible de dire si ce process est la production : OTO_MCP_PUBLIC_URL "
            f"est absente ou sans hôte ({os.environ.get('OTO_MCP_PUBLIC_URL')!r}). "
            "Pose-la (https://mcp.oto.cx en production), ou éteins les boucles qui "
            "agissent sur un tiers (OTO_SCHEDULER_ENABLED=0, OTO_BILLING_RUNNER_ENABLED=0).")
    sentry = os.environ.get("OTO_SENTRY_ENV", "").strip().lower()
    if sentry and (sentry == "production") != (origine == PROD):
        raise EnvironnementAmbigu(
            f"OTO_MCP_PUBLIC_URL désigne {origine!r} mais OTO_SENTRY_ENV vaut {sentry!r} : "
            "les deux déclarations de l'environnement se contredisent. Corrige celle qui "
            "ment, ce process ne choisit pas entre elles.")
    return origine == PROD


class ReplIdentiteDevDangereux(RuntimeError):
    """`OTO_MCP_DEV_SUB` est posée EN MÊME TEMPS qu'un émetteur Logto réel."""


def verifier_repli_identite_dev() -> None:
    """**Refuse de démarrer** si le repli d'identité dev (`OTO_MCP_DEV_SUB`,
    `auth/hooks.py`) est posé EN MÊME TEMPS qu'un émetteur Logto réel
    (`LOGTO_ENDPOINT`).

    `OTO_MCP_DEV_SUB` accorde une identité SANS jeton — documenté, opt-in, sans
    effet tant que la variable n'est pas posée. Mais rien ne l'empêchait
    MÉCANIQUEMENT de coexister avec un environnement servi sous un vrai émetteur :
    un `.env` de dev copié en prod, ou l'inverse, aurait glissé un repli
    d'authentification à côté d'un Logto qui authentifie pour de vrai, sans qu'aucun
    des deux ne le sache (revue de sécurité du 2026-08-29, oto-backend#572, constat
    basse sévérité n°1).

    Chaque variable posée SEULE reste exactement ce qu'elle a toujours été — ce n'est
    que leur croisement qui est refusé."""
    if os.environ.get("OTO_MCP_DEV_SUB") and os.environ.get("LOGTO_ENDPOINT"):
        raise ReplIdentiteDevDangereux(
            "OTO_MCP_DEV_SUB est posée alors que LOGTO_ENDPOINT l'est aussi : le "
            "repli d'identité de dev local (auth/hooks.py) ne doit jamais coexister "
            "avec un émetteur Logto réel. Retire OTO_MCP_DEV_SUB pour démarrer contre "
            "ce Logto. (LOGTO_ENDPOINT reste obligatoire pour démarrer — le stdio "
            "local sans auth a été retiré le 2026-06-13 — donc le retirer NE lève "
            "pas cette garde, `main()` échouera plus loin sur `require_env`.)"
        )


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
    # noqa: SILENT — dette déclarée : on sert NOTRE marque au tenant partenaire (#424, verdict C)
    except Exception:  # noqa: BLE001
        return dashboard_url()


def tenant_slug_for(sub: Optional[str]) -> Optional[str]:
    """Le tenant qui HÉBERGE les orgs créées par ce compte : son slug, ou `None`
    pour le tenant primaire (le défaut).

    Alimente `orgs.tenant_id` à la création (`org_store.create_org`). Même source
    que `front_for` juste dessous — le registre d'émetteurs, donc le préfixe du sub,
    donc l'émetteur du jeton : **dérivé, jamais déclaré**, un appelant ne peut pas
    revendiquer un tenant auquel il n'appartient pas.

    ⚠️ **La paire se lit ensemble, et elle diffère d'une condition.** `front_for`
    n'écrit la marque que si le tenant déclare un `dashboard_url` (sans adresse, pas
    de lien sortant à poser — cf. son inertie volontaire). Le RATTACHEMENT, lui, n'a
    besoin d'aucune adresse : une org d'un tenant sans dashboard doit quand même
    déclarer son tenant. D'où deux fonctions et non un tuple élargi : la condition de
    l'une serait un angle mort de l'autre, et c'est précisément l'angle mort qui a
    laissé 65 orgs sur le tenant primaire.

    Ne lève pas, pour la même raison que `front_for` : on est sur le chemin de
    création d'une org, un registre illisible doit dégrader vers le tenant primaire,
    pas refuser la création.
    ⚠️ **Et cette dégradation est MUETTE depuis le 03/09/2026** : le contrôle qui la
    rapportait a été retiré. Elle est sans conséquence pour autant, et c'est la raison
    du retrait — le rattachement déclaré ne DÉCIDE rien. `db.org_tenant_slug` le double
    par deux dérivations (marque du front, sub qualifié d'un membre) qui, elles, se
    lisent du jeton à chaque appel : une org née avec le mauvais rattachement rend
    quand même le bon verdict. Mesuré le 03/09 : 166 orgs sur 166 identiques avec et
    sans lui.
    """
    if not sub:
        return None
    try:
        from . import tenancy
        slug = tenancy.current().tenant_of(sub)
        return None if (not slug or slug == tenancy.PRIMARY_SLUG) else slug
    # noqa: SILENT — registre illisible : on dégrade vers le tenant primaire au lieu de refuser une création d'org ; l'écart n'est plus rapporté (contrôle retiré le 03/09/2026) et n'a aucune conséquence, les deux dérivations rendent le bon verdict sans lui
    except Exception:  # noqa: BLE001
        return None


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
    # noqa: SILENT — dette déclarée : on sert NOTRE front au tenant partenaire (#424, verdict C)
    except Exception:  # noqa: BLE001
        return (None, None)
