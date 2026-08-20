"""Documents légaux — SOURCE DE VÉRITÉ (version/label/url), miroir de
`oto-websites/web/src/legal`.

Le contenu des docs vit sur oto.cx (routes `/terms`, `/cgv`, `/dpa`) ; ici on ne
tient que les MÉTADONNÉES (slug → version courante + libellé + URL) et la carte des
CONTEXTES (quels docs sont requis pour « accéder » vs « acheter »). Le backend
`me.legal` en dérive le reste-à-accepter ; la table `legal_acceptances` ne trace que
le consentement.

⚠️ Tenir aligné avec `web/src/legal` : à chaque bump de `current` d'un doc côté site,
bumper `version` ici — sinon un doc modifié ne redemande pas l'acceptation (ou en
redemande une périmée). Versions au 2026-07-09 : terms 3.0, cgv 2.0, dpa 2.0.

**Un tenant tiers (Tulina…) a ses PROPRES documents, pas les nôtres** — même besoin
que `orgs.front_base_url`/`front_brand` (invitations) ou `guides` scope `tenant`
(socle d'instructions) : une donnée servie à l'utilisateur qui ne peut pas rester
celle de la plateforme primaire. `docs_for` en est le seam : un override par
(tenant, slug) vit dans `tenant_legal_docs` (table, PAS le registre `tenancy.py` —
lu en LIVE, sans redémarrage) ; absent, le slug garde son défaut `CURRENT_DOCS`
tel quel. Un tenant sans override — le cas de Tulina aujourd'hui — voit donc
exactement les documents d'oto, jusqu'à ce qu'une ligne soit posée pour lui.
"""
from __future__ import annotations

from . import db, tenancy

# slug → métadonnées de la VERSION COURANTE (miroir de web/src/legal `current`).
# Défaut plateforme — s'applique à tout tenant sans override déclaré.
CURRENT_DOCS: dict[str, dict[str, str]] = {
    "terms": {"version": "3.0", "label": "CGU", "url": "https://oto.cx/terms"},
    "cgv":   {"version": "2.0", "label": "CGV", "url": "https://oto.cx/cgv"},
    "dpa":   {"version": "2.0", "label": "DPA", "url": "https://oto.cx/dpa"},
}

# Contexte → docs requis. `access` = à l'inscription (CGU) ; `purchase` = à l'achat.
# Un override de tenant ne peut pas AJOUTER de slug à un contexte — seulement
# remplacer version/label/url d'un slug qui y figure déjà.
CONTEXTS: dict[str, list[str]] = {
    "access": ["terms"],
    "purchase": ["terms", "cgv", "dpa"],
}


def docs_for(tenant_slug: str) -> dict[str, dict[str, str]]:
    """`CURRENT_DOCS`, avec les overrides déclarés par `tenant_slug` fusionnés
    par-dessus, slug par slug. Chaque appel relit la table — c'est ce qui rend un
    override effectif sans redéploiement ni redémarrage du process.

    Le tenant PRIMAIRE (oto) court-circuite la lecture : `CURRENT_DOCS` EST son
    défaut, il n'y a jamais de ligne à chercher pour lui — et ça évite un aller PG
    par défaut sur le chemin le plus emprunté (le seul aujourd'hui, tant qu'aucun
    tenant n'a d'override)."""
    if not tenant_slug or tenant_slug == tenancy.PRIMARY_SLUG:
        return CURRENT_DOCS
    overrides = db.get_tenant_legal_docs(tenant_slug)
    if not overrides:
        return CURRENT_DOCS
    return {slug: {**meta, **overrides.get(slug, {})} for slug, meta in CURRENT_DOCS.items()}
