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

Rien ne les tient ensemble : un compte peut être qualifié `<tenant>:…` pendant que
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
            "dashboard_url, link_paths, tool_prefix, brand FROM tenants "
            "WHERE issuer IS NOT NULL AND btrim(issuer) <> '' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def tenant_exists(slug: str) -> bool:
    """Le slug désigne-t-il une ligne `tenants` ? Garde de pose d'une clé de tenant
    (L-clés PR 1) : un slug inconnu ne doit pas fabriquer une ligne de coffre que
    personne ne lira. Lecture par PK, sans compteur — la fiche de suivi coûte trop
    pour une garde."""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM tenants WHERE slug = %s", (slug,)).fetchone() is not None


# ── Le rôle « admin de tenant » (L-clés PR 2) ────────────────────────────────

def is_tenant_admin(slug: str, sub: str) -> bool:
    """Lu à l'appel par `_authz.TENANT_ADMIN_OF` — UNE lecture par PK."""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM tenant_admins WHERE slug = %s AND sub = %s",
                            (slug, sub)).fetchone() is not None


def list_tenant_admins(slug: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT sub, granted_by, granted_at FROM tenant_admins WHERE slug = %s "
            "ORDER BY granted_at, sub", (slug,)).fetchall()
    return [dict(r) for r in rows]


def add_tenant_admin(slug: str, sub: str, granted_by: "str | None" = None) -> None:
    """Idempotent : re-déclarer un admin ne change rien (ni l'auteur, ni la date)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tenant_admins (slug, sub, granted_by) VALUES (%s, %s, %s) "
            "ON CONFLICT (slug, sub) DO NOTHING", (slug, sub, granted_by))


def remove_tenant_admin(slug: str, sub: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM tenant_admins WHERE slug = %s AND sub = %s",
                           (slug, sub))
        return (cur.rowcount or 0) > 0


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
           t.dashboard_url, t.link_paths, t.tool_prefix, t.brand, t.created_at,
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


# Le tenant EFFECTIF d'une org, en UNE requête — l'UNION des trois axes qui peuvent
# la rattacher à un tenant tiers, dans l'ordre du plus déclaré au plus dérivé. Union
# et non « le meilleur axe » : chacun a un angle mort connu, et le coût d'un faux
# négatif (traiter l'org d'un partenaire comme la nôtre) est ce qu'on refuse.
#
#   1. `orgs.tenant_id` — le rattachement DÉCLARÉ (ADR 0052 L1). ⚠️ **Il a été inerte
#      jusqu'au 2026-09-03, il ne l'est plus.** Mesuré vide le 2026-09-02 (160 orgs
#      sur 160 portant le tenant primaire, dont les 61 qui vivaient chez un
#      partenaire) ; ALIMENTÉ le 2026-09-03 par `scripts/migrate_org_tenant --apply`
#      — 65 orgs repointées, `orgs_desalignees` de 48 à 0 — et posé à la naissance
#      par `org_store.create_org` depuis le même jour. Cet axe porte, désormais.
#      ⚠️ Ce qui ne fait PAS de lui un axe suffisant seul, et c'est pourquoi les deux
#      autres restent : il est ÉCRIT par quelqu'un, quand les deux suivants se
#      DÉRIVENT de l'émetteur du jeton à chaque lecture. Un écrivain peut cesser
#      d'écrire sans bruit — c'est exactement ce qui a produit le trou d'origine.
#   2. `orgs.front_brand` — le front qui HÉBERGE l'org, dérivé de l'émetteur du jeton
#      de son créateur à l'INSERT (`config.front_for`, écrivain unique dans
#      `org_store.create_org`). Non déclarable par l'appelant, donc non revendicable.
#      C'est l'axe qui porte. ⚠️ Son angle mort est historique : avant que la
#      dérivation soit confiée à l'écrivain unique, deux des trois créateurs d'org
#      repartaient à NULL — des orgs de partenaire ont donc pu naître sans marque.
#   3. Le PRÉFIXE du sub d'un membre (`tenancy.qualify` : `<slug>:<sub>`), qui suit
#      l'émetteur du jeton et rien d'autre. C'est lui qui couvre l'angle mort de (2).
#      ⚠️ Son propre angle mort : une org de partenaire sans aucun membre qualifié
#      (invitée depuis notre front) — que (2) couvre. Les deux se tiennent.
#
# Croisement mesuré le 2026-09-02 sur les 160 orgs : (2) et (3) rendent le MÊME
# ensemble de 61 orgs, zéro désaccord dans les deux sens. Deux dérivations
# indépendantes qui concordent, c'est ce qui permet d'affirmer l'absence de faux
# négatif aujourd'hui ; l'union est ce qui la maintient demain.
#
# L'EXPRESSION est exportée à part de la requête : tout dispositif qui trie une
# POPULATION d'orgs (et pas une seule) doit trancher avec exactement les mêmes trois
# axes — sinon deux définitions du « chez le partenaire » divergent, et la seconde
# sera la moins prudente. Aujourd'hui : `db/outreach.py` (l'audience d'une relance).
# `o` est l'alias attendu pour `orgs`, `%(primary)s` le slug du tenant primaire.
# Les trois axes, NOMMÉS un par un — parce que deux expressions différentes en ont
# besoin, et qu'elles ne prennent pas les mêmes. Les recopier serait la voie normale
# vers deux définitions du « chez le partenaire » qui divergent en silence.
_AXE_DECLARE = """(SELECT t.slug FROM tenants t
        WHERE t.id = o.tenant_id AND t.slug <> %(primary)s)"""
_AXE_MARQUE = """NULLIF(btrim(COALESCE(o.front_brand, '')), '')"""
_AXE_MEMBRE = """(SELECT p.slug
         FROM org_members om
         JOIN (SELECT slug, slug || ':' AS pfx FROM tenants
                WHERE slug <> %(primary)s) p ON om.sub LIKE p.pfx || '%%'
        WHERE om.org_id = o.id
        ORDER BY length(p.pfx) DESC LIMIT 1)"""

# La DÉRIVATION seule : les deux axes structurels (2) et (3), **sans le rattachement
# déclaré**. C'est ce qui la rend capable de juger la colonne — une dérivation qui
# repartirait de `tenant_id` se comparerait à elle-même et ne rougirait JAMAIS. Cette
# omission est le cœur du contrôle de conformité, pas un raccourci.
#
# ⚠️ **Pas de repli sur le tenant primaire, et c'est tout le sujet.** Cette expression
# rend NULL quand aucun axe ne parle — une org sans marque de front et sans membre
# qualifié. NULL veut dire « je ne sais pas », PAS « elle est à nous » : replier sur
# `%(primary)s` transformerait une absence de signal en affirmation, et le contrôle
# accuserait toute org d'un tenant sans dashboard dont les membres sont des subs nus.
# Trois états, jamais un zéro déguisé en verdict.
_ORG_DERIVE_BRUT_EXPR = f"""COALESCE(
      {_AXE_MARQUE},
      {_AXE_MEMBRE})"""

# Le tenant DÉCLARÉ, primaire compris (là où `_AXE_DECLARE` l'annule pour se laisser
# dépasser dans le COALESCE) : pour comparer, il faut la valeur, pas un trou.
_ORG_DECLARE_EXPR = """COALESCE(
      (SELECT t.slug FROM tenants t WHERE t.id = o.tenant_id),
      %(primary)s)"""

_ORG_TENANT_EXPR = f"""COALESCE(
      {_AXE_DECLARE},
      {_AXE_MARQUE},
      {_AXE_MEMBRE},
      %(primary)s)"""

_ORG_TENANT_SQL = f"""
    SELECT {_ORG_TENANT_EXPR} AS slug
      FROM orgs o WHERE o.id = %(oid)s
"""


def org_tenant_slug(org_id: int) -> str:
    """Le tenant EFFECTIF d'une organisation : `'oto'` (la nôtre) ou le slug du
    tenant tiers qui l'héberge. Union des trois axes ci-dessus, sans arbitrage.

    Sert à répondre à « cette organisation est-elle NOTRE cliente, ou celle d'un
    partenaire hébergé ? » — la question que doit poser tout dispositif qui
    S'ADRESSE au titulaire de l'org (badge, échéance, relance). Les clients d'un
    partenaire ne sont pas les nôtres : leur écrire dans son produit, c'est parler
    par-dessus lui.

    Org inconnue ⇒ tenant primaire : il n'y a personne à protéger derrière un id qui
    ne désigne rien, et rendre un tiers ici masquerait le vrai défaut (l'id est faux).
    Le fail-closed sur erreur appartient à l'appelant, pas à la lecture.

    ⚠️ Ce n'est PAS `front_brand` : la marque n'est qu'un des trois axes, et l'axe
    qui a un trou historique. Lire la colonne en direct rouvrirait ce trou.
    """
    with _connect() as conn:
        row = conn.execute(_ORG_TENANT_SQL,
                           {"primary": tenancy.PRIMARY_SLUG, "oid": int(org_id)}).fetchone()
    return (row and row["slug"]) or tenancy.PRIMARY_SLUG


def orgs_tenant_mismatches(*, limit: int = _TENANT_LIST_CAP) -> dict:
    """Les orgs dont le rattachement DÉCLARÉ est démenti par sa DÉRIVATION.

    Le garde-fou du rattachement, et la seule lecture qui JUGE `orgs.tenant_id` au
    lieu de le compter. Deux fautes, nommées séparément parce qu'elles ne se
    corrigent pas pareil :

    - **`non_declaree`** — l'org porte le tenant primaire (le DEFAULT de la colonne)
      alors que sa marque de front ou un membre qualifié la rattachent à un
      partenaire. C'est l'état qu'a laissé le provisioning tant qu'il n'écrivait pas
      la colonne : **65 orgs sur 165 le matin du 2026-09-03, ramenées à 0 le même
      jour** par la commande ci-dessous. Se corrige en écrivant la dérivation
      (`scripts/migrate_org_tenant.py`).
    - **`contredite`** — l'org est déclarée chez un partenaire, et la dérivation en
      désigne un AUTRE. Personne ne peut la produire aujourd'hui (`create_org` est
      l'écrivain unique et dérive de la même source) ; on la surveille pour le jour
      où un second écrivain apparaît. Se corrige en tranchant, pas en écrasant.

    ⚠️ **Le troisième état n'est PAS une faute, délibérément.** Une org déclarée chez
    un partenaire que la dérivation ne corrobore pas — ni marque de front, ni membre
    qualifié — n'est pas rapportée : la dérivation dit « je ne sais pas », et une
    absence de signal n'est pas une preuve du contraire. Deux populations réelles y
    vivent : l'org d'un tenant sans `dashboard_url` (pas de marque à poser) dont les
    membres sont des subs nus, et l'org qui vient de naître — `create_org` pose le
    rattachement à l'INSERT, son premier membre arrive à l'appel suivant. Traiter ce
    silence comme une faute rendrait le contrôle bruyant là où il doit être sûr.

    ⚠️ **La population est DÉRIVÉE, pas énumérée** : la requête balaie `orgs` entière,
    archivées comprises — c'est sur une archivée qu'un rattachement faux survit le
    plus longtemps sans que personne le voie.

    Rend `total` (le compte réel, sur toute la table) à part de `orgs` (la liste,
    bornée par `limit`) : un plafond posé sur une lecture qui tronque déjà annoncerait
    le chiffre de la page, pas celui de la population.

    ⚠️ **Et rend sa PORTÉE** — `jugees` / `indeterminees` (03/09/2026). `total: 0` ne
    veut pas dire « tout est conforme » mais « rien de fautif parmi ce que j'ai su
    juger », et l'écart entre les deux phrases est énorme : mesuré le jour de la
    migration du partenaire, ce contrôle jugeait 65 orgs sur 166. C'est ce silence
    non dit qui a laissé huit espaces personnels hors de la migration sans que rien ne
    le signale. Un zéro sans sa portée est un zéro déguisé en verdict.

    ⚠️ `indeterminees` **n'est pas une file d'attente de fautes**, et c'est pourquoi
    elle n'a pas de liste : la dérivation se tait aussi sur toutes NOS orgs, qui sont
    légitimement à nous. C'est une mesure de ce que l'instrument ne voit pas. Nommer
    ces orgs inviterait à les traiter comme des cas, et ce serait exactement l'erreur
    que le troisième état existe pour éviter.
    """
    sql = f"""
        WITH juge AS (
            SELECT o.id, o.name, o.archived_at,
                   {_ORG_DECLARE_EXPR}    AS declare,
                   {_ORG_DERIVE_BRUT_EXPR} AS derive
              FROM orgs o
        ),
        fautes AS (
            SELECT *, CASE WHEN declare = %(primary)s THEN 'non_declaree'
                           ELSE 'contredite' END AS faute
              FROM juge
             -- `derive IS NOT NULL` : le silence de la dérivation ne juge rien.
             WHERE derive IS NOT NULL AND derive IS DISTINCT FROM declare
        )
        SELECT id, name, archived_at, declare, derive, faute,
               (SELECT COUNT(*) FROM fautes) AS total
          FROM fautes ORDER BY id LIMIT %(cap)s
    """
    # La PORTÉE du contrôle, lue à part. Sans elle, `total: 0` se lit « tout est
    # conforme » alors qu'il veut dire « rien de ce que j'ai pu juger n'est fautif » —
    # et le contrôle est muet sur la majorité de la table (mesuré le 03/09/2026 : il
    # juge 65 orgs sur 166). Les deux phrases ne sont pas la même, et c'est celle qu'on
    # ne dit pas qui a laissé huit espaces personnels hors de la migration du partenaire
    # sans que rien ne le signale. Trois états, jamais un zéro déguisé en verdict.
    # ⚠️ `indeterminees` n'est PAS une file d'attente de fautes : la dérivation s'y tait
    # aussi sur toutes NOS orgs, qui sont légitimement à nous. C'est une mesure de ce
    # que l'instrument ne voit pas, pas une liste de suspects — d'où un compteur, et
    # aucune liste : nommer ces orgs inviterait à les traiter comme des cas.
    portee_sql = f"""
        SELECT COUNT(*) AS orgs,
               COUNT(*) FILTER (WHERE {_ORG_DERIVE_BRUT_EXPR} IS NOT NULL) AS jugees
          FROM orgs o
    """
    params = {"primary": tenancy.PRIMARY_SLUG, "cap": max(1, int(limit))}
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        portee = dict(conn.execute(portee_sql, params).fetchone())
    total = int(rows[0]["total"]) if rows else 0
    for r in rows:
        r.pop("total", None)
    return {"total": total, "orgs": rows, "tronque": total > len(rows),
            "jugees": int(portee["jugees"]),
            "indeterminees": int(portee["orgs"]) - int(portee["jugees"])}


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
