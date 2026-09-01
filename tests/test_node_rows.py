"""Lignes d'un nœud-tableau (lot ⑤) — curseur opaque, et la garde d'homonymie.

⚠️ **Le double de store RÉSOUT les noms plats, comme le vrai.** `core.cursor_rows` et
`core.count_rows` passent tous deux leurs clauses par `_resolve_filters` avant de bâtir
leur SQL — un double qui ne le ferait pas rendrait vert un compte pris sur des noms non
résolus, c'est-à-dire exactement le défaut #621. Le double appelle donc la VRAIE
fonction de résolution, et les tests comparent ce que les deux chemins ont réellement
poussé en SQL.
"""
import pytest

from oto_mcp.capabilities import node_rows as R
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.datastore.columns import _resolve_filters
from oto_mcp.datastore.errors import InvalidCursor
from oto_mcp.db import datastore as db_datastore

CTX = ResolvedCtx(sub="u1", org_id=2)

TABLE = {"id": 5, "public_id": "nod_tbl", "parent_id": None, "kind": "tableau",
         "owner_type": "org", "owner_id": "2", "position": 0,
         "props": {"title": "vivier", "legacy_id": 12,
                   "child_schema": {"fields": [
                       {"key": "nom", "label": "Nom"},
                       {"key": "score", "label": "Score", "type": "number"}]}},
         "created_at": "2026-08-01", "updated_at": "2026-08-01"}
PAGE = {**TABLE, "kind": "page"}

# Une colonne-tableau en DOUBLE SERVICE : `contact1_nom` est un nom servi en lecture,
# `contacts[0].nom` le chemin réel (oto#22 §6). C'est le seul cas où compter avant de
# résoudre se voit — et il existe en production.
SCHEMA_ALIAS = {"fields": [{"key": "contacts", "type": "list",
                            "flat_alias": "contact{n}_{attr}",
                            "fields": [{"key": "nom"}]}]}


class _Store:
    def __init__(self, ns_id=12, page=None, schema=None, leve=None, total=7):
        self.ns_id, self.page, self.vu = ns_id, page or {"rows": [], "next_cursor": None}, {}
        self.schema, self.leve, self.total = schema, leve, total
        # Ce que chaque chemin a réellement poussé en SQL — la seule comparaison qui
        # dise si le pied du tableau décrit la page servie.
        self.filtres_page = self.filtres_compte = None

    def _resolve(self, namespace):
        if self.ns_id is None:
            raise RuntimeError("NamespaceNotFound")
        return self.ns_id

    def _schema_of(self, ns_id):
        return self.schema

    def cursor_rows(self, namespace, **kw):
        self.vu = dict(kw, namespace=namespace)
        if self.leve is not None:
            raise self.leve
        self.filtres_page = _resolve_filters(self.schema, kw.get("filters"))
        return self.page

    def count_rows(self, namespace, *, filter=None, q=None, filters=None):
        # `core.count_rows` : « le compte doit décrire le MÊME jeu que la page :
        # mêmes noms résolus ».
        self.filtres_compte = _resolve_filters(self.schema, filters)
        return self.total


def _compte_brut(etat):
    """Le compte pris DIRECTEMENT sur la table : il ne résout rien, et c'est exact —
    `db.datastore_count_rows` bâtit son `WHERE` sur les noms qu'on lui donne."""
    def _f(ns, q=None, filters=None):
        etat["store"].filtres_compte = list(filters or [])
        return etat["store"].total
    return _f


@pytest.fixture
def seams(monkeypatch):
    etat = {"fiche": TABLE, "store": _Store(), "total": 7}
    monkeypatch.setattr(R.db_node, "node_by_public_id", lambda pid: etat["fiche"])
    monkeypatch.setattr(R.ds, "make_store", lambda sub: etat["store"])
    # ⚠️ Posé sur le MODULE de la couche db, pas sur un alias de `node_rows` : le
    # chemin corrigé ne l'appelle plus du tout, et un test qui ne vaudrait que tant
    # que l'appel existe ne peut pas être vu rouge PUIS vert sans être réécrit.
    monkeypatch.setattr(db_datastore, "datastore_count_rows", _compte_brut(etat))
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


def test_un_noeud_sans_cle_legacy_prend_le_chemin_NATIF(seams, monkeypatch):
    """L'absence de `legacy_id` n'est pas un cas limite de la garde : c'est un AUTRE
    chemin de lecture (2026-09-01).

    Ce test disait l'inverse jusqu'ici — « la garde ne doit pas le refuser au motif
    qu'elle ne peut pas le vérifier » — et il avait raison tant qu'un seul chemin
    existait. Depuis que la nouvelle surface écrit ses propres tableaux, un tableau
    né ici n'a AUCUN namespace à résoudre : ses lignes sont ses enfants. Le faire
    passer par le store chercherait un nom qui n'y existe pas et refuserait la
    lecture d'un tableau parfaitement lisible.
    """
    seams["fiche"] = {**TABLE, "props": {**TABLE["props"], "legacy_id": None}}
    vu = {}
    monkeypatch.setattr(R.db_node_tables, "list_rows",
                        lambda tid, **kw: (vu.setdefault("tid", tid), ([], None))[1])
    monkeypatch.setattr(R.db_node_tables, "count_rows", lambda tid: 3)
    monkeypatch.setattr(R.ds, "make_store",
                        lambda sub: pytest.fail("le store a été consulté pour un "
                                                "tableau natif"))

    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))
    assert out["total"] == 3
    assert vu["tid"] == TABLE["id"]


# ── Le curseur, opaque et transporté tel quel ──────────────────────────────────
def test_le_curseur_est_repasse_TEL_QUEL_et_rendu_tel_quel(seams):
    seams["store"] = _Store(page={"rows": [], "next_cursor": "eyJvIjo1MH0"})
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", cursor="abc123"))
    assert seams["store"].vu["cursor"] == "abc123"     # jamais décodé ici
    assert out["nextCursor"] == "eyJvIjo1MH0"


def test_fin_de_liste_rend_None_pas_une_chaine_vide(seams):
    assert R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl"))["nextCursor"] is None


def test_un_curseur_PERIME_OU_FORGE_rend_400_invalid_cursor_et_pas_un_500(seams):
    """#621 — `InvalidCursor` n'était rattrapé nulle part.

    L'adaptateur REST ne traduit QUE `AuthzDenied` ; tout le reste remonte et sort en
    500. Un curseur tronqué par un copier-coller, ou repassé d'un régime de tri dans
    l'autre, donnait donc une panne de serveur là où le client a seulement une chose
    à faire : repartir sans curseur. Un 500 ne le lui dit pas, et il ne se distingue
    pas d'une vraie panne dans les alertes.
    """
    seams["store"] = _Store(leve=InvalidCursor("off:40"))
    with pytest.raises(AuthzDenied) as e:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", cursor="b2ZmOjQw"))
    assert (e.value.status, e.value.code) == (400, "invalid_cursor")
    assert "cursor" in e.value.message


def test_les_DEUX_chemins_nomment_un_curseur_illisible_PAREIL(seams, monkeypatch):
    """Une même route, un même défaut, un seul code — sinon le client en gère deux.

    Le chemin natif rendait `curseur_invalide` et le chemin recopié rien du tout :
    un front qui apprend à traiter l'un tombe sur l'autre au premier tableau de
    l'autre provenance, et la nature du tableau ne lui est pas servie.
    """
    seams["fiche"] = {**TABLE, "props": {**TABLE["props"], "legacy_id": None}}
    monkeypatch.setattr(R.ds, "make_store",
                        lambda sub: pytest.fail("store consulté sur un tableau natif"))
    with pytest.raises(AuthzDenied) as e:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", cursor="pas-un-entier"))
    assert (e.value.status, e.value.code) == (400, "invalid_cursor")


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


def test_le_total_est_FILTRE_pas_le_volume_du_tableau(seams):
    seams["store"] = _Store(total=3)
    out = R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", q="clos",
                                          filter=["statut:clos"]))
    assert out["total"] == 3
    # Le compte reçoit les MÊMES filtres que la page : sinon le pied annonce
    # « 3 sur 12 000 » alors que l'écran en montre 3 sur 3.
    assert seams["store"].filtres_compte == [
        {"field": "statut", "op": "eq", "value": "clos"}]


def test_le_total_compte_le_MEME_jeu_que_la_page_noms_PLATS_RESOLUS(seams):
    """#621 — le compte partait sur les noms NON résolus, la page sur les résolus.

    Sur un schéma à double service, `contact1_nom` est un nom SERVI en lecture : la
    page le traduit en `contacts[0].nom` avant son SQL, le compte ne le traduisait pas.
    Deux `WHERE` différents, un pied de tableau qui annonce un autre jeu que celui
    qu'il coiffe — et rien qui échoue, jamais.
    """
    seams["store"] = _Store(schema=SCHEMA_ALIAS)
    R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl", filter=["contact1_nom:Acme"]))

    st = seams["store"]
    assert st.filtres_page == [
        {"field": "contacts[0].nom", "op": "eq", "value": "Acme"}]
    assert st.filtres_compte == st.filtres_page


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
def test_un_filtre_SANS_DEUX_POINTS_est_refuse_en_nommant_la_forme_attendue(seams):
    """#621 — il était IGNORÉ, et la page partait comme si le filtre n'existait pas.

    Le choix d'origine était écrit : « faire échouer toute la page pour une entrée
    malformée coûte plus que de ne pas l'appliquer ». Il compare le mauvais couple.
    Ce qui part alors n'est pas une page en moins, c'est une page NON FILTRÉE servie
    en 200 à un appelant qui a demandé un filtre — il lit un tableau entier en croyant
    lire un extrait, et rien dans la réponse ne le détrompe.
    """
    with pytest.raises(AuthzDenied) as e:
        R._compose(CTX, R.NodeRowsInput(node_id="nod_tbl",
                                        filter=["statut:clos", "n_importe_quoi"]))
    assert (e.value.status, e.value.code) == (400, "invalid_filter")
    # Le refus NOMME la forme attendue et l'entrée fautive : un « non » sans les deux
    # laisse deviner laquelle des entrées corriger, et vers quoi.
    assert "colonne:valeur" in e.value.message
    assert "n_importe_quoi" in e.value.message
    # Et rien n'a été servi : le refus tombe AVANT la page, pas à côté d'elle.
    assert seams["store"].vu == {}


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
