"""L'audience d'une relance, exercée sur le SQL réel — et surtout : sur qui elle EXCLUT.

Ce fichier existe pour une phrase : **les comptes d'un tenant partenaire ne doivent
jamais entrer dans une sélection.** Ce sont les clients de ce partenaire ; leur écrire,
c'est parler par-dessus lui, dans son produit, à ses clients. Une consigne ne suffit
pas — elle sera oubliée le jour où quelqu'un ajoutera un critère —, donc l'exclusion
vit dans la requête et ce test rougit si elle en sort.

**Deux axes, deux comptes-témoins.** Le filtre est une UNION, et chacune de ses moitiés
a son angle mort :

- `tulina:…` — le sub QUALIFIÉ. C'est l'axe évident.
- `sub-invite` — un sub NU (inscrit chez nous), membre uniquement d'orgs de partenaire.
  Il passe le premier axe sans encombre. Mesuré à zéro en prod le 2026-09-02, ce qui ne
  dit rien de demain — et c'est précisément la population que la seconde moitié couvre.

⚠️ **Ce que ce test NE prouve pas** : `orgs.tenant_id` reste dans l'expression parce
qu'il ne coûte rien, mais il est INERTE en prod (160 orgs sur 160 portent le tenant
primaire, partenaires compris — le provisioning ne l'écrit pas). Un filtre bâti sur ce
seul axe serait vert ici et ne rattraperait rien là-bas : `orgs_tenant_id` ci-dessous
est donc un troisième témoin, pas une garantie.

Patron de base éphémère : `test_migrate_sub_group_grants_db.py::live`.
"""
from __future__ import annotations

import os
import uuid

import pytest

CAMPAGNE = "relance-test"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_outreach_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        _peupler()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url), ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _peupler() -> None:
    """La population : un compte par situation, nommé par ce qu'il DOIT devenir."""
    from oto_mcp.db._conn import _connect

    comptes = [
        # (sub, email, locale) — tous inactifs sauf mention contraire
        ("sub-jamais", "jamais@exemple.test", None),
        ("sub-jamais-fr", "jamais-fr@exemple.test", "fr"),
        ("sub-jamais-en", "jamais-en@exemple.test", "en"),
        ("sub-actif", "actif@exemple.test", None),
        ("sub-dormant", "dormant@exemple.test", None),
        ("sub-desinscrit", "desinscrit@exemple.test", None),
        ("sub-sans-email", None, None),
        ("sub-deja-relance", "deja@exemple.test", None),
        ("sub-invite", "invite@exemple.test", None),          # sub NU, orgs de partenaire
        ("sub-orgs-tenant-id", "tid@exemple.test", None),     # org portant tenant_id tiers
        ("sub-sans-org", "sansorg@exemple.test", None),       # aucune appartenance
        ("tulina:sub-partenaire", "partenaire@exemple.test", None),
        # Qualifié chez le partenaire, invité dans une org à NOUS. Il ne prouve
        # rien sur la qualification du sub (voir plus bas : cet axe est redondant) —
        # il documente la CONTAGION, avec son voisin.
        ("tulina:sub-croise", "croise@exemple.test", None),
        ("sub-voisin-du-croise", "voisin@exemple.test", None),
    ]
    with _connect() as conn:
        conn.execute("INSERT INTO tenants (slug, name, issuer) VALUES "
                     "('oto', 'oto', NULL) ON CONFLICT (slug) DO NOTHING")
        conn.execute("INSERT INTO tenants (slug, name, issuer) VALUES "
                     "('tulina', 'Partenaire', 'https://auth.exemple.test') "
                     "ON CONFLICT (slug) DO NOTHING")
        tid_partenaire = conn.execute(
            "SELECT id FROM tenants WHERE slug = 'tulina'").fetchone()["id"]

        for sub, email, locale in comptes:
            conn.execute("INSERT INTO users (sub, email, locale) VALUES (%s, %s, %s)",
                         (sub, email, locale))

        def org(nom, brand=None, tenant_id=1):
            # `orgs.tenant_id` est NOT NULL DEFAULT 1 (le tenant primaire) : c'est
            # justement pourquoi il ne discrimine rien en prod.
            row = conn.execute(
                "INSERT INTO orgs (name, front_brand, tenant_id) VALUES (%s, %s, %s) "
                "RETURNING id", (nom, brand, tenant_id)).fetchone()
            return row["id"]

        maison = org("Maison")
        chez_le_partenaire = org("Chez le partenaire", brand="tulina")
        par_tenant_id = org("Rattachée par tenant_id", tenant_id=tid_partenaire)
        # Une org à NOUS (aucune marque, tenant primaire) où l'on a invité un compte
        # qualifié chez le partenaire. Isolée exprès de « Maison » : sans ça elle
        # emporterait toute la population du fichier (cf. le test de contagion).
        maison_bis = org("Maison bis")

        for sub, _e, _l in comptes:
            if sub == "sub-sans-org":
                continue
            cible = maison
            if sub == "sub-invite":
                cible = chez_le_partenaire
            elif sub == "sub-orgs-tenant-id":
                cible = par_tenant_id
            elif sub == "tulina:sub-partenaire":
                cible = chez_le_partenaire
            elif sub in ("tulina:sub-croise", "sub-voisin-du-croise"):
                cible = maison_bis
            conn.execute("INSERT INTO org_members (org_id, sub) VALUES (%s, %s)",
                         (cible, sub))

        # Activité : l'actif a appelé hier, le dormant il y a 90 jours.
        conn.execute("INSERT INTO tool_calls (sub, tool, kind, created_at) VALUES "
                     "(%s, 'oto_whoami', 'mcp', NOW() - INTERVAL '1 day')", ("sub-actif",))
        conn.execute("INSERT INTO tool_calls (sub, tool, kind, created_at) VALUES "
                     "(%s, 'oto_whoami', 'mcp', NOW() - INTERVAL '90 days')", ("sub-dormant",))
        # Une trace REST ne compte pas : ouvrir le dashboard n'est pas se servir d'oto.
        conn.execute("INSERT INTO tool_calls (sub, tool, kind) VALUES "
                     "(%s, 'GET /api/me', 'rest')", ("sub-jamais",))

        conn.execute("INSERT INTO outreach_optouts (sub) VALUES (%s)", ("sub-desinscrit",))
        conn.execute(
            "INSERT INTO outreach_sends (campaign, sub, to_email, locale, kind, fingerprint) "
            "VALUES (%s, %s, 'deja@exemple.test', 'fr', 'send', 'abc')",
            (CAMPAGNE, "sub-deja-relance"))


def _subs(**kw) -> set:
    from oto_mcp.db import outreach
    return {r["sub"] for r in outreach.audience(campaign=CAMPAGNE, **kw)}


# ── l'exclusion partenaire, la raison d'être du fichier ──────────────────────

def test_aucun_compte_de_partenaire_n_entre_dans_une_audience(live):
    """Les trois axes, chacun par son témoin. Retirer n'importe lequel de
    `db/outreach.py::_AUDIENCE_SQL` fait rougir cette assertion — c'est le seul
    contrôle qui protège les clients d'un tiers."""
    for statut in ("jamais_actif", "silencieux"):
        selection = _subs(statut=statut, silence_days=30)
        assert not {"tulina:sub-partenaire", "tulina:sub-croise"} & selection, (
            f"[{statut}] un compte au sub QUALIFIÉ sous un tenant tiers est entré "
            "dans l'audience — on s'apprête à écrire aux clients d'un partenaire.")
        assert "sub-invite" not in selection, (
            f"[{statut}] un compte au sub NU, membre des seules orgs d'un partenaire, "
            "est entré dans l'audience. C'est l'angle mort de la qualification du sub, "
            "et la raison pour laquelle le filtre est une UNION.")
        assert "sub-orgs-tenant-id" not in selection, (
            f"[{statut}] une org rattachée par `orgs.tenant_id` à un tenant tiers n'a "
            "pas écarté son membre.")


def test_un_membre_QUALIFIE_rend_toute_son_org_partenaire_donc_ses_voisins_sortent(live):
    """**Sur-exclusion assumée, et nommée ici plutôt que découverte un jour.**

    Le tenant EFFECTIF d'une org est celui de `org_tenant_slug` : un seul membre au
    sub qualifié suffit à la faire lire comme celle du partenaire, et TOUS ses membres
    quittent alors l'audience. Le sens du refus est délibéré — rater une relance ne
    coûte rien, écrire aux clients d'un tiers coûte le partenariat — mais l'effet
    n'est pas anodin : il peut vider une audience sans rien dire. Si l'audience
    servie paraît trop petite, c'est ici qu'il faut regarder.

    ⚠️ Conséquence, mesurée par les mutations plus bas : la qualification du sub est
    REDONDANTE avec cette ceinture (un compte qualifié est toujours, aussi, membre
    d'une org que sa seule présence rend partenaire). Elle est gardée en profondeur,
    pas parce qu'elle mord seule."""
    selection = _subs()
    assert "sub-voisin-du-croise" not in selection
    assert "tulina:sub-croise" not in selection


def test_un_compte_sans_aucune_appartenance_est_ECARTE(live):
    """Le sens du refus va vers l'exclusion : on ne sait pas de qui il est."""
    assert "sub-sans-org" not in _subs()


# ── « jamais actif » veut dire jamais un APPEL D'OUTIL ───────────────────────

def test_jamais_actif_retient_ceux_qui_n_ont_jamais_appele_un_outil(live):
    selection = _subs(statut="jamais_actif")
    assert {"sub-jamais", "sub-jamais-fr", "sub-jamais-en"} <= selection
    assert "sub-actif" not in selection
    assert "sub-dormant" not in selection, (
        "un compte qui a DÉJÀ appelé n'est pas « jamais actif » — ce sont deux "
        "populations distinctes, et le message n'est pas le même.")


def test_une_trace_REST_ne_vaut_pas_un_appel_d_outil(live):
    """`sub-jamais` porte une ligne `kind='rest'` : il a ouvert le dashboard et rien
    demandé de plus. C'est exactement quelqu'un à relancer."""
    assert "sub-jamais" in _subs(statut="jamais_actif")


def test_silencieux_retient_ceux_qui_ont_appele_puis_plus_rien(live):
    selection = _subs(statut="silencieux", silence_days=30)
    assert "sub-dormant" in selection
    assert "sub-actif" not in selection
    assert "sub-jamais" not in selection, (
        "« ne fait plus rien » exclut « n'a jamais rien fait » : sans appel antérieur, "
        "il n'y a pas de silence à constater.")


# ── les trois soustractions qui protègent le destinataire ────────────────────

def test_un_desinscrit_quitte_toute_audience(live):
    assert "sub-desinscrit" not in _subs()


def test_un_compte_deja_relance_sur_CETTE_campagne_sort(live):
    assert "sub-deja-relance" not in _subs(), "on ne relance pas deux fois la même personne"
    assert "sub-deja-relance" in _subs_autre_campagne(), (
        "l'exclusion est propre à la campagne : une AUTRE relance doit pouvoir "
        "l'atteindre, sinon un envoi condamnerait définitivement ses destinataires.")


def _subs_autre_campagne() -> set:
    from oto_mcp.db import outreach
    return {r["sub"] for r in outreach.audience(campaign="une-autre-campagne")}


def test_un_compte_sans_adresse_sort(live):
    assert "sub-sans-email" not in _subs(), "aucune adresse ⟹ rien à envoyer"


# ── la langue : ce qu'on SAIT, jamais ce qu'on devine ────────────────────────

def test_la_preference_declaree_est_rendue_telle_quelle(live):
    from oto_mcp.db import outreach
    par_sub = {r["sub"]: r for r in outreach.audience(campaign=CAMPAGNE)}
    assert par_sub["sub-jamais-fr"]["locale"] == "fr"
    assert par_sub["sub-jamais-en"]["locale"] == "en"
    assert par_sub["sub-jamais"]["locale"] is None, (
        "aucune préférence déclarée ⟹ None. Y mettre une valeur devinée ferait passer "
        "un choix d'opérateur pour une donnée de compte.")


# ── la preuve que c'est bien le FILTRE qui protège ───────────────────────────
#
# Une garde jamais vue rouge n'en est pas une : les assertions ci-dessus seraient
# vertes si le jeu de données ne contenait, par accident, aucun compte de partenaire
# atteignable. Les deux tests suivants RETIRENT une moitié du filtre et exigent que le
# compte-témoin correspondant entre alors dans l'audience. Ils échouent bruyamment si
# le fragment visé n'existe plus sous cette forme — c'est voulu : une reformulation du
# SQL doit forcer à re-viser la mutation, jamais à la laisser porter dans le vide.

_CEINTURE = """AND EXISTS (SELECT 1 FROM org_members om
                          JOIN org_tenant ot ON ot.org_id = om.org_id
                         WHERE om.sub = u.sub AND ot.slug = %(primary)s)"""


def _audience_sans(*mutations):
    """Rejoue `_AUDIENCE_SQL` amputé d'un ou plusieurs fragments. Chaque mutation est
    une paire `(fragment, remplacement)`."""
    from oto_mcp.db import outreach
    mute = outreach._AUDIENCE_SQL
    for fragment, remplacement in mutations:
        assert fragment in mute, (
            "fragment introuvable dans `_AUDIENCE_SQL` — la requête a été reformulée. "
            "Ré-vise cette mutation sur la nouvelle forme AVANT de croire les tests "
            f"voisins : sans elle, plus rien ne prouve que le filtre mord.\n{fragment!r}")
        mute = mute.replace(fragment, remplacement, 1)
    sql = mute.format(projection=outreach._COLONNES,
                      critere=outreach._CRITERE["jamais_actif"])
    from oto_mcp import tenancy
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        rows = conn.execute(sql, {"primary": tenancy.PRIMARY_SLUG, "campaign": CAMPAGNE,
                                  "days": 30, "cap": 500}).fetchall()
    return {r["sub"] for r in rows}


def test_sans_AUCUNE_des_deux_moities_le_partenaire_ENTRE(live):
    """La preuve que le jeu de données n'est pas vide de sa cible : sans le filtre,
    le compte du partenaire arrive bien dans la sélection. Sans ce test, toutes les
    assertions « n'est pas dans l'audience » pourraient être vertes pour la mauvaise
    raison — un fixture qui ne contient personne d'atteignable."""
    assert "tulina:sub-partenaire" in _audience_sans(
        ("t.slug = %(primary)s", "TRUE"), (_CEINTURE, "AND TRUE"))


def _CEINTURE_OTEE() -> set:
    return _audience_sans((_CEINTURE, "AND TRUE"))


def test_sans_la_ceinture_par_appartenance_l_invite_ENTRE(live):
    """La moitié qui porte VRAIMENT — celle qui couvre l'angle mort de l'autre : un
    sub NU qui ne vit que chez un partenaire, et qu'aucune qualification n'attrape."""
    entrants = _CEINTURE_OTEE()
    assert {"sub-invite", "sub-orgs-tenant-id", "sub-sans-org",
            "sub-voisin-du-croise"} <= entrants
