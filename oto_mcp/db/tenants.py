"""Palier tenant (ADR 0052) — lecture de l'étage d'identité.

Source du registre d'émetteurs (`tenancy.build`), et **suivi** des tenants pour la
console plateforme. Toujours en LECTURE SEULE : déclarer un tenant reste un runbook
(une instance Logto par tenant, barreau B4) — un écran qui poserait un émetteur
donnerait l'illusion qu'il suffit d'une ligne en base, alors que le registre est
construit AU BOOT et que l'annuaire n'existe pas encore côté partenaire.

⚠️ **Un tenant se compte par DEUX sources qui peuvent diverger**, et c'est
précisément ce que le suivi doit rendre visible :

- `orgs.tenant_id` — le rattachement DÉCLARÉ d'une organisation (posé par L1 sur
  l'existant, par le provisioning ensuite) ;
- le **préfixe du sub** — la qualification par tenant (`tenancy.qualify`), qui suit
  l'émetteur du jeton et rien d'autre.

Rien ne les tient ensemble : un compte peut être qualifié `tulina:…` pendant que
son organisation reste sur le tenant `oto` (c'est l'état laissé par une bascule
L3bis partielle). Les compteurs les gardent donc SÉPARÉS et nomment l'écart
(`orgs_desalignees`) plutôt que d'en dériver un chiffre unique qui mentirait.
"""
from __future__ import annotations

from .. import tenancy
from ._conn import _connect

# Bornes des listes servies par la fiche d'un tenant : une fiche rend son INDEX,
# pas la population (cf. §« Ce qu'un outil RENVOIE a un budget »).
_TENANT_LIST_CAP = 50


def list_tenant_issuers() -> list:
    """Tenants qui déclarent un émetteur, ordre stable.

    Le tenant `oto` n'y figure **pas** : son émetteur est l'env (`LOGTO_ENDPOINT`),
    donc DB-indépendant — l'authentification canonique ne doit jamais dépendre
    d'une lecture de table. Une ligne qui le redéclarerait est de toute façon
    ignorée par le registre (l'env gagne).
    """
    with _connect() as conn:
        rows = conn.execute(
            # `name` et `hosts` servent la DÉCOUVERTE (lot L3 : PRM et 401 sensibles
            # au host), jamais la vérification d'un jeton — celle-ci ne connaît que
            # l'émetteur. Les lire ici ne change donc rien au chemin d'auth.
            "SELECT slug, name, issuer, jwks_uri, hosts, oauth_client_id, "
            "dashboard_url, link_paths, tool_prefix FROM tenants "
            "WHERE issuer IS NOT NULL AND btrim(issuer) <> '' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Suivi (console plateforme) ───────────────────────────────────────────────

# Le tenant d'un sub, EN SQL — miroir de `tenancy.IssuerRegistry.tenant_of` : le
# préfixe déclaré le plus long qui matche, sinon le tenant primaire (sub nu).
#
# ⚠️ Deux différences assumées avec le registre du process, toutes deux dans le sens
# du suivi : ici on classe sur les tenants de la BASE (le registre, lui, ignore une
# ligne sans émetteur), et le tenant primaire est la LIGNE `oto`, pas l'env. Un
# compte qualifié sous un tenant dont l'émetteur n'est pas encore déclaré est donc
# COMPTÉ ici alors que ses jetons sont rejetés — c'est exactement l'état qu'un suivi
# doit montrer, pas masquer.
_SUB_TENANT_SQL = """
    SELECT u.sub, u.created_at,
           COALESCE((SELECT p.id FROM pref p
                      WHERE u.sub LIKE p.p || '%%'
                      ORDER BY length(p.p) DESC LIMIT 1),
                    (SELECT id FROM tenants WHERE slug = %(primary)s)) AS tenant_id
      FROM users u
"""

# `slug` ne contient ni `%` ni `_` (`tenancy._SLUG_RE`), donc le `LIKE` ci-dessus
# compare bien un préfixe littéral — pas un motif que le slug pourrait ouvrir.
_TENANT_PREF_SQL = "SELECT id, slug || ':' AS p FROM tenants WHERE slug <> %(primary)s"


def _tenant_counts_sql(where_tenant: str = "") -> str:
    """Les compteurs d'un tenant, une passe. `where_tenant` borne à un tenant."""
    return f"""
    WITH pref AS ({_TENANT_PREF_SQL}),
         sub_tenant AS ({_SUB_TENANT_SQL}),
         org_counts AS (
             SELECT tenant_id,
                    COUNT(*) FILTER (WHERE archived_at IS NULL) AS orgs,
                    COUNT(*) FILTER (WHERE archived_at IS NOT NULL) AS orgs_archivees
               FROM orgs GROUP BY tenant_id
         ),
         acct_counts AS (
             SELECT tenant_id, COUNT(*) AS comptes, MAX(created_at) AS dernier_compte_at
               FROM sub_tenant GROUP BY tenant_id
         ),
         call_counts AS (
             -- kind='mcp' : le trafic d'OUTILS, iso avec le reste du monitoring
             -- (`rest`/`protocol`/`connector` mesurent autre chose).
             SELECT st.tenant_id,
                    COUNT(*) AS appels,
                    COUNT(DISTINCT c.sub) AS comptes_actifs,
                    MAX(c.created_at) AS last_seen_at
               FROM tool_calls c JOIN sub_tenant st ON st.sub = c.sub
              WHERE c.kind = 'mcp'
                AND c.created_at >= NOW() - make_interval(days => %(days)s)
              GROUP BY st.tenant_id
         ),
         drift AS (
             -- L'écart entre les deux sources : une org rattachée à ce tenant dont
             -- le CRÉATEUR est qualifié sous un autre. `created_by` est le seul sub
             -- que porte la table `orgs` — l'appartenance vit dans `org_members`,
             -- donc c'est une SONDE, pas un recensement (elle ne voit pas une org
             -- dont seuls les membres ont basculé).
             SELECT o.tenant_id, COUNT(*) AS orgs_desalignees
               FROM orgs o JOIN sub_tenant st ON st.sub = o.created_by
              WHERE o.archived_at IS NULL AND o.tenant_id <> st.tenant_id
              GROUP BY o.tenant_id
         )
    SELECT t.id, t.slug, t.name, t.issuer, t.jwks_uri, t.hosts, t.oauth_client_id,
           t.dashboard_url, t.link_paths, t.tool_prefix, t.created_at,
           COALESCE(oc.orgs, 0) AS orgs,
           COALESCE(oc.orgs_archivees, 0) AS orgs_archivees,
           COALESCE(ac.comptes, 0) AS comptes,
           ac.dernier_compte_at,
           COALESCE(cc.appels, 0) AS appels,
           COALESCE(cc.comptes_actifs, 0) AS comptes_actifs,
           cc.last_seen_at,
           COALESCE(dr.orgs_desalignees, 0) AS orgs_desalignees
      FROM tenants t
      LEFT JOIN org_counts oc ON oc.tenant_id = t.id
      LEFT JOIN acct_counts ac ON ac.tenant_id = t.id
      LEFT JOIN call_counts cc ON cc.tenant_id = t.id
      LEFT JOIN drift dr ON dr.tenant_id = t.id
     {where_tenant}
     ORDER BY t.id
    """


def _shape_tenant(row: dict) -> dict:
    """Forme servie : les compteurs en entiers, et l'ÉTAT D'ÉMETTEUR dérivé ici —
    une seule dérivation, partagée par la liste et la fiche."""
    out = dict(row)
    issuer = (out.get("issuer") or "").strip()
    primaire = out.get("slug") == tenancy.PRIMARY_SLUG
    # Ce que le tenant peut faire, tel que le registre le verra au prochain boot :
    # le primaire tient son émetteur de l'ENV (une ligne le redéclarant est ignorée),
    # les autres n'authentifient que si leur ligne porte un émetteur.
    out["issuer_source"] = "env" if primaire else ("db" if issuer else None)
    out["authenticates"] = bool(primaire or issuer)
    out["primary"] = primaire
    out["hosts"] = list(out.get("hosts") or [])
    out["link_paths"] = dict(out.get("link_paths") or {})
    # DÉCLARÉ seulement : ce que le process applique vraiment se lit sur le registre
    # (`tenants_admin._decorate` → `tool_prefix_effectif`). Un préfixe posé en base
    # après le dernier boot, ou refusé par `tool_alias.normalize_prefix`, s'affiche
    # ici sans être servi — et c'est justement l'écart qu'un suivi doit montrer.
    out["tool_prefix"] = (out.get("tool_prefix") or None)
    for k in ("orgs", "orgs_archivees", "comptes", "comptes_actifs", "appels",
              "orgs_desalignees"):
        out[k] = int(out.get(k) or 0)
    return out


def list_tenants_overview(*, days: int = 30) -> list[dict]:
    """Tous les tenants + leur empreinte sur une fenêtre (défaut 30 j).

    Une ligne par tenant DÉCLARÉ, y compris ceux à zéro compte : un tenant provisionné
    dont personne ne s'est encore connecté est ce qu'on veut le plus voir.
    """
    with _connect() as conn:
        rows = conn.execute(_tenant_counts_sql(),
                            {"primary": tenancy.PRIMARY_SLUG, "days": int(days)}).fetchall()
    return [_shape_tenant(r) for r in rows]


def get_tenant_overview(slug: str, *, days: int = 30) -> dict | None:
    """La fiche d'un tenant : ses compteurs + les listes qui les expliquent.

    `None` si le slug n'existe pas — l'appelant en fait un 404, jamais une fiche vide
    (qui se lirait comme « ce tenant existe et n'a rien »).
    """
    params = {"primary": tenancy.PRIMARY_SLUG, "days": int(days), "slug": slug}
    with _connect() as conn:
        row = conn.execute(_tenant_counts_sql("WHERE t.slug = %(slug)s"), params).fetchone()
        if row is None:
            return None
        fiche = _shape_tenant(row)
        params["tid"] = fiche["id"]
        params["cap"] = _TENANT_LIST_CAP

        fiche["orgs_recentes"] = [dict(r) for r in conn.execute(
            """
            SELECT o.id, o.name, o.created_at, o.archived_at, o.personal_of IS NOT NULL
                   AS personal, o.front_base_url, o.front_brand,
                   (SELECT COUNT(*) FROM org_members m WHERE m.org_id = o.id) AS membres
              FROM orgs o WHERE o.tenant_id = %(tid)s
             ORDER BY o.archived_at IS NOT NULL, o.created_at DESC LIMIT %(cap)s
            """, params).fetchall()]

        # Les comptes du tenant, les plus actifs d'abord (un suivi sert à voir QUI
        # porte l'usage) ; l'inactif remonte quand même, en fin de liste, à 0 appel.
        fiche["comptes_recents"] = [dict(r) for r in conn.execute(
            f"""
            WITH pref AS ({_TENANT_PREF_SQL}), sub_tenant AS ({_SUB_TENANT_SQL})
            SELECT u.sub, u.email, u.name, u.role, u.created_at,
                   COALESCE(c.appels, 0) AS appels, c.last_seen_at
              FROM sub_tenant st
              JOIN users u ON u.sub = st.sub
              LEFT JOIN (
                    SELECT sub, COUNT(*) AS appels, MAX(created_at) AS last_seen_at
                      FROM tool_calls
                     WHERE kind = 'mcp'
                       AND created_at >= NOW() - make_interval(days => %(days)s)
                     GROUP BY sub
              ) c ON c.sub = u.sub
             WHERE st.tenant_id = %(tid)s
             ORDER BY COALESCE(c.appels, 0) DESC, u.created_at DESC LIMIT %(cap)s
            """, params).fetchall()]

        # Le détail de l'écart compté par `orgs_desalignees` : sans lui le chiffre
        # est une alarme sans adresse.
        fiche["orgs_desalignees_detail"] = [dict(r) for r in conn.execute(
            f"""
            WITH pref AS ({_TENANT_PREF_SQL}), sub_tenant AS ({_SUB_TENANT_SQL})
            SELECT o.id, o.name, o.created_by, t2.slug AS tenant_du_createur
              FROM orgs o
              JOIN sub_tenant st ON st.sub = o.created_by
              JOIN tenants t2 ON t2.id = st.tenant_id
             WHERE o.archived_at IS NULL AND o.tenant_id = %(tid)s
               AND o.tenant_id <> st.tenant_id
             ORDER BY o.id LIMIT %(cap)s
            """, params).fetchall()]
    return fiche
