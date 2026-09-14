"""`oto-mcp maintenance oauth-relay-callbacks [--apply]` — la mise en service d'un host relayé.

⚠️ **Le host doit DÉJÀ figurer dans `OTO_MCP_OAUTH_RELAY_HOSTS`** du `.env` que la commande
charge — c'est la seule liste qu'elle parcourt —, et le service ne pas encore avoir
redémarré : il n'annoncera le relais qu'au redémarrage, rappel posé.

Pose `<as_base>/oauth/callback` sur l'application partagée de l'annuaire de chaque host
déclaré, puis RELIT l'application pour le constater (Logto remplace la liste entière à
chaque écriture, sans contrôle de concurrence : une écriture promise ne vaut pas une écriture
constatée). À blanc par défaut (constate seulement), `--apply` écrit. Chaque host est tenté
et rendu, même si un autre échoue. Un host dont la plateforme n'administre pas l'annuaire est
`refusé` : le relais ne compare le rappel d'un client qu'aux rappels relus sur l'application,
ce qui exige de pouvoir la lire.
"""
from __future__ import annotations

import logging
import os

from . import facade, relay

_log = logging.getLogger("oto_mcp.oauth_facade")


def poser_les_rappels(*, dry_run: bool = True) -> dict:
    from .. import db, server, tenancy
    declares = sorted(relay.hosts_declares())
    if not declares:
        return {"(aucun)": f"{relay.HOSTS_ENV} est vide dans cet environnement : rien à poser"}
    # Une base illisible ferait passer chaque tenant pour « inconnu » (le registre du boot
    # l'avale) : ici elle doit ÉCHOUER, pas rendre un diagnostic faux.
    db.list_tenant_issuers()
    registre, _ = server._registry_and_issuers()
    tenancy.install(registre)
    public_url = os.environ.get("OTO_MCP_PUBLIC_URL", "")
    app_plateforme = os.environ.get("OTO_MCP_CLAUDE_APP_ID", "")
    rendu = {}
    for host in declares:
        c = relay.cible_pour_host(host, public_url, app_plateforme)
        if c.host != host:
            rendu[host] = "inconnu : ni le domaine de la plateforme, ni celui d'un tenant"
            continue
        if not c.app_id or c.directory is None:
            rendu[host] = ("refusé : annuaire non administré par la plateforme "
                           "(tenants.logto_mgmt ou credential absent) — relais impossible")
            continue
        try:
            if not dry_run:
                facade._register_redirects(c.app_id, [c.rappel], c.directory, cors_uris=[])
            present = c.rappel in facade._redirect_uris(c.app_id, c.directory)
        except Exception as exc:
            _log.warning("oauth.relay maintenance host=%s échec", host, exc_info=True)
            rendu[host] = f"échec : {type(exc).__name__}"
            continue
        if dry_run:
            rendu[host] = "présent" if present else f"absent : {c.rappel} (rejouer avec --apply)"
        else:
            rendu[host] = "posé" if present else "NON CONSTATÉ après écriture"
    return rendu
