"""Une ÉCRITURE rend un accusé, pas la page — et `fields` est honoré ou refusé.

Quatre signaux d'usage, deux d'entre eux venus d'un client (#461, #506, #525, #530),
tous sur `oto_doc` et tous sur la même racine : ce qui revient d'un appel n'a pas de
budget. Ils décrivent deux défauts distincts.

**(1) `op=get` acceptait `fields` et l'IGNORAIT** (#461, #525). Un appelant qui demandait
`fields=["id","title","rev","updated_at"]` — le cas le plus courant, relire le `rev` avant
un `op=patch` en concurrence optimiste — récupérait la page ENTIÈRE. Argument
accepté-et-ignoré : la famille que ce dépôt refuse. Un argument qu'une surface ne sait pas
honorer se REFUSE en le nommant, il ne s'avale pas.

**(2) `op=patch` et `op=update` rendaient le CORPS ENTIER** (#506, #530). Sur les deux
grosses pages de la base de connaissance d'un client — 128 000 et 85 000 caractères — la
réponse dépasse le plafond de résultat du client : **une écriture RÉUSSIE est rendue à
l'agent comme un échec**, la charge part en fichier, et un agent qui lit l'erreur au
premier degré réécrit (double écriture) ou déclare l'opération ratée. C'est le plus grave
des deux, parce qu'il transforme un succès en faux échec.

Mesuré sur la page de 128 104 caractères reproduite plus bas (JSON servi, caractères) :

    op                                        avant        après
    op=patch (une section)                  133 166          471
    op=update                               133 245          471
    op=move                                 133 245          471
    op=get fields=[id,title,rev,updated_at] 133 245          377
    op=get (nu — une LECTURE)               133 245      133 245   ← inchangé, exprès

Le pendant côté LISTE est déjà tenu par `test_list_view_budget.py` ; ce fichier tient le
pendant côté PAGE UNIQUE (lecture projetée et écriture). Même seam
(`output_projection.summarize`), même vocabulaire : les corps deviennent une TAILLE et le
retour NOMME ce qu'il a écarté.
"""
import json

import pytest

from oto_mcp.capabilities import docs as D
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=None)


def _corps(vise: int) -> str:
    """Un corps de page réaliste : des SECTIONS, pas un pavé — c'est ce qui rend
    `op=patch` mesurable (on remplace UNE section, les autres restent en place)."""
    out, n, total = [], 0, 0
    while total < vise:
        bloc = (f"## Section {n}\n\nProcédure interne : le contexte, la règle, "
                "l'exception, et qui tranche en cas de doute.\n\n")
        out.append(bloc)
        total += len(bloc)
        n += 1
    return "".join(out)


# Les tailles RÉELLES des deux pages citées par #530 (doc 662 et doc 692 de la base de
# connaissance Tulina) : c'est à cette échelle que le retour cesse de passer.
GROS = _corps(128_000)
PETIT = _corps(85_000)


def _page(body: str, doc_id: int = 662) -> dict:
    return {"id": doc_id, "project_id": 153, "parent_id": None, "title": "Guide",
            "description": None, "position": 0, "body_md": body, "kind": "doc",
            "created_at": "2026-08-01 09:00:00", "updated_at": "2026-08-27 11:04:00"}


@pytest.fixture
def seams(monkeypatch):
    """Le chemin réel d'une écriture, avec un corps de taille réaliste."""
    etat = {"body": GROS}
    monkeypatch.setattr(D.ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(D, "_public_doc_url", lambda tok, sub=None: None)
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(etat["body"], i))
    monkeypatch.setattr(D.db, "doc_rev", lambda t, b: "9f2c41a")
    monkeypatch.setattr(D.db, "log_project_activity", lambda *a, **k: None)
    monkeypatch.setattr(D.db, "list_doc_revisions", lambda did, limit=50: [])
    monkeypatch.setattr(D.db, "move_doc", lambda did, p, position=None: None)

    def _update(did, title=None, body_md=None, kind=None, edited_by=None,
                description=None, expected_rev=None):
        if body_md is not None:
            etat["body"] = body_md
    monkeypatch.setattr(D.db, "update_doc", _update)
    monkeypatch.setattr(D.db, "create_doc",
                        lambda pid, title, parent_id=None, body_md="", kind="doc",
                        created_by=None, description=None: 662)
    return etat


def _taille(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


# ── Défaut n°2 : une écriture réussie rendait la page entière (#506, #530) ────────────

def test_un_patch_rend_l_accuse_et_non_les_128_000_caracteres_de_la_page(seams):
    """Le cœur de #530 : la réponse d'une écriture ne doit pas suivre la taille de la page."""
    out = D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="Section 3",
                                 body_md="Nouveau contenu de section."))
    assert "body_md" not in out                    # le corps n'est PAS rejoué à l'appelant
    assert _taille(out) < 1_500                    # 133 166 c. avant correction
    # L'agent sait ce qu'il n'a pas : une TAILLE, jamais un extrait tronqué.
    assert out["body_md_length"] == len(seams["body"])


def test_l_accuse_porte_de_quoi_ENCHAINER_sans_relire(seams):
    """Ce que les quatre signaux demandent nommément : id, rev, updated_at — le `rev`
    surtout, puisque `update`/`patch` l'exigent en `expected_rev` au tour suivant."""
    out = D._doc(CTX, D.DocInput(op="update", doc_id=662, body_md="corps refondu"))
    assert out["id"] == 662 and out["rev"] == "9f2c41a"
    assert out["updated_at"] == "2026-08-27 11:04:00"
    assert out["title"] == "Guide" and out["project_id"] == 153
    # Et le retour NOMME ce qu'il a écarté + le chemin vers le brut (jamais muet).
    assert out["projection"]["omitted"] == ["body_md"]
    assert 'fields=["*"]' in out["projection"]["hint"]


def test_le_budget_d_une_ecriture_ne_suit_PAS_la_taille_de_la_page(seams, monkeypatch):
    """L'invariant, mesuré : deux pages de tailles très différentes, deux accusés de
    taille quasi identique. Un corps réintroduit dans l'accusé fait exploser l'écart."""
    gros = D._doc(CTX, D.DocInput(op="update", doc_id=662, body_md=GROS))
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(PETIT, i))
    petit = D._doc(CTX, D.DocInput(op="update", doc_id=692, body_md=PETIT))
    assert abs(_taille(gros) - _taille(petit)) < 20      # seuls id/ids diffèrent


def test_le_corps_reste_ATTEIGNABLE_apres_une_ecriture(seams):
    """Projeter n'est pas amputer : `fields=["*"]` rend la page entière, comme sur la liste."""
    out = D._doc(CTX, D.DocInput(op="update", doc_id=662, body_md="corps refondu",
                                 fields=["*"]))
    assert out["body_md"] == "corps refondu"
    assert "projection" not in out          # rien d'écarté ⟹ pas de bloc à annoncer


def test_le_patch_garde_son_avertissement_de_sous_sections_perdues(seams, monkeypatch):
    """#530 demande explicitement `removed_subsections` dans l'accusé — et le garde-fou
    du signal #334 (un `replace` emporte les sous-sections) ne doit pas tomber avec le
    corps : c'est justement l'information qu'on ne peut PAS redécouvrir sans relire."""
    corps = "# T\n\n## A\n\nvieux\n\n### A1\n\nsous\n\n## B\n\nb\n"
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(corps, i))
    out = D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="A", body_md="neuf"))
    assert out["removed_subsections"] == ["A1"] and "A1" in out["warning"]
    assert "body_md" not in out


def test_un_move_ne_rejoue_pas_le_corps_qu_il_n_a_pas_touche(seams):
    """Un déplacement ne modifie aucun contenu : rendre la page entière est du pur poids."""
    out = D._doc(CTX, D.DocInput(op="move", doc_id=662, parent_id=None))
    assert "body_md" not in out and _taille(out) < 1_500


# ── Défaut n°1 : `fields` accepté-et-ignoré sur `op=get` (#461, #525) ─────────────────

def test_get_HONORE_fields_le_cas_lire_le_rev_avant_de_patcher(seams):
    """#461 mot pour mot : « fetch only the rev for an optimistic-concurrency patch »."""
    out = D._doc(CTX, D.DocInput(op="get", doc_id=662,
                                 fields=["id", "title", "rev", "updated_at"]))
    assert "body_md" not in out
    assert out["rev"] == "9f2c41a" and out["updated_at"] == "2026-08-27 11:04:00"
    assert _taille(out) < 1_000                     # 133 245 c. avant correction


def test_get_SANS_fields_rend_toujours_la_page_entiere(seams):
    """Une LECTURE nue reste une lecture : son travail est de livrer la page. Le
    dashboard en dépend (revue de proposition : il affiche `body_md` de cette réponse)."""
    out = D._doc(CTX, D.DocInput(op="get", doc_id=662))
    assert out["body_md"] == GROS and "projection" not in out


def test_get_avec_fields_garde_de_quoi_ADRESSER_la_page(seams):
    out = D._doc(CTX, D.DocInput(op="get", doc_id=662, fields=["kind"]))
    assert {"id", "project_id", "parent_id", "title", "kind"} <= set(out)
    assert "position" in out["projection"]["omitted"]


def test_fields_vide_est_refuse_sur_get_aussi(seams):
    """Un `fields=[]` ne s'avale pas — même refus que sur la liste."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="get", doc_id=662, fields=[]))
    assert e.value.code == "empty_fields"


def test_fields_sur_une_op_qui_ne_projette_PAS_est_refuse_en_le_nommant(seams):
    """La leçon générale de #461 : ce qu'une surface ne sait pas honorer, elle le
    REFUSE. Sans quoi le prochain appelant paiera le même silence sur une autre op."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="revisions", doc_id=662, fields=["id"]))
    assert e.value.code == "unsupported_fields"
    assert "get" in e.value.message and "revisions" in e.value.message
