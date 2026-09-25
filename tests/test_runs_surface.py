"""Ce que l'écran « Runs » demande à la file et aux passages, et que ni l'une ni
les autres n'écrivaient.

Deux manques, tous deux découverts en dessinant l'écran plutôt qu'en lisant le
code — et tous deux du même genre : une donnée qui n'est vraie qu'à un instant
et que personne ne notait.

1. **Le DÉNOMINATEUR d'un passage — RETIRÉ le 13/09/2026.** `rows_at_launch` comptait
   à l'armement les lignes du `row_filter`, et rien d'autre : ni le périmètre déclaré
   du tableau (`lifecycle.claimable`), ni les baux, ni les lignes sorties de la file.
   Une campagne dont le filtre contredisait ce périmètre annonçait douze lignes pendant
   que chacune de ses réservations rendait `null`. Décision : la plateforme ne compte
   pas à la place de l'agent. Le compte n'est plus calculé, ni écrit, ni servi ; la
   colonne reste en base. Les bancs ci-dessous gardent ce RETRAIT.
2. **D'où vient un travail.** La file mélange trois origines et ne les
   distinguait qu'implicitement. Trier côté client ne marche PAS : la file est
   paginée sur `id DESC` et un passage de 2 000 lignes remplit la première page à
   lui seul, donc « programmé » rendrait VIDE sur une org qui joue un digest
   chaque matin. Un écran qui ment sur une absence est pire que pas d'écran.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_fleets as RF
from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.db import runner_jobs as JOBS


@pytest.fixture(autouse=True)
def _cle_de_modele_non_exigee(monkeypatch):
    """Ce fichier ne parle pas de la garde de clé de modèle — elle a son propre banc
    (`test_cle_de_modele_exigee.py`). Le réglage est lu ÉTEINT, comme sur toute
    plateforme qui ne l'a pas allumé : sans cette doublure, la lecture irait
    chercher la vraie base et chaque banc tomberait sur une raison qui n'est pas
    la sienne."""
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)


def _ctx(sub="alexis", org_id=2):
    return ResolvedCtx(sub=sub, org_id=org_id)


# ── le dénominateur : RETIRÉ ──────────────────────────────────────────────────

def test_le_denominateur_n_est_plus_servi_nulle_part():
    """Ni la ligne que rendent les verbes (le SELECT de `db/runner_fleets`), ni le
    schéma servi de la flotte, ni sa carte de liste. Un champ resté au schéma sans
    être rendu promettrait un compte que plus personne ne calcule."""
    from oto_mcp.db import runner_fleets as dbf
    assert "rows_at_launch" not in {c.strip() for c in dbf._COLS.split(",")}
    for modele in (RF.Fleet, RF.FleetCard, RF.FleetOut):
        assert "rows_at_launch" not in modele.model_fields, modele.__name__
    assert "rows_at_launch" not in RF.db.CHAMPS_MODIFIABLES


def test_armer_n_ecrit_plus_le_compte(monkeypatch):
    """L'UPDATE tel qu'il part vers la base : aucune écriture de la colonne — ni un
    compte, ni un NULL qui effacerait l'ancienne valeur."""
    import inspect
    from oto_mcp.db import runner_fleets as dbf
    vu = {}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            vu["sql"], vu["params"] = sql, params
            return self

        def fetchone(self):
            return {"id": 7, "status": "armed"}

    monkeypatch.setattr(dbf, "_connect", lambda: _Conn())
    assert dbf.armer(7, 2) == {"id": 7, "status": "armed"}
    assert "rows_at_launch" not in vu["sql"]
    assert vu["params"] == (7, 2)
    assert list(inspect.signature(dbf.armer).parameters) == ["fleet_id", "org_id"]


def test_lancer_ne_compte_rien_et_arme_avec_la_flotte_et_son_org_seules(monkeypatch):
    """`launch` n'ouvre plus le tableau visé. La doublure d'`armer` épingle sa signature
    À DESSEIN : un argument de compte réintroduit ferait lever l'appel au lieu de
    passer inaperçu."""
    def _jamais(*a, **k):
        raise AssertionError("l'armement a ouvert le tableau visé pour y compter")

    monkeypatch.setattr("oto_mcp.datastore.core.make_store", _jamais)
    monkeypatch.setattr(RF.db, "get_fleet", lambda fid, oid: {
        "id": fid, "status": "draft", "procedure": "p", "input": "x",
        "model": "claude-sonnet-5",
        "namespace": "prospects", "row_filter": {"statut": "a_traiter"}})
    recu = {}

    def _armer(fid, oid):
        recu["args"] = (fid, oid)
        return {"id": fid, "status": "armed", "max_rows": None, "max_tokens_per_row": None}

    monkeypatch.setattr(RF.db, "armer", _armer)
    monkeypatch.setattr("oto_mcp.roles.is_org_admin", lambda sub, org: True)
    monkeypatch.setattr(RF, "_run_courant", lambda: None)
    monkeypatch.setattr(RF.db, "runner_arme", lambda org: {
        "armed": True, "workers": 1, "last_seen": None, "families": ["anthropic"]})

    out = RF._fleets(_ctx(), RF.FleetInput(op="launch", fleet_id=7))
    assert recu["args"] == (7, 2)
    assert out["fleet"]["status"] == "armed"


# ── l'origine d'un travail ────────────────────────────────────────────────────

def test_les_trois_origines_partitionnent_la_file():
    """Tout travail tombe dans exactement une origine — sinon un onglet en perd."""
    assert set(JOBS._SOURCES) == {"batch", "scheduled", "manual"}
    assert JOBS._SOURCES["batch"] == "fleet_id IS NOT NULL"
    for s in ("scheduled", "manual"):
        assert "fleet_id IS NULL" in JOBS._SOURCES[s]
    assert "IS NOT NULL" in JOBS._SOURCES["scheduled"]
    assert "IS NULL" in JOBS._SOURCES["manual"].split("payload->>'trigger_id'")[1]


@pytest.mark.parametrize("source", ["batch", "scheduled", "manual"])
def test_le_filtre_dorigine_entre_dans_le_WHERE_commun(source):
    ou, params = JOBS._filtre_de_file(196, None, source)
    assert JOBS._SOURCES[source] in ou
    assert params == [196]


def test_une_origine_inconnue_est_REFUSEE_et_ne_rend_pas_toute_la_file():
    """Un filtre inconnu doit lever, pas se taire : ignorer silencieusement une
    valeur inattendue rendrait la file ENTIÈRE sous une étiquette qui promet un
    sous-ensemble."""
    with pytest.raises(ValueError):
        JOBS._filtre_de_file(196, None, "batches")


def test_la_page_et_son_total_partagent_le_MEME_filtre():
    """Servir un filtre à la page sans l'appliquer au compte redonne un total qui
    décrit une autre population que les lignes affichées."""
    import inspect
    for fn in (JOBS.list_jobs, JOBS.count_jobs):
        p = inspect.signature(fn).parameters
        assert "source" in p and "fleet_id" in p, fn.__name__


def test_le_filtre_dorigine_se_compose_avec_le_statut_et_la_flotte():
    ou, params = JOBS._filtre_de_file(196, "failed", "scheduled", fleet_id=None)
    assert "status = %s" in ou and JOBS._SOURCES["scheduled"] in ou
    assert params == [196, "failed"]
    ou, params = JOBS._filtre_de_file(196, None, None, fleet_id=12)
    assert "fleet_id = %s" in ou and params == [196, 12]


def test_la_source_est_declaree_sur_le_contrat_servi():
    champ = RJ.JobsInput.model_fields["source"]
    assert champ.default is None
    assert "scheduled" in str(champ.annotation)


def test_le_filtre_par_declencheur_lit_le_payload_et_entre_dans_le_total():
    """`trigger_id` n'a pas de colonne (le tick le pose dans le payload) ;
    demandé par Alexis au même titre que `fleet_id` : un historique trié côté
    client donne un total qui ne sert pas de dénominateur."""
    ou, params = JOBS._filtre_de_file(196, None, None, fleet_id=None, trigger_id=14)
    assert "payload->>'trigger_id'" in ou and params == [196, "14"]
    import inspect
    for fn in (JOBS.list_jobs, JOBS.count_jobs):
        assert "trigger_id" in inspect.signature(fn).parameters, fn.__name__
    assert "trigger_id" in RJ.JobsInput.model_fields


def test_le_filtre_par_declencheur_ne_CASTE_pas_le_payload():
    """⚠️ Le filtre a d'abord été écrit `(payload->>'trigger_id')::bigint = %s`.

    `payload` est un JSON libre : il suffit d'UNE ligne de l'org dont `trigger_id` n'est
    pas un nombre pour que le cast fasse échouer la requête ENTIÈRE — pas seulement
    cette ligne-là. Le filtre deviendrait une panne, sur des données qu'aucun de nos
    écrivains ne produit aujourd'hui mais que rien n'empêche d'exister : un payload est
    précisément l'endroit où l'on met ce qu'on n'a pas modélisé.

    La forme sûre vivait déjà deux fonctions plus haut dans le même fichier
    (`perimer_travaux_du_declencheur`, `comptage_perime`) : même clé, même lecture. Ce
    banc tient les TROIS d'accord, parce que c'est la divergence qui a produit le
    défaut — pas l'ignorance de la bonne forme."""
    import inspect
    ou, params = JOBS._filtre_de_file(196, None, None, fleet_id=None, trigger_id=14)
    assert "::bigint" not in ou, (
        "un cast sur une clé de payload libre : une seule ligne non numérique dans "
        "l'org fait tomber la requête entière")
    assert params == [196, "14"], "la comparaison est textuelle des deux côtés"
    # Et les trois lectures de cette clé restent d'accord.
    src = inspect.getsource(JOBS)
    assert src.count("payload->>'trigger_id')::bigint") == 0
    assert src.count("payload->>'trigger_id' = %s") >= 3
