"""Relance de plateforme : l'audience, le journal des envois, les refus.

Une relance s'adresse à une **personne**, donc tout se compte par COMPTE (`users.sub`)
et jamais par organisation. Le piège est mesuré : au 2026-09-02, 64 des 78 organisations
directes vivantes sont des espaces PERSONNELS créés d'office à l'inscription — compter
l'inactivité par org, c'est écrire à quelqu'un au sujet d'un espace qu'il n'a jamais
demandé, et le message n'a alors aucun sens pour lui.

## Le filtre partenaire est dans la REQUÊTE, pas dans une consigne

Les comptes hébergés chez un tenant tiers sont **les clients de ce tenant** : leur
écrire, c'est parler par-dessus lui, dans son produit, à ses clients. L'exclusion doit
donc survivre au prochain critère qu'on ajoutera sans y penser — elle vit dans
`_AUDIENCE_SQL`, une seule fois, en amont de tout le reste, et
`tests/test_outreach_audience_db.py` rougit si un compte de partenaire entre dans une
sélection.

⚠️ **Le discriminant n'est PAS `orgs.tenant_id` seul.** Ce n'est plus parce qu'il
serait vide — il a été alimenté le 2026-09-03 (65 orgs repointées vers leur tenant, et
`create_org` le pose à la naissance depuis ce jour-là). C'est parce qu'une audience se
trie sur ce qu'on DÉRIVE, pas sur ce qu'un écrivain a écrit : cette colonne est restée
vide de l'origine au 2026-09-03, et s'y fier seul aurait alors adressé une relance à
tous les clients d'un partenaire, dans son produit, par-dessus lui. Deux axes portent,
et on prend leur UNION :

1. la **qualification du sub** (`tenancy.qualify` → préfixe `<slug>:`), qui suit
   l'émetteur du jeton et rien d'autre — c'est `tenants._SUB_TENANT_SQL` ;
2. l'appartenance à au moins une org dont le tenant EFFECTIF est le nôtre — c'est
   `tenants._ORG_TENANT_EXPR`, l'union des trois axes qui rattachent une ORG, et la
   MÊME expression que celle servie par `tenants.org_tenant_slug` (source unique :
   deux définitions du « chez le partenaire » divergent toujours, et c'est la moins
   prudente qui l'emporte en silence).

Le (2) couvre l'angle mort du (1) : un compte inscrit chez NOUS (sub nu) mais invité
uniquement dans des orgs de partenaire. Mesuré à 0 aujourd'hui, ce qui ne dit rien de
demain. Un compte SANS aucune appartenance est **exclu** : on ne sait pas de qui il
est, et le sens du refus va vers l'exclusion.

⚠️ **(1) est REDONDANT avec (2), et c'est mesuré, pas supposé.** Un sub qualifié est
forcément membre d'une org que sa seule présence fait lire comme celle du partenaire
(`org_tenant_slug` retient le tenant d'un membre qualifié) : (2) l'écarte donc déjà.
On garde (1) en profondeur — il ne coûte rien et il redeviendra le seul axe le jour où
la qualification et l'appartenance divergeront. Corollaire à connaître : **un seul
membre qualifié suffit à sortir TOUTE une org de l'audience**, y compris ses membres à
nous. Sur-exclusion assumée (rater une relance ne coûte rien), mais elle peut vider une
audience sans rien dire — `tests/test_outreach_audience_db.py` la nomme.
"""
from __future__ import annotations

from typing import Optional

from .. import tenancy
from ._conn import _connect
from .tenants import _ORG_TENANT_EXPR, _SUB_TENANT_SQL, _TENANT_PREF_SQL

# Plafond dur d'un envoi en masse. Ce n'est pas une pagination : c'est la borne d'un
# geste irréversible. Au-delà, l'opérateur découpe en campagnes — ce qui l'oblige à
# regarder deux fois.
MAX_ENVOI = 200

# Fenêtre par défaut au-delà de laquelle un compte qui a DÉJÀ appelé est dit silencieux.
DEFAULT_SILENCE_DAYS = 30

# Les deux populations qu'on ne doit pas confondre. « jamais_actif » = n'a jamais
# appelé un outil (le lot demandé) ; « silencieux » = a appelé, puis plus rien depuis
# `silence_days`. Ce ne sont pas les mêmes personnes et le message n'est pas le même.
STATUTS = ("jamais_actif", "silencieux")


# `%(primary)s` (le slug du tenant primaire), `%(campaign)s`, `%(days)s` et `%(cap)s`
# sont liés par l'appelant. L'ORDRE des filtres est intentionnel : le partenaire est
# écarté AVANT tout critère d'activité, pour qu'ajouter un critère plus bas ne puisse
# jamais le rouvrir.
_AUDIENCE_SQL = f"""
WITH pref AS ({_TENANT_PREF_SQL}),
     sub_tenant AS ({_SUB_TENANT_SQL}),
     org_tenant AS (SELECT o.id AS org_id, {_ORG_TENANT_EXPR} AS slug FROM orgs o),
     -- (1) le sub n'est pas qualifié sous un tenant tiers, ET (2) le compte est membre
     -- d'au moins une org dont le tenant effectif est le nôtre. Un compte sans aucune
     -- appartenance ne passe pas ce EXISTS — exclu, et c'est le bon sens du refus.
     notres AS (
         SELECT u.sub, u.email, u.name, u.locale, u.created_at
           FROM users u
           JOIN sub_tenant st ON st.sub = u.sub
           JOIN tenants t ON t.id = st.tenant_id
          WHERE t.slug = %(primary)s
            AND EXISTS (SELECT 1 FROM org_members om
                          JOIN org_tenant ot ON ot.org_id = om.org_id
                         WHERE om.sub = u.sub AND ot.slug = %(primary)s)
     ),
     appels AS (
         SELECT sub, COUNT(*) AS appels, MAX(created_at) AS last_seen_at
           FROM tool_calls WHERE kind = 'mcp' AND sub IS NOT NULL GROUP BY sub
     )
SELECT {{projection}}
  FROM notres n
  LEFT JOIN appels a ON a.sub = n.sub
 WHERE n.email IS NOT NULL AND btrim(n.email) <> ''
   -- Un refus vaut pour toute campagne : il se lit ici, pas au moment d'envoyer.
   AND NOT EXISTS (SELECT 1 FROM outreach_optouts o WHERE o.sub = n.sub)
   -- Déjà relancé sur CETTE campagne ⟹ hors audience (l'index unique le garantit de
   -- toute façon à l'écriture ; ici, c'est pour que le compte affiché soit juste).
   AND NOT EXISTS (SELECT 1 FROM outreach_sends s
                    WHERE s.sub = n.sub AND s.campaign = %(campaign)s AND s.kind = 'send')
   AND {{critere}}
"""

# Les colonnes servies. À part de la requête pour qu'on puisse compter l'audience
# ENTIÈRE avec exactement les mêmes filtres — sans quoi le nombre annoncé avant un
# envoi ne serait que celui de la page servie.
_COLONNES = """n.sub, n.email, n.name, n.locale, n.created_at,
       COALESCE(a.appels, 0) AS appels, a.last_seen_at,
       (SELECT COUNT(*) FROM outreach_sends s
         WHERE s.sub = n.sub AND s.kind = 'send') AS relances_deja_recues"""

_CRITERE = {
    # « n'a jamais rien fait » : aucun appel d'OUTIL. Un compte peut avoir des lignes
    # `rest`/`protocol` (il a ouvert le dashboard, ou son client a fait un handshake)
    # sans avoir jamais rien demandé à la plateforme — c'est bien un compte à relancer.
    "jamais_actif": "COALESCE(a.appels, 0) = 0",
    "silencieux": ("COALESCE(a.appels, 0) > 0 AND "
                   "a.last_seen_at < NOW() - make_interval(days => %(days)s)"),
}


def _params(campaign: str, silence_days: int, cap: int) -> dict:
    return {"primary": tenancy.PRIMARY_SLUG, "campaign": campaign,
            "days": int(silence_days), "cap": max(1, min(int(cap), MAX_ENVOI))}


def _critere(statut: str) -> str:
    if statut not in _CRITERE:
        raise ValueError(f"statut inconnu : {statut!r} (attendu : {', '.join(STATUTS)})")
    return _CRITERE[statut]


def audience(*, campaign: str, statut: str = "jamais_actif",
             silence_days: int = DEFAULT_SILENCE_DAYS, cap: int = MAX_ENVOI) -> list[dict]:
    """Les comptes à relancer, filtre partenaire déjà appliqué. Bornée par `cap`."""
    sql = (_AUDIENCE_SQL.format(projection=_COLONNES, critere=_critere(statut))
           + " ORDER BY n.created_at ASC LIMIT %(cap)s")
    with _connect() as conn:
        rows = conn.execute(sql, _params(campaign, silence_days, cap)).fetchall()
    return [dict(r) for r in rows]


def taille_audience(*, campaign: str, statut: str = "jamais_actif",
                    silence_days: int = DEFAULT_SILENCE_DAYS) -> int:
    """Combien de personnes la sélection contient VRAIMENT — sans plafond.

    Séparé de `audience()` pour une raison précise : le plafond y TRONQUE. Sans ce
    compte, un opérateur devant 200 lignes ne saurait pas s'il en reste 3 ou 3 000,
    croirait sa campagne finie, et la troncature — silencieuse — serait pire qu'un
    refus. C'est ce nombre-là qu'un envoi annonce avant de partir.
    """
    sql = ("SELECT COUNT(*) AS n FROM ("
           + _AUDIENCE_SQL.format(projection="1", critere=_critere(statut)) + ") x")
    with _connect() as conn:
        return int(conn.execute(sql, _params(campaign, silence_days, MAX_ENVOI))
                   .fetchone()["n"])


# ── Journal des envois ───────────────────────────────────────────────────────

def enregistre_envoi(*, campaign: str, sub: str, to_email: str, locale: str,
                     fingerprint: str, kind: str = "send",
                     sent_by: Optional[str] = None) -> bool:
    """Trace un envoi. **Appelé AVANT l'envoi**, et c'est le point : l'index unique
    `(campaign, sub)` est ce qui empêche un doublon, donc il doit se refermer avant
    que le mail parte. Rend False si la ligne existait déjà (⟹ ne pas envoyer).

    Un `kind='test'` n'est pas contraint : on se refait autant d'essais qu'on veut.
    """
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO outreach_sends
                 (campaign, sub, to_email, locale, kind, fingerprint, sent_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (campaign, sub, to_email, locale, kind, fingerprint, sent_by))
        return (cur.rowcount or 0) > 0


def annule_envoi(*, campaign: str, sub: str) -> None:
    """Retire la trace d'un envoi qui n'est pas parti (le mailer a rendu False).

    Sans ça, un échec de transport condamnerait le compte : la ligne le sortirait de
    toute audience future alors qu'il n'a jamais rien reçu.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM outreach_sends WHERE campaign = %s AND sub = %s "
                     "AND kind = 'send'", (campaign, sub))


def locales_essayees(*, campaign: str, fingerprint: str) -> set:
    """Les langues pour lesquelles un essai a été envoyé, sur CE contenu exact.

    L'empreinte porte le contenu (sujet + corps + CTA, les deux langues) : retoucher
    une virgule après l'essai invalide l'essai. C'est voulu — « je l'ai vu arriver
    dans ma boîte » ne vaut que pour le message qu'on a vu.

    Rendre l'ENSEMBLE des langues plutôt qu'un booléen est ce qui permet à l'envoi
    d'exiger un essai pour chaque langue qu'il va réellement servir — et seulement
    pour celles-là : réclamer un essai anglais quand personne ne recevra d'anglais
    serait une formalité, et une formalité finit par se contourner.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT locale FROM outreach_sends WHERE campaign = %s "
            "AND fingerprint = %s AND kind = 'test'", (campaign, fingerprint)).fetchall()
        return {r["locale"] for r in rows}


def journal(*, campaign: Optional[str] = None, cap: int = 200) -> list[dict]:
    """Qui a été relancé, quand, dans quelle langue, par qui."""
    where, params = "", []
    if campaign:
        where = "WHERE campaign = %s"
        params.append(campaign)
    params.append(max(1, int(cap)))
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT s.id, s.campaign, s.sub, s.to_email, s.locale, s.kind,
                       s.fingerprint, s.sent_by, s.sent_at,
                       (SELECT 1 FROM outreach_optouts o WHERE o.sub = s.sub) IS NOT NULL
                       AS desinscrit
                  FROM outreach_sends s {where}
                 ORDER BY s.sent_at DESC LIMIT %s""", tuple(params)).fetchall()
        return [dict(r) for r in rows]


# ── Refus de recevoir ────────────────────────────────────────────────────────

def desinscrire(sub: str, *, source: str = "link") -> None:
    """Idempotent : se désinscrire deux fois ne change ni la date ni la provenance.

    Aucune vérification que le compte existe : la FK s'en charge, et un jeton signé
    qui nomme un compte disparu ne doit pas fabriquer une erreur au destinataire.
    """
    with _connect() as conn:
        conn.execute("INSERT INTO outreach_optouts (sub, source) VALUES (%s, %s) "
                     "ON CONFLICT (sub) DO NOTHING", (sub, source))


def reinscrire(sub: str) -> bool:
    """Lève un refus (acte d'opérateur, sur demande explicite du titulaire)."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM outreach_optouts WHERE sub = %s", (sub,))
        return (cur.rowcount or 0) > 0


def est_desinscrit(sub: str) -> bool:
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM outreach_optouts WHERE sub = %s",
                            (sub,)).fetchone() is not None


def desinscrits(cap: int = 200) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT o.sub, u.email, o.source, o.opted_out_at FROM outreach_optouts o "
            "LEFT JOIN users u ON u.sub = o.sub ORDER BY o.opted_out_at DESC LIMIT %s",
            (max(1, int(cap)),)).fetchall()
        return [dict(r) for r in rows]
