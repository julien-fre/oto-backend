"""Le drain d'alias suit la CHAÎNE jusqu'au bout — exercé en SQL réel.

Une personne rapprochée plusieurs fois laisse une chaîne dans `sub_aliases` :
`A → B → C → D`, une ligne par bascule. Un drain qui ne fait qu'un saut rend `B` à
qui présente `A` — et `B` a été supprimé par la bascule suivante. Ce n'est pas une
erreur propre : les deux portes servies appellent `upsert_user` juste après le
drain, donc l'identifiant mort est **RECRÉÉ**, et le compte fantôme qui en naît sert
du trafic sans org, sans coffre et sans une ligne de trace.

Mesuré en production le 2026-09-03 sur 23 alias : une chaîne de 3 maillons (bascules
du 28/07, 03/08, 13/08), une de 2, et un maillon intermédiaire déjà sans ligne
`users`. Le maillon supprimé le 13/08 avait été recréé le 16/08 par ce chemin, puis
avait servi 884 appels sous une identité morte.

**Pourquoi en SQL réel et pas sur doublure** : la correction EST une requête
récursive. Un faux curseur prouverait qu'on lit bien les colonnes qu'on s'est
données ; il ne prouverait ni que la récursion suit la chaîne, ni qu'elle s'arrête
sur un cycle, ni ce que le cas nominal coûte. Les quatre cas exigés se posent donc
sur une base éphémère, chacun séparément — retirer la garde de l'un rougit le sien
et lui seul.

Patron de base éphémère : `test_migrate_sub_group_grants_db.py::live`.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

# La requête que celle-ci REMPLACE — le point de comparaison du coût nominal. Elle
# n'est pas là pour la nostalgie : sans elle, « le cas nominal ne coûte rien de
# plus » n'a pas de référence, et se mesure contre une intuition.
_SQL_UN_SAUT = "SELECT new_sub FROM sub_aliases WHERE old_sub=%(sub)s"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_aliaschain_" + uuid.uuid4().hex[:8]
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


# ── outillage : construire une chaîne, lire la table BRUTE ───────────────────

def _chaine_par_merges(n: int) -> list[str]:
    """Une vraie chaîne, construite par `n` merges successifs — le chemin exact qui
    a produit celle de la production. Rend les maillons, du plus ancien au plus
    récent ; seul le dernier porte encore une ligne `users`."""
    from oto_mcp import db
    uniq = uuid.uuid4().hex[:8]
    maillons = [f"m{i}_{uniq}" for i in range(n + 1)]
    db.upsert_user(maillons[0])
    for ancien, suivant in zip(maillons, maillons[1:]):
        db.upsert_user(suivant)
        assert db.migrate_sub(ancien, suivant, operator_source="test") is True
    return maillons


def _alias_brut(old: str, new: str) -> None:
    """Poser un alias SANS passer par le merge — pour fabriquer ce que le merge ne
    fabrique pas (un cycle, une chaîne absurde). La table n'a pas de contrainte qui
    l'en empêche : `old_sub` est clé primaire, `new_sub` n'est contraint par rien."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sub_aliases (old_sub, new_sub) VALUES (%s,%s) "
            "ON CONFLICT (old_sub) DO UPDATE SET new_sub=EXCLUDED.new_sub", (old, new))


def _compte_existe(sub: str) -> bool:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE sub=%s", (sub,)).fetchone() is not None


def _supprimer_le_compte(sub: str) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE sub=%s", (sub,))


def _porte(sub_du_jeton: str) -> str:
    """La séquence EXACTE des deux portes servies (`api/base._authenticate`,
    `auth/hooks.current_user_sub_from_token`) : canonicaliser, puis `upsert_user`.

    C'est cette séquence, et pas `resolve_sub` seul, qui fabriquait le fantôme :
    l'upsert n'est gaté par rien, donc tout sub que le drain rend et qui n'existe
    plus est RECRÉÉ à cet instant."""
    from oto_mcp import db
    sub = db.resolve_sub(sub_du_jeton)
    db.upsert_user(sub)
    return sub


# ── 1. la chaîne se suit jusqu'au bout ───────────────────────────────────────

def test_chaine_de_longueur_2_aboutit_au_compte_actuel(live):
    """Deux rapprochements d'affilée. Un seul saut rendrait le maillon du milieu —
    supprimé par le second merge."""
    from oto_mcp import db
    a, b, c = _chaine_par_merges(2)
    assert not _compte_existe(b), "décor invalide : le maillon du milieu survit"

    assert db.resolve_sub(a) == c
    assert db.resolve_sub(b) == c


def test_chaine_de_longueur_3_aboutit_au_compte_actuel(live):
    """Le cas de la production : trois rapprochements (28/07, 03/08, 13/08). Une
    récursion qui ne descendrait que d'un cran de plus s'arrêterait ici."""
    from oto_mcp import db
    a, b, c, d = _chaine_par_merges(3)
    assert not _compte_existe(b) and not _compte_existe(c), "décor invalide"

    assert db.resolve_sub(a) == d
    assert db.resolve_sub(b) == d
    assert db.resolve_sub(c) == d


def test_le_maillon_du_milieu_n_est_JAMAIS_recree(live):
    """La conséquence servie, mesurée sur la séquence des portes : après le passage
    d'un jeton portant le premier identifiant, aucun maillon intermédiaire ne doit
    avoir repris vie. C'est exactement ce qui s'est produit en production le 16/08."""
    a, b, c, d = _chaine_par_merges(3)

    assert _porte(a) == d
    assert not _compte_existe(b), "le maillon du milieu a été RECRÉÉ"
    assert not _compte_existe(c), "le maillon du milieu a été RECRÉÉ"


# ── 2. une chaîne qui n'aboutit nulle part se REFUSE ─────────────────────────

def test_chaine_qui_aboutit_a_un_compte_disparu_leve(live):
    """Le compte final a été supprimé après coup (suppression de compte, purge). Le
    drain n'a alors personne à qui servir la requête : il refuse, il ne devine pas."""
    from oto_mcp import db
    from oto_mcp.db.sub_aliases import AliasNonResolvable
    a, b, c = _chaine_par_merges(2)
    _supprimer_le_compte(c)

    with pytest.raises(AliasNonResolvable) as e:
        db.resolve_sub(a)
    assert e.value.motif == "compte_disparu"
    assert a in str(e.value)


def test_un_compte_disparu_n_est_pas_ressuscite_par_la_porte(live):
    """Le refus doit tenir jusqu'au bout de la séquence servie : si `resolve_sub`
    rendait quoi que ce soit, `upsert_user` recréerait la ligne dans la foulée."""
    from oto_mcp.db.sub_aliases import AliasNonResolvable
    a, b, c = _chaine_par_merges(2)
    _supprimer_le_compte(c)

    with pytest.raises(AliasNonResolvable):
        _porte(a)
    assert not _compte_existe(c), "le compte final a été RECRÉÉ par la porte"
    assert not _compte_existe(b), "le maillon du milieu a été RECRÉÉ par la porte"


# ── 3. un cycle ne fait pas tourner le chemin d'entrée ───────────────────────

def test_un_cycle_leve_au_lieu_de_tourner(live):
    """`A → B` puis `B → A`. Rien dans le schéma ne l'interdit — et le défaut qu'on
    corrige ici (un identifiant mort qu'on RECRÉE) est justement ce qui rend un
    rapprochement en sens inverse atteignable. Sur le chemin d'entrée de chaque
    requête, une résolution qui boucle gèlerait le service."""
    from oto_mcp import db
    from oto_mcp.db.sub_aliases import AliasNonResolvable
    uniq = uuid.uuid4().hex[:8]
    a, b = f"cyc_a_{uniq}", f"cyc_b_{uniq}"
    _alias_brut(a, b)
    _alias_brut(b, a)

    debut = time.monotonic()
    with pytest.raises(AliasNonResolvable) as e:
        db.resolve_sub(a)
    ecoule = time.monotonic() - debut
    assert e.value.motif == "cycle"
    # Le refus n'est utile que s'il arrive VITE : c'est le chemin d'entrée de chaque
    # requête. Une seconde est trois ordres de grandeur au-dessus du coût réel — la
    # borne attrape le tour en rond, pas la lenteur d'une machine chargée.
    assert ecoule < 1.0, f"la résolution a mis {ecoule:.2f}s"


def test_un_cycle_plus_long_leve_aussi(live):
    """Le cycle n'est pas forcément un aller-retour : `A → B → C → A`. La détection
    porte sur le chemin PARCOURU, pas sur le maillon précédent."""
    from oto_mcp import db
    from oto_mcp.db.sub_aliases import AliasNonResolvable
    uniq = uuid.uuid4().hex[:8]
    a, b, c = f"tri_a_{uniq}", f"tri_b_{uniq}", f"tri_c_{uniq}"
    _alias_brut(a, b)
    _alias_brut(b, c)
    _alias_brut(c, a)

    with pytest.raises(AliasNonResolvable) as e:
        db.resolve_sub(a)
    assert e.value.motif == "cycle"


# ── 4. la borne de profondeur : le SECOND frein, prouvé seul ─────────────────

def test_une_chaine_absurdement_longue_est_refusee(live):
    """Frein indépendant de la détection de cycle : une chaîne ACYCLIQUE plus longue
    que la borne se refuse au lieu de se dérouler. Ce cas ne déclenche pas la
    détection de cycle — c'est ce qui en fait une preuve séparée."""
    from oto_mcp import db
    from oto_mcp.db import sub_aliases
    from oto_mcp.db.sub_aliases import AliasNonResolvable
    uniq = uuid.uuid4().hex[:8]
    n = sub_aliases.MAX_SAUTS + 4
    maillons = [f"long{i}_{uniq}" for i in range(n + 1)]
    for ancien, suivant in zip(maillons, maillons[1:]):
        _alias_brut(ancien, suivant)
    db.upsert_user(maillons[-1])

    with pytest.raises(AliasNonResolvable) as e:
        db.resolve_sub(maillons[0])
    assert e.value.motif == "chaine_trop_longue"


def test_une_chaine_pile_a_la_borne_se_resout_encore(live):
    """La borne refuse ce qui la DÉPASSE, pas ce qui l'atteint. Sans ce cas, on ne
    saurait pas si le refus ci-dessus vient de la longueur ou d'un décalage d'un
    cran dans la récursion."""
    from oto_mcp import db
    from oto_mcp.db import sub_aliases
    uniq = uuid.uuid4().hex[:8]
    n = sub_aliases.MAX_SAUTS
    maillons = [f"borne{i}_{uniq}" for i in range(n + 1)]
    for ancien, suivant in zip(maillons, maillons[1:]):
        _alias_brut(ancien, suivant)
    db.upsert_user(maillons[-1])

    assert db.resolve_sub(maillons[0]) == maillons[-1]


# ── 5. le cas nominal ne coûte rien de plus — mesuré, pas affirmé ────────────

def _noeuds(plan: dict):
    yield plan
    for enfant in plan.get("Plans", []) or []:
        yield from _noeuds(enfant)


def _relations_visitees(sql: str, params: dict) -> dict:
    """Les relations RÉELLEMENT accédées par ce plan, et combien de fois.

    ⚠️ `Actual Loops` est ce qui compte, pas la présence du nœud : le test
    d'existence du compte apparaît dans le plan de la requête neuve même quand il
    n'est jamais exécuté. Lire l'arbre sans lire les boucles ferait dire à ce test
    l'inverse de la vérité."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        row = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()
    plan = list(row.values())[0][0]["Plan"]
    visites: dict[str, int] = {}
    for n in _noeuds(plan):
        rel, loops = n.get("Relation Name"), n.get("Actual Loops", 0)
        if rel and loops:
            visites[rel] = visites.get(rel, 0) + loops
    return visites


def test_le_cas_nominal_ne_touche_QUE_sub_aliases_et_une_seule_fois(live):
    """Un identifiant courant, sans alias — le cas de tout le trafic. Le plan de la
    requête neuve doit accéder aux MÊMES relations, le MÊME nombre de fois, que la
    requête d'un seul saut qu'elle remplace. En particulier : `users` n'est pas
    touchée, alors que la requête sait la joindre."""
    from oto_mcp.db import sub_aliases
    uniq = uuid.uuid4().hex[:8]
    sub = f"sans_alias_{uniq}"
    _chaine_par_merges(2)  # de quoi peupler la table : on ne mesure pas sur du vide

    avant = _relations_visitees(_SQL_UN_SAUT, {"sub": sub})
    apres = _relations_visitees(sub_aliases._CHAINE_SQL,
                                {"sub": sub, "max": sub_aliases.MAX_SAUTS})

    assert avant == {"sub_aliases": 1}, avant
    assert apres == avant, (
        f"le cas nominal touche désormais {apres} au lieu de {avant} — c'est un "
        "coût de plus en tête de CHAQUE requête servie")


def test_le_cas_nominal_ne_fait_qu_une_seule_requete(live, monkeypatch):
    """Le compagnon de la mesure de plan : une requête, pas deux. Un correctif qui
    irait vérifier l'existence du compte dans un SECOND aller-retour serait invisible
    au test ci-dessus, qui ne juge qu'un plan à la fois."""
    import contextlib

    from oto_mcp import db
    from oto_mcp.db import _conn as dbconn
    uniq = uuid.uuid4().hex[:8]
    sub = f"sans_alias_{uniq}"
    passages: list[str] = []

    class _Espion:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            passages.append(sql)
            return self._conn.execute(sql, params)

        def __getattr__(self, nom):
            return getattr(self._conn, nom)

    vrai = dbconn._connect

    @contextlib.contextmanager
    def _compte():
        with vrai() as conn:
            yield _Espion(conn)

    monkeypatch.setattr("oto_mcp.db.sub_aliases._connect", _compte)
    assert db.resolve_sub(sub) == sub
    assert len(passages) == 1, f"{len(passages)} requêtes sur le cas nominal"
