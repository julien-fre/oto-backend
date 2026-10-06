"""La garde des migrations d'une instance cible (oto-backend#1163).

Le défaut : une cible servait le code d'un tag dont la tête Alembic était 0037, sur une
base restée en 0031 — la chaîne porte → `deployer.sh` → bleu/vert montait le code sans
jouer les migrations, et rien ne le disait. Désormais, `deployer.sh` REFUSE de démarrer
la couleur neuve tant que la base du rôle n'est pas à la tête des révisions DU TAG, en
nommant l'écart et la commande qui migre ; il ne migre jamais lui-même.

Deux étages :
- le script (`deploy/cible/migrations_a_jour.py`) : tête lue dans un VRAI registre Alembic
  (jetable), état de la base dicté — à jour, en retard, plusieurs têtes, base illisible,
  base antérieure à la référence du registre (squash, docs/migrations-versionnees.md
  §5.4)… ; plus, sur un PostgreSQL réel quand il y en a un, la lecture de la base
  elle-même, contre le registre du dépôt ;
- la chaîne (`_banc_cible.py`, root simulé) : le refus arrive après l'installation dans la
  couleur inactive et AVANT son démarrage — rien ne démarre, rien ne bascule.
"""
from __future__ import annotations

import importlib.util
import textwrap

import pytest

from _banc_cible import DEPOT, Banc

_spec = importlib.util.spec_from_file_location(
    "migrations_a_jour", DEPOT / "deploy" / "cible" / "migrations_a_jour.py")
garde = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(garde)

NEUVE, VERSIONNEE, SANS_VERSION = (garde.EtatRegistre.NEUVE, garde.EtatRegistre.VERSIONNEE,
                                   garde.EtatRegistre.SANS_VERSION)


# --- le script -------------------------------------------------------------------------

def _registre(tmp_path, revisions: list[tuple[str, str | None]]):
    """Un registre Alembic jetable : (révision, précédente) par fichier."""
    registre = tmp_path / "migrations"
    (registre / "versions").mkdir(parents=True)
    (registre / "script.py.mako").write_text("")
    (registre / "env.py").write_text("")
    for rev, prec in revisions:
        (registre / "versions" / f"{rev}.py").write_text(textwrap.dedent(f'''\
            """{rev}"""
            revision = {rev!r}
            down_revision = {prec!r}
            branch_labels = None
            depends_on = None
            '''))
    return registre


@pytest.fixture
def lineaire(tmp_path):
    return _registre(tmp_path, [("0031", None), ("0035", "0031"), ("0037", "0035")])


def _main(registre, monkeypatch, etat, versions=(), dsn="postgresql://exemple.invalid/base"):
    monkeypatch.setenv("DATABASE_URL", dsn)
    lus = []

    def lire(d):
        lus.append(d)
        if isinstance(etat, Exception):
            raise etat
        return etat, list(versions)
    return garde.main(lire=lire, registre=registre), lus


def test_a_jour_passe(lineaire, monkeypatch, capsys):
    code, lus = _main(lineaire, monkeypatch, VERSIONNEE, ["0037"])
    assert code == 0 and lus == ["postgresql://exemple.invalid/base"]
    assert "base à la tête du tag (0037)" in capsys.readouterr().out


def test_en_retard_refuse_et_nomme_0031_et_0037(lineaire, monkeypatch, capsys):
    code, _ = _main(lineaire, monkeypatch, VERSIONNEE, ["0031"])
    assert code == garde.EN_RETARD == 3
    err = capsys.readouterr().err
    assert "la base est en 0031, le tag attend 0037" in err


def test_plusieurs_tetes_refuse_sans_lire_la_base(tmp_path, monkeypatch, capsys):
    registre = _registre(tmp_path, [("0031", None), ("0036", "0031"), ("0037", "0031")])
    code, lus = _main(registre, monkeypatch, VERSIONNEE, ["0037"])
    assert code == 1 and lus == []
    assert "2 têtes (0036, 0037)" in capsys.readouterr().err


def test_base_illisible_refuse_sans_rien_dire_du_dsn(lineaire, monkeypatch, capsys):
    import psycopg
    panne = psycopg.OperationalError('connection to server at "hote-secret.invalid" failed')

    def connecter(*a, **k):
        raise panne
    monkeypatch.setattr(garde.psycopg, "connect", connecter)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:motdepasse@hote-secret.invalid/b")
    code = garde.main(registre=lineaire)
    err = capsys.readouterr().err
    assert code == 1 and "base du rôle illisible (OperationalError)" in err
    assert "hote-secret" not in err and "motdepasse" not in err


def test_sans_database_url_refuse(lineaire, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert garde.main(lire=lambda d: pytest.fail("lu sans DSN"), registre=lineaire) == 1
    assert "DATABASE_URL absente" in capsys.readouterr().err


@pytest.mark.parametrize("etat, versions, code, motif", [
    (NEUVE, [], 0, "base neuve"),
    (SANS_VERSION, [], 1, "pas d'`alembic_version`"),
    (VERSIONNEE, [], 1, "porte 0 révision(s)"),
    (VERSIONNEE, ["0031", "0037"], 1, "porte 2 révision(s)"),
    (VERSIONNEE, ["0042"], 1, "0042, révision inconnue du registre du tag"),
    (VERSIONNEE, ["0035"], 3, "la base est en 0035, le tag attend 0037"),
])
def test_chaque_etat_a_son_verdict(lineaire, monkeypatch, capsys, etat, versions, code, motif):
    assert _main(lineaire, monkeypatch, etat, versions)[0] == code
    sortie = capsys.readouterr()
    assert motif in sortie.out + sortie.err


class _BaseDictee:
    """Une connexion qui répond comme une base versionnée à `versions` — sans PostgreSQL."""

    def __init__(self, versions):
        self.versions, self.read_only = versions, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *params):
        if "pg_class" in sql:
            lignes = [{"relname": "alembic_version"}, {"relname": "orgs"}]
        elif "FROM alembic_version" in sql:
            lignes = [{"version_num": v} for v in self.versions]
        else:
            lignes = []
        return type("Curseur", (), {"fetchall": lambda _: lignes})()


def test_une_base_anterieure_a_la_reference_refuse_en_nommant_le_tag(lineaire, monkeypatch,
                                                                     capsys):
    monkeypatch.setattr(garde.psycopg, "connect",
                        lambda *a, **k: _BaseDictee(["0039_feed_synced_at_retiree"]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://exemple.invalid/base")
    assert garde.main(registre=lineaire) == garde.ILLISIBLE == 1
    err = capsys.readouterr().err
    assert ("révision 0039_feed_synced_at_retiree antérieure à la référence "
            "0041_recherche_valeurs_servies (squash du 06/10/2026) : monter d'abord cette "
            "base avec un tag antérieur au squash (v1.441.0)") in err


def test_le_registre_du_depot_a_une_seule_tete():
    """Le script lit le registre de l'arbre où il tourne : celui du dépôt doit conclure."""
    tete, connues = garde.tete_du_tag(garde._REGISTRE)
    assert tete in connues


def test_le_lanceur_accepte_le_script():
    spec = importlib.util.spec_from_file_location("lanceur_secrets",
                                                  DEPOT / "deploy" / "lanceur_secrets.py")
    lanceur = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lanceur)
    commande = lanceur.cible(["--script", "deploy/cible/migrations_a_jour.py"])
    assert commande[-1].endswith("/deploy/cible/migrations_a_jour.py")


def test_lecture_reelle_de_la_base(pg_dsn):
    """Sur un PostgreSQL réel : neuve, versionnée, sans version — lues en lecture seule."""
    import os

    from _base_jetable import base_jetable
    with base_jetable(pg_dsn) as ouvrir:
        dsn = os.environ["DATABASE_URL"]
        assert garde.lire_base(dsn) == (NEUVE, [])
        with ouvrir() as c:
            c.execute("CREATE TABLE t (x int)")
        assert garde.lire_base(dsn) == (SANS_VERSION, [])
        with ouvrir() as c:
            c.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            c.execute("INSERT INTO alembic_version VALUES ('0031')")
        assert garde.lire_base(dsn) == (VERSIONNEE, ["0031"])
        absente = dsn.rsplit("/", 1)[0] + "/base_absente_1163"
        with pytest.raises(garde.Refus, match="illisible"):
            garde.lire_base(absente)


# La tête se lit dans le registre : figée ici, chaque nouvelle révision cassait ce banc.
_TETE_DU_REGISTRE = garde.tete_du_tag(garde._REGISTRE)[0]


@pytest.mark.parametrize("version, code, motif", [
    (_TETE_DU_REGISTRE, 0, "base à la tête du tag"),
    ("0041_recherche_valeurs_servies", 3,
     "la base est en 0041_recherche_valeurs_servies, le tag attend"),
    ("0039_feed_synced_at_retiree", 1,
     "révision 0039_feed_synced_at_retiree antérieure à la référence "
     "0041_recherche_valeurs_servies (squash du 06/10/2026)"),
])
def test_contre_le_registre_du_depot(pg_dsn, monkeypatch, capsys, version, code, motif):
    """Sur un PostgreSQL réel et le registre du dépôt : une base à la tête est à jour, une
    base à la référence est en retard (`migrer upgrade head` la monte), une base à une
    révision retirée est refusée en le nommant."""
    from _base_jetable import base_jetable
    with base_jetable(pg_dsn) as ouvrir:
        with ouvrir() as c:
            c.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            c.execute("INSERT INTO alembic_version VALUES (%s)", (version,))
        assert garde.main() == code
        sortie = capsys.readouterr()
        assert motif in sortie.out + sortie.err


# --- la chaîne -------------------------------------------------------------------------

@pytest.fixture
def banc(tmp_path):
    return Banc(tmp_path)


def _garde_appelee(cmds: list[str]) -> list[str]:
    return [c for c in cmds if c.startswith("systemd-run ")
            and "--script deploy/cible/migrations_a_jour.py" in c]


def test_chaine_a_jour_monte(banc):
    fini = banc.lancer("deployer.sh", "deployer", "prod", "v1.2.3")
    assert fini.returncode == 0, fini.stdout + fini.stderr
    cmds = banc.commandes()
    appel, = _garde_appelee(cmds)
    # sous le lanceur DE L'ARBRE où le tag vient d'être installé, avec l'environnement du rôle
    assert "-p WorkingDirectory=/opt/exemple/prod-green " in appel
    assert "-p EnvironmentFile=/etc/exemple/prod/app.env" in appel
    assert "-p LoadCredential=scw:/etc/exemple/scw.key" in appel
    assert "/opt/exemple/prod-green/deploy/lanceur_secrets.py --script" in appel
    # après l'installation, avant le démarrage
    assert cmds.index("uv sync --frozen --quiet") < cmds.index(appel) \
        < cmds.index("systemctl start exemple-prod@green")


def test_chaine_en_retard_refuse_avant_demarrage_et_nomme_la_commande(banc):
    fini = banc.lancer("deployer.sh", "deployer", "prod", "v1.2.3", BANC_MIGRATIONS_CODE="3",
                       BANC_MIGRATIONS_DIT="migrations : la base est en 0031, le tag attend 0037")
    assert fini.returncode == 1
    sortie = (fini.stdout + fini.stderr).replace(str(banc.racine), "")
    assert "la base est en 0031, le tag attend 0037" in sortie
    assert ("systemd-run --pipe --wait --quiet --collect -p WorkingDirectory=/opt/exemple/prod-green "
            "-p EnvironmentFile=/etc/exemple/prod/app.env -p EnvironmentFile=/etc/exemple/prod/lanceur.env "
            "-p LoadCredential=scw:/etc/exemple/scw.key /opt/exemple/prod-green/.venv/bin/python "
            "/opt/exemple/prod-green/deploy/lanceur_secrets.py migrer upgrade head") in sortie
    assert "rien n'a basculé" in sortie
    _rien_n_a_demarre(banc)


@pytest.mark.parametrize("code", ["1", "2", "127"])
def test_chaine_etat_illisible_refuse_sans_proposer_de_migrer(banc, code):
    fini = banc.lancer("deployer.sh", "deployer", "prod", "v1.2.3", BANC_MIGRATIONS_CODE=code,
                       BANC_MIGRATIONS_DIT="migrations : REFUS — le registre du tag a 2 têtes")
    assert fini.returncode == 1
    assert "le registre du tag a 2 têtes" in fini.stderr
    assert "ne se conclut pas" in fini.stderr and "migrer current" in fini.stderr
    assert "upgrade head" not in fini.stderr
    _rien_n_a_demarre(banc)


def _rien_n_a_demarre(banc):
    cmds = banc.commandes()
    assert _garde_appelee(cmds)
    assert not [c for c in cmds if c.startswith("systemctl start ")]
    assert not [c for c in cmds if c.startswith("caddy ")]
    assert not [c for c in cmds if "lanceur_secrets.py migrer" in c]   # jamais migré
    assert banc.lire("/etc/exemple/prod/active") == "blue\n"
    assert not (banc.racine / "etc/systemd/system/exemple-prod-maintenance.service").exists()


def test_le_retour_arriere_ne_consulte_pas_les_migrations(banc):
    assert banc.lancer("deployer.sh", "deployer", "prod", "v1.2.3").returncode == 0
    banc.trace.write_text("")
    fini = banc.lancer("deployer.sh", "retour", "prod", "v1.2.3", BANC_MIGRATIONS_CODE="3")
    assert fini.returncode == 0, fini.stderr
    assert not _garde_appelee(banc.commandes())
