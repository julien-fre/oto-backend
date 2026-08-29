"""Ouvrir un nœud (lot ④) — le 404 indistinct, le fil, et ce qu'on refuse de servir."""
import pytest

from oto_mcp.capabilities import node_view as N
from oto_mcp.capabilities._types import AuthzDenied, NotModified, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=2)

PAGE = {"id": 1, "public_id": "nod_page", "parent_id": None, "kind": "page",
        "owner_type": "org", "owner_id": "2", "position": 0,
        "props": {"title": "Brief", "created_by": "u1"},
        "created_at": "2026-08-01", "updated_at": "2026-08-14 10:00:00"}
TABLE = {**PAGE, "id": 2, "public_id": "nod_tbl", "kind": "tableau",
         "props": {"title": "Vivier", "child_schema": {"fields": [{"key": "nom"}]}}}


@pytest.fixture
def seams(monkeypatch):
    etat = {"fiche": PAGE, "blocs": [], "chaine": [], "freres": {}, "grants": []}
    monkeypatch.setattr(N.db_node, "node_by_public_id", lambda pid: etat["fiche"])
    monkeypatch.setattr(N.db_node, "blocks_of", lambda nid: etat["blocs"])
    monkeypatch.setattr(N.db_node, "ancestors_of", lambda nid, max_depth=12: etat["chaine"])
    monkeypatch.setattr(N.db_node, "siblings_of", lambda p, owner, cap=50: etat["freres"])
    monkeypatch.setattr(N.db_shell, "direct_grants", lambda sub: etat["grants"])
    monkeypatch.setattr(N.db_shell, "names_of", lambda subs: {"u1": "Alexis"})
    monkeypatch.setattr(N.ownership, "active_org_principals",
                        lambda sub, org: [("org", "2"), ("user", "u1"), ("group", "9")])
    return etat


# ── Le 404 indistinct : le point de sécurité du module ─────────────────────────
def test_un_noeud_INEXISTANT_et_un_noeud_INTERDIT_rendent_LE_MEME_refus(seams):
    seams["fiche"] = None
    with pytest.raises(AuthzDenied) as absent:
        N._compose(CTX, "nod_x")

    seams["fiche"] = {**PAGE, "owner_type": "org", "owner_id": "999"}   # une AUTRE org
    with pytest.raises(AuthzDenied) as interdit:
        N._compose(CTX, "nod_page")

    # Même statut, même code, même message : le code d'état ne doit pas devenir un
    # oracle d'existence — sinon on énumère le contenu d'une org en lisant des 403.
    assert (absent.value.status, absent.value.code, absent.value.message) == \
           (interdit.value.status, interdit.value.code, interdit.value.message)
    assert absent.value.status == 404


def test_un_noeud_PARTAGE_en_direct_est_lisible(seams):
    from oto_mcp.db import shell as db_shell
    pid = db_shell._public_id_derive("prj", "7")
    seams["fiche"] = {**PAGE, "public_id": pid, "owner_type": "user", "owner_id": "u9"}
    seams["grants"] = [{"resource_type": "project", "resource_id": "7"}]
    assert N._compose(CTX, pid)["id"] == pid


def test_la_voie_des_grants_n_est_PAS_payee_quand_le_proprietaire_suffit(seams, monkeypatch):
    appels = {"n": 0}

    def _compte(sub):
        appels["n"] += 1
        return []
    monkeypatch.setattr(N.db_shell, "direct_grants", _compte)
    N._compose(CTX, "nod_page")      # possédé par l'org active
    assert appels["n"] == 0, "une requête de grants par ouverture de page, pour rien"


# ── Page vs tableau ────────────────────────────────────────────────────────────
def test_ouvrir_un_TABLEAU_rend_le_schema_JAMAIS_les_lignes(seams, monkeypatch):
    seams["fiche"] = TABLE
    appels = {"blocs": 0}
    monkeypatch.setattr(N.db_node, "blocks_of",
                        lambda nid: appels.__setitem__("blocs", appels["blocs"] + 1) or [])
    out = N._compose(CTX, "nod_tbl")
    assert out["type"] == "table"
    assert out["columns"] == {"fields": [{"key": "nom"}]}
    assert "body" not in out
    assert appels["blocs"] == 0


def test_un_tableau_LIBRE_rend_None_pas_une_liste_vide(seams):
    seams["fiche"] = {**TABLE, "props": {"title": "Libre"}}
    # 29 des 83 tableaux de production ne déclarent aucun schéma. `[]` dirait « aucune
    # colonne » ; `None` dit « table libre », ce qui est la vérité.
    assert N._compose(CTX, "nod_tbl")["columns"] is None


def test_le_corps_d_une_page_porte_les_ids_de_blocs_et_leur_source(seams):
    seams["blocs"] = [
        {"public_id": "blk_a", "type": "text", "props": {"md": "# Titre\n"}},
        {"public_id": "blk_b", "type": "code", "props": {"md": "```py\nx=1\n```", "lang": "py"}},
    ]
    body = N._compose(CTX, "nod_page")["body"]
    assert [b["id"] for b in body] == ["blk_a", "blk_b"]
    assert body[1]["lang"] == "py"
    # La source EXACTE voyage : le front rend ce qu'il sait rendre et laisse passer le
    # reste, au lieu de recevoir une forme appauvrie.
    assert "".join(b["md"] for b in body) == "# Titre\n```py\nx=1\n```"


# ── Le fil ─────────────────────────────────────────────────────────────────────
def test_le_fil_va_de_la_RACINE_au_noeud_avec_la_fratrie_de_chaque_maillon(seams):
    seams["chaine"] = [
        {"id": 10, "public_id": "nod_r", "parent_id": None, "kind": "page",
         "props": {"title": "Racine"}},
        {"id": 1, "public_id": "nod_page", "parent_id": 10, "kind": "page",
         "props": {"title": "Brief"}},
    ]
    seams["freres"] = {
        None: [{"public_id": "nod_r", "kind": "page", "title": "Racine"}],
        10: [{"public_id": "nod_page", "kind": "page", "title": "Brief"},
             {"public_id": "nod_s", "kind": "tableau", "title": "Vivier"}],
    }
    fil = N._compose(CTX, "nod_page")["trail"]
    assert [c["id"] for c in fil] == ["nod_r", "nod_page"]
    # Le maillon lui-même est DANS sa fratrie — le popover s'ouvre sans rien redemander.
    assert {s["id"] for s in fil[1]["siblings"]} == {"nod_page", "nod_s"}
    assert fil[1]["siblings"][1]["type"] == "table"


# ── Ce qu'on refuse de servir, et qu'on DIT ────────────────────────────────────
def test_ce_qui_n_est_pas_servi_est_NOMME_pas_rendu_vide(seams):
    out = N._compose(CTX, "nod_page")
    assert "access" not in out and "dependencies" not in out
    # Un `editors: []` affirmerait « personne d'autre » ; un `dependencies: []`
    # autoriserait une suppression. L'absence se DIT.
    assert set(out["non_servi"]) >= {"access", "dependencies"}


def test_un_noeud_sans_auteur_le_DIT_plutot_que_d_attribuer_a_tort(seams):
    seams["fiche"] = {**PAGE, "props": {"title": "Orpheline"}}
    out = N._compose(CTX, "nod_page")
    assert out["modified"]["by"] is None
    assert "modified.by" in out["non_servi"]


def test_la_date_est_ISO_jamais_une_phrase_relative(seams):
    # « jeudi » deviendrait faux la semaine suivante sans que `rev` bouge : le 304
    # confirmerait alors un cache qui ment.
    assert N._compose(CTX, "nod_page")["modified"]["at"] == "2026-08-14 10:00:00"


# ── `rev` et le 304 ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_meme_rev_rend_la_sentinelle(seams):
    corps = N._compose(CTX, "nod_page")
    res = await N._node(CTX, N.NodeInput(node_id="nod_page", rev=corps["rev"]))
    assert isinstance(res, NotModified) and res.rev == corps["rev"]


def test_le_rev_suit_le_CORPS(seams):
    avant = N._compose(CTX, "nod_page")["rev"]
    seams["blocs"] = [{"public_id": "blk_a", "type": "text", "props": {"md": "neuf"}}]
    assert N._compose(CTX, "nod_page")["rev"] != avant


def test_le_corps_valide_l_Output_declare(seams):
    N.NodeOut(**N._compose(CTX, "nod_page"))
    seams["fiche"] = TABLE
    N.NodeOut(**N._compose(CTX, "nod_tbl"))


# ── La couche DB : le prédicat de genre, comme au rail ─────────────────────────
def test_chaque_lecture_de_nodes_exclut_les_LIGNES():
    import ast
    import inspect

    from oto_mcp.db import node_view as db_node

    source = inspect.getsource(db_node)
    arbre = ast.parse(source)
    vus = []
    for fn in [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef)]:
        corps = ast.get_source_segment(source, fn) or ""
        if "FROM nodes" not in corps:
            continue
        vus.append(fn.name)
        assert "_HORS_LIGNES" in corps or "kind <> 'ligne'" in corps, fn.name
    assert vus, "aucune lecture de `nodes` — le test ne garde plus rien"


def test_la_remontee_du_fil_est_BORNEE():
    # `nodes.parent_id` n'a pas de clé étrangère : rien en base n'empêche un cycle.
    # Sans borne, la remontée tournerait jusqu'au timeout.
    import inspect

    from oto_mcp.db import node_view as db_node
    src = inspect.getsource(db_node.ancestors_of)
    assert "max_depth" in src and "niveau <" in src


# ── Les poignées vers les autres surfaces (front tiers, 29/08) ─────────────────
def test_une_page_rend_son_doc_id_et_le_project_id_de_ses_props(seams):
    seams["fiche"] = {**PAGE, "props": {"title": "Brief", "legacy": "doc",
                                        "legacy_id": 12, "project_id": 7}}
    out = N._compose(CTX, "nod_page")
    assert (out["doc_id"], out["project_id"]) == (12, 7)


def test_un_projet_rend_son_project_id_et_aucun_doc_id(seams):
    seams["fiche"] = {**PAGE, "props": {"title": "Refonte", "legacy": "prj",
                                        "legacy_id": 7, "pinned": True}}
    out = N._compose(CTX, "nod_page")
    assert (out["doc_id"], out["project_id"]) == (None, 7)


def test_un_tableau_range_sous_un_projet_lit_le_projet_sur_le_fil(seams):
    """Les props d'un tableau ne portent pas de `project_id` : c'est sa PLACE dans
    l'arbre qui le dit — le maillon `prj` le plus proche."""
    seams["fiche"] = {**TABLE, "props": {"title": "Vivier", "legacy": "tbl", "legacy_id": 3}}
    seams["chaine"] = [
        {"id": 10, "public_id": "nod_r", "parent_id": None, "kind": "page",
         "props": {"title": "Refonte", "legacy": "prj", "legacy_id": 7}},
        {"id": 2, "public_id": "nod_tbl", "parent_id": 10, "kind": "tableau",
         "props": {"title": "Vivier", "legacy": "tbl", "legacy_id": 3}},
    ]
    out = N._compose(CTX, "nod_tbl")
    assert (out["doc_id"], out["project_id"]) == (None, 7)


def test_un_noeud_sans_source_legacy_rend_null_pas_un_entier_devine(seams):
    out = N._compose(CTX, "nod_page")           # PAGE n'a ni legacy ni project_id
    assert (out["doc_id"], out["project_id"]) == (None, None)
    assert "doc_id" in out and "project_id" in out      # présents, pas absents


def test_un_bloc_liste_dit_si_elle_est_numerotee(seams):
    seams["blocs"] = [
        {"public_id": "blk_o", "type": "text",
         "props": {"md": "1. un\n2. deux\n", "role": "list", "items": ["un", "deux"]}},
        {"public_id": "blk_u", "type": "text",
         "props": {"md": "- un\n- deux\n", "role": "list", "items": ["un", "deux"]}},
        {"public_id": "blk_p", "type": "text",
         "props": {"md": "prose\n", "role": "paragraph"}},
    ]
    body = N._compose(CTX, "nod_page")["body"]
    assert [b.get("ordered") for b in body] == [True, False, None]
    # Sur un paragraphe la clé est ABSENTE, pas `false` : « pas une liste » n'est pas
    # « une liste non numérotée ».
    assert "ordered" not in body[2]
