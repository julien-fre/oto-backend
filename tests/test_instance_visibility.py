"""R9 — qui VOIT une instance, dérivé de la chaîne d'accès.

Deux moitiés, et la seconde est celle qui compte.

**1. La forme** (sans base) : l'audience de chaque palier, les prêts nominatifs, les
deux surcharges du propriétaire, et les gates qui rendent une audience VIDE — une clé
d'équipe sur un connecteur par-personne, une clé plateforme sur un connecteur byo-only.
Une audience vide n'est pas un bug : c'est une clé que la résolution ne lit jamais, et
l'annoncer visible serait un mensonge.

**2. La CONFRONTATION** (vrai PostgreSQL) : pour chaque appelant, l'ensemble des
instances dont l'audience le contient doit être EXACTEMENT l'ensemble des barreaux que
`walk_cascade` lui rend. C'est le seul garde-fou qui vaille contre le défaut que
`keyStack.ts` porte déjà côté dashboard — un miroir de la cascade que rien ne relie à
elle, et qui ne casse pas : il ment. Ici, s'ils divergent, ce test rougit.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp.connectors import instance_visibility as vis

SUB_A, SUB_B = "usr_vis_a", "usr_vis_b"
ORG, GROUP = 1, 7
MEMBRE_A = f"{ORG}:{SUB_A}"

# `hunter` : byo_user + byo_org + platform, free-tier ouvert, et BASCULÉ sur la chaîne
# de grants. `crunchbase` : byo_user seul, session par personne — il n'est ni
# org-partageable ni éligible au palier plateforme, donc il porte les cas VIDES.
PARTAGEABLE, PERSO = "hunter", "crunchbase"


# ─── 1. La forme, palier par palier ──────────────────────────────────────────

def test_une_cle_de_membre_n_est_vue_que_de_son_proprietaire():
    """Jamais cross-org : l'instance personnelle qui me suit d'une autre org (#172)
    est la MIENNE vue d'ailleurs, donc le même scope, pas un scope de plus."""
    assert vis.derive("member", MEMBRE_A, PARTAGEABLE) == [f"user:{SUB_A}"]


def test_une_cle_d_equipe_est_vue_de_l_equipe():
    assert vis.derive("group", str(GROUP), PARTAGEABLE) == [f"group:{GROUP}"]


def test_une_cle_d_org_est_vue_de_l_org():
    assert vis.derive("org", str(ORG), PARTAGEABLE) == [f"org:{ORG}"]


def test_un_palier_partage_sur_un_connecteur_PAR_PERSONNE_n_est_vu_de_personne():
    """La clé existe au coffre, et la cascade ne la lit jamais : ses barreaux équipe
    et org sont gatés sur `org_shareable`. Annoncer une audience serait annoncer un
    accès qui n'existe pas."""
    assert vis.derive("group", str(GROUP), PERSO) == []
    assert vis.derive("org", str(ORG), PERSO) == []


def test_le_residu_oauth_user_reste_vu_de_son_sujet():
    """`entity_type='user'` est le résidu des mounts OAuth (ADR 0033) : hors cascade
    de travail, mais c'est bien un credential, et il a un propriétaire."""
    assert vis.derive("user", SUB_A, "google") == [f"user:{SUB_A}"]


def test_les_prets_nominatifs_s_AJOUTENT_a_tous_les_paliers():
    """`share_side` est une EXTENSION (ADR 0044), pas une allowlist : un prêt ne
    remplace jamais l'audience, il l'élargit — et il peut viser hors de l'org, parce
    que c'est un acte explicite du propriétaire, pas une découverte."""
    assert vis.derive("member", MEMBRE_A, PARTAGEABLE,
                      share_side=["user:usr_pair", "group:9"]) == \
        ["group:9", "user:usr_pair", f"user:{SUB_A}"]


# ─── 2. Le palier plateforme, où l'audience n'est pas structurelle ───────────

def test_une_cle_plateforme_FERMEE_n_est_vue_que_de_ses_beneficiaires():
    assert vis.derive("platform", "env", PARTAGEABLE, share_mode="closed",
                      share_down=["org:8", "user:usr_x"]) == ["org:8", "user:usr_x"]


def test_une_cle_plateforme_fermee_SANS_beneficiaire_n_est_vue_de_personne():
    """`closed` + allowlist vide = fermé par défaut. C'est la polarité du vide
    (ADR 0044 §F) et elle s'inverse d'un palier à l'autre — d'où ce test."""
    assert vis.derive("platform", "env", PARTAGEABLE, share_mode="closed") == []


def test_une_cle_plateforme_OUVERTE_est_vue_de_tout_le_monde():
    """Le free-tier. On rend un mot (`platform`) et pas la liste des subs : elle
    changerait à chaque inscription, et la liste vide voudrait dire l'inverse."""
    assert vis.derive("platform", "env", PARTAGEABLE) == [vis.EVERYONE]


def test_une_cle_plateforme_ouverte_AVEC_allowlist_se_referme_dessus():
    """`open` ne veut pas dire « ouvert » mais « le vide de l'allowlist vaut tout le
    monde » — miroir exact de `_platform_instance_usable`."""
    assert vis.derive("platform", "env", PARTAGEABLE, share_down=["org:8"]) == ["org:8"]


def test_une_cle_plateforme_sur_un_connecteur_byo_only_n_est_vue_de_personne():
    """Le palier plateforme de la cascade est gaté sur `auth_modes` : un connecteur
    par-personne ne résout JAMAIS une clé plateforme."""
    assert vis.derive("platform", "env", PERSO) == []


def test_la_chaine_de_grants_PRIME_sur_le_partage_de_la_ligne(monkeypatch):
    """Le connecteur est basculé et des arêtes existent : l'audience EST l'ensemble
    des bénéficiaires vivants — le free-tier de la ligne ne la rouvre pas."""
    monkeypatch.setattr(vis, "_chain_grantees",
                        lambda ref: (["org:8", "user:usr_y"], True))
    assert vis.derive("platform", "env", PARTAGEABLE) == ["org:8", "user:usr_y"]


def test_une_chaine_qui_REFUSE_ferme_l_audience_sans_repli(monkeypatch):
    """Des arêtes existent, toutes révoquées : plus personne. Sans ce cas, révoquer
    une arête ne couperait rien — l'ancien chemin free-tier re-accorderait aussitôt,
    et « la révocation coupe l'accès » serait faux."""
    monkeypatch.setattr(vis, "_chain_grantees", lambda ref: ([], True))
    assert vis.derive("platform", "env", PARTAGEABLE) == []


def test_une_chaine_MUETTE_retombe_sur_l_ancien_chemin(monkeypatch):
    """Aucune arête n'a jamais visé cette clé : l'ancien chemin, à l'identique."""
    monkeypatch.setattr(vis, "_chain_grantees", lambda ref: ([], False))
    assert vis.derive("platform", "env", PARTAGEABLE) == [vis.EVERYONE]


# ─── 3. Les surcharges du propriétaire ───────────────────────────────────────

def test_masquer_ramene_l_audience_au_seul_proprietaire():
    """⚠️ `hidden` est un cran d'ERGONOMIE, pas de sécurité : masquer ne protège de
    rien (0053-D2, tout se refuse à l'appel). Celui qui résout continue de résoudre ;
    il cesse seulement de la voir listée comme un objet partageable."""
    assert vis.derive("org", str(ORG), PARTAGEABLE, visibility=vis.HIDDEN) == \
        [f"org:{ORG}"]
    assert vis.derive("member", MEMBRE_A, PARTAGEABLE, visibility=vis.HIDDEN,
                      share_side=["user:usr_pair"]) == [f"user:{SUB_A}"]


def test_elargir_a_l_org_ajoute_l_org_du_proprietaire():
    """La piste notée par R9 (« un réglage d'org, opt-in ») : ma clé devient
    DÉCOUVRABLE par mon org — ce qui ne la rend pas résolvable par elle."""
    assert vis.derive("member", MEMBRE_A, PARTAGEABLE, visibility=vis.ORG_WIDE) == \
        [f"org:{ORG}", f"user:{SUB_A}"]


def test_aucune_surcharge_n_est_posee_aujourd_hui():
    """La colonne existe, son défaut est `inherited`, et **rien ne l'écrit** : aucune
    surface ne pose `hidden` ni `org`. Les deux branches ci-dessus sont écrites et
    testées, pas servies — le geste qui les pose est un lot produit. Ce test le fige :
    si quelqu'un se met à l'écrire, il devra le dire ici."""
    import pathlib
    racine = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
    ecrivains = []
    for p in racine.rglob("*.py"):
        texte = p.read_text(encoding="utf-8")
        if "visibility = %s" in texte or "SET visibility" in texte:
            ecrivains.append(p.relative_to(racine).as_posix())
    assert not ecrivains, (
        f"quelqu'un ÉCRIT `connector_instances.visibility` : {ecrivains}. C'est un lot "
        "produit (R9 : « un réglage d'org, opt-in »), avec sa surface et sa revue — "
        "pas un effet de bord.")


# ─── 4. LA CONFRONTATION : l'audience dérivée == ce que le walker rend ───────

@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_vis_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "2" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


@pytest.fixture
def coffre_quatre_paliers(live):
    """Une clé à CHACUN des quatre paliers du même connecteur, et deux appelants :
    A (membre de l'org, de l'équipe, avec sa clé perso) et B (membre de l'org seule)."""
    from oto_mcp import credentials_store as cs

    _exec("DELETE FROM connector_instances")
    _exec("DELETE FROM connector_credentials")
    _exec("DELETE FROM grants")
    _exec("INSERT INTO orgs (id, name) VALUES (%s, 'o') ON CONFLICT DO NOTHING", (ORG,))
    _exec("INSERT INTO org_groups (id, org_id, name) VALUES (%s, %s, 'g') "
          "ON CONFLICT DO NOTHING", (GROUP, ORG))
    for s in (SUB_A, SUB_B):
        _exec("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (s,))
    cs.set_credential("member", MEMBRE_A, PARTAGEABLE, "k", set_by=SUB_A)
    cs.set_credential("group", str(GROUP), PARTAGEABLE, "k", set_by=SUB_A)
    cs.set_credential("org", str(ORG), PARTAGEABLE, "k", set_by=SUB_A)
    cs.set_credential("platform", "env", PARTAGEABLE, "k", set_by="system")


def _audience(quad) -> list[str]:
    """L'audience dérivée d'une ligne de coffre, lue comme la sert la projection."""
    from oto_mcp import credentials_store as cs
    from oto_mcp.db import connector_instances as ci

    mode, down, side = cs.sharing_for_vault_rows([quad])[quad]
    inst = ci.instances_for_vault_rows([quad])[quad]
    return vis.derive(quad[0], quad[1], quad[2], account=quad[3],
                      visibility=inst["visibility"], share_mode=mode,
                      share_down=down, share_side=side)


def _barreaux_du_walker(sub, groupe) -> set[tuple[str, str]]:
    """Les (entity_type, entity_id) que la cascade RÉELLE rend à cet appelant."""
    from oto_mcp import access
    rungs = access.walk_cascade(sub, PARTAGEABLE, org=ORG, group=groupe,
                                probe=access.PRESENCE_PROBE)
    return {(r.entity_type, str(r.entity_id)) for r in rungs}


def _quadruplets_visibles(scopes: set[str]) -> set[tuple[str, str]]:
    """Les instances dont l'audience contient l'un des scopes de l'appelant."""
    quads = [("member", MEMBRE_A, PARTAGEABLE, ""), ("group", str(GROUP), PARTAGEABLE, ""),
             ("org", str(ORG), PARTAGEABLE, ""), ("platform", "env", PARTAGEABLE, "")]
    return {(q[0], q[1]) for q in quads
            if (set(_audience(q)) & scopes) or vis.EVERYONE in _audience(q)}


def test_l_audience_derivee_est_CELLE_du_walker_pour_un_membre_complet(
        coffre_quatre_paliers):
    """A porte sa clé, appartient à l'équipe et à l'org : les quatre paliers le
    servent, et les quatre audiences le contiennent."""
    assert _barreaux_du_walker(SUB_A, GROUP) == _quadruplets_visibles(
        {f"user:{SUB_A}", f"group:{GROUP}", f"org:{ORG}"})


def test_l_audience_derivee_est_CELLE_du_walker_pour_un_membre_sans_cle_ni_equipe(
        coffre_quatre_paliers):
    """B n'a ni clé perso ni équipe : le walker lui rend l'org et la plateforme, et
    l'audience dérivée exclut exactement les deux autres. C'est la moitié qui prouve
    quelque chose — un miroir trop généreux passerait le test précédent."""
    rendus = _barreaux_du_walker(SUB_B, None)
    assert rendus == _quadruplets_visibles({f"user:{SUB_B}", f"org:{ORG}"})
    assert ("member", MEMBRE_A) not in rendus and ("group", str(GROUP)) not in rendus


def test_fermer_la_cle_plateforme_la_retire_des_DEUX_cotes(coffre_quatre_paliers):
    """Le mouvement, pas l'état : on referme l'allowlist sur quelqu'un d'autre, et le
    walker comme l'audience doivent cesser de servir la clé à B — ensemble."""
    from oto_mcp import credentials_store as cs

    cs.set_instance_sharing("platform", "env", PARTAGEABLE,
                            share_down=["user:un_autre"])
    _exec("UPDATE connector_credentials SET share_mode='closed' "
          "WHERE entity_type='platform'")
    quad = ("platform", "env", PARTAGEABLE, "")
    assert _audience(quad) == ["user:un_autre"]
    assert ("platform", "env") not in _barreaux_du_walker(SUB_B, None)


def test_une_arete_de_la_chaine_devient_l_audience(coffre_quatre_paliers):
    """`hunter` est basculé sur la chaîne de grants : poser une arête doit se lire
    dans l'audience, et la révoquer doit la fermer — sans repli sur le free-tier,
    sinon révoquer ne couperait rien."""
    from oto_mcp import grants_chain

    quad = ("platform", "env", PARTAGEABLE, "")
    assert _audience(quad) == [vis.EVERYONE]          # muette : l'ancien chemin

    grants_chain.grant(PARTAGEABLE, f"user:{SUB_B}", label="env")
    assert _audience(quad) == [f"user:{SUB_B}"]

    grants_chain.revoke(PARTAGEABLE, f"user:{SUB_B}", label="env")
    assert _audience(quad) == []                       # refuse, sans repli
