"""Lignes d'un nœud-tableau (lot ⑤) — curseur opaque, et la garde d'homonymie."""
import pytest

from oto_mcp.capabilities import node_rows as R
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=2)

TABLE = {"id": 5, "public_id": "nod_tbl", "parent_id": None, "kind": "tableau",
         "owner_type": "org", "owner_id": "2", "position": 0,
         "props": {"title": "vivier", "legacy_id": 12,
                   "child_schema": {"fields": [
                       {"key": "nom", "label": "Nom"},
                       {"key": "score", "label": "Score", "type": "number"}]}},
         "created_at": "2026-08-01", "updated_at": "2026-08-01"}
PAGE = {**TABLE, "kind": "page"}


class _Store:
    def __init__(self, ns_id=12, page=None):
        self.ns_id, self.page, self.vu = ns_id, page or {"rows": [], "next_cursor": None}, {}

    def _resolve(self, namespace):
        if self.ns_id is None:
            raise RuntimeError("NamespaceNotFound")
        return self.ns_id

    def cursor_rows(self, namespace, **kw):
        self.vu = dict(kw, namespace=namespace)
        return self.page


@pytest.fixture
def seams(monkeypatch):
    etat = {"fiche": TABLE, "store": _Store(), "total": 7}
    monkeypatch.setattr(R.db_node, "node_by_public_id", lambda pid: etat["fiche"])
    monkeypatch.setattr(R.ds, "make_store", lambda sub: etat["store"])
    monkeypatch.setattr(R.db_ds, "datastore_count_rows",
                        lambda ns, q=None, filters=None: etat["total"])
    return etat


# ── Le 404 couvre TROIS causes ─────────────────────────────────────────────────
def test_inexistant_interdit_et_PAS_UN_TABLEAU_rendent_le_MEME_refus(seams):
    refus = []
    seams["fiche"] = None
    with pytest.raises(AuthzDenied) as e1:
        R._compose(CTX, R.NodeRowsInput(node_id="x"))
    refus.append(e1.value)

    seams["fiche"] = PAGE                      # un nœud, mais une PAGE
    with pytest.raises(AuthzDenied) as e2:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    refus.append(e2.value)

    seams["fiche"], seams["store"] = TABLE, _Store(ns_id=None)   # interdit
    with pytest.raises(AuthzDenied) as e3:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    refus.append(e3.value)

    # Distinguer « c'est une page » renseignerait sur un contenu qu'on n'a pas le
    # droit de voir : la nature d'un nœud est déjà une information.
    assert len({(r.status, r.code, r.message) for r in refus}) == 1
    assert refus[0].status == 404


# ── La garde d'homonymie : le bug qui n'aurait produit AUCUNE erreur ────────────
def test_un_nom_qui_resout_AILLEURS_est_refuse(seams):
    seams["store"] = _Store(ns_id=999)         # le nom a résolu un AUTRE tableau
    with pytest.raises(AuthzDenied) as e:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    assert e.value.status == 404
    # Sans cette garde : les lignes d'un autre tableau, avec les bonnes colonnes,
    # sans erreur, et personne ne le voit.


def test_un_noeud_sans_cle_legacy_ne_declenche_pas_la_garde(seams):
    # Un nœud NATIF (créé par une surface, pas converti) n'a pas de `legacy_id` :
    # la garde ne doit pas le refuser au motif qu'elle ne peut pas le vérifier.
    seams["fiche"] = {**TABLE, "props": {**TABLE["props"], "legacy_id": None}}
    assert R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))["total"] == 7


# ── Le curseur, opaque et transporté tel quel ──────────────────────────────────
def test_le_curseur_est_repasse_TEL_QUEL_et_rendu_tel_quel(seams):
    seams["store"] = _Store(page={"rows": [], "next_cursor": "eyJvIjo1MH0"})
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", cursor="abc123"))
    assert seams["store"].vu["cursor"] == "abc123"     # jamais décodé ici
    assert out["nextCursor"] == "eyJvIjo1MH0"


def test_fin_de_liste_rend_None_pas_une_chaine_vide(seams):
    assert R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))["nextCursor"] is None


def test_la_limite_est_ECRETEE_jamais_refusee(seams):
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", limit=99999))
    assert seams["store"].vu["limit"] == R._LIMITE_MAX
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", limit=-3))
    assert seams["store"].vu["limit"] == 1


# ── Colonnes, total, cellules ──────────────────────────────────────────────────
def test_les_colonnes_voyagent_avec_CHAQUE_page_dans_l_ordre_du_schema(seams):
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    assert [c["key"] for c in out["columns"]] == ["nom", "score"]
    assert out["columns"][0]["title"] == "Nom"
    # Le seul indice d'affichage qu'un front ne peut pas deviner.
    assert out["columns"][1]["numeric"] is True
    assert "numeric" not in out["columns"][0]


def test_le_total_est_FILTRE_pas_le_volume_du_tableau(seams, monkeypatch):
    vus = {}
    monkeypatch.setattr(R.db_ds, "datastore_count_rows",
                        lambda ns, q=None, filters=None: vus.update(q=q, f=filters) or 3)
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", q="clos",
                                          filter=["statut:clos"]))
    assert out["total"] == 3
    # Le compte reçoit les MÊMES filtres que la page : sinon le pied annonce
    # « 3 sur 12 000 » alors que l'écran en montre 3 sur 3.
    assert vus["q"] == "clos" and vus["f"] == [{"field": "statut", "op": "eq", "value": "clos"}]


def test_les_cellules_sont_des_CHAINES_deja_rendues(seams):
    seams["store"] = _Store(page={"rows": [
        {"_id": "r1", "nom": "Acme", "score": 42, "extra": {"a": 1}}], "next_cursor": None})
    ligne = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))["items"][0]
    assert ligne["id"] == "r1"
    assert ligne["cells"] == {"nom": "Acme", "score": "42"}   # bornées au schéma
    assert all(isinstance(v, str) for v in ligne["cells"].values())


def test_une_table_LIBRE_rend_tous_ses_champs_utilisateur(seams):
    # 29 des 83 tableaux de production ne déclarent aucun schéma : borner aux colonnes
    # déclarées rendrait leur écran vide.
    seams["fiche"] = {**TABLE, "props": {"title": "vivier", "legacy_id": 12}}
    seams["store"] = _Store(page={"rows": [
        {"_id": "r1", "_created_at": "2026-01-01", "libre": "oui"}], "next_cursor": None})
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    assert out["columns"] == []
    assert out["items"][0]["cells"] == {"libre": "oui"}      # sans les colonnes système


# ── Les filtres ────────────────────────────────────────────────────────────────
def test_un_filtre_malforme_est_IGNORE_pas_une_erreur(seams):
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl",
                                    filter=["statut:clos", "n_importe_quoi"]))
    assert seams["store"].vu["filters"] == [
        {"field": "statut", "op": "eq", "value": "clos"}]


def test_un_filtre_unique_arrive_en_CHAINE_et_marche_quand_meme(seams):
    # Une query string à un seul `?filter=` n'arrive pas en liste.
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", filter="statut:clos"))
    assert seams["store"].vu["filters"] == [
        {"field": "statut", "op": "eq", "value": "clos"}]


def test_le_tri_passe_la_CLE_de_colonne_au_store(seams):
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", sort="score", direction="asc"))
    assert seams["store"].vu["order_by"] == "score"
    assert seams["store"].vu["order_dir"] == "asc"


def test_le_corps_valide_l_Output_declare(seams):
    R.RowsPage(**R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl")))
