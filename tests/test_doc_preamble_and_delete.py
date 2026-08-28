"""Rendre ÉDITABLES les grandes pages : le préambule, et la suppression d'une section.

Quatre signaux d'usage du même client, sur le même geste quotidien (#481, #492, #507,
#583), deux manques, une seule cause : `op=patch` n'adressait une région que par son
TITRE markdown.

**(1) Ce qui est AU-DESSUS du premier titre est inatteignable** (#481, #492, #507).
Chaque page de cette base ouvre sur un bandeau de provenance portant une date de dernière
vérification, posé avant le premier titre. Il n'appartient à aucune section : le
rafraîchir passait donc par `op=update`, qui exige le corps ENTIER en argument — 128 000
caractères pour changer une date. Résultat mesuré en prod : « les pages les plus longues
et les plus éditées de la base portent les bandeaux les plus périmés », soit exactement
l'inverse de ce que le champ sert à dire. Trois signalements en trois semaines.

**(2) On ne peut pas SUPPRIMER une section, titre compris** (#583, rencontré deux fois
dans un seul déroulé). `replace` remplace le corps et GARDE le titre : la purge à J-14 du
journal glissant a donc laissé un titre orphelin surmontant un paragraphe-pierre tombale.

**Les deux désignations retenues, et pourquoi.**

- Le préambule se désigne sur un **AUTRE AXE** : `region="preamble"`, jamais
  `section="__preamble__"` (la forme que les signaux proposaient). Un mot réservé DANS
  `section` est un mot qu'une page peut écrire en titre — `## __preamble__` est un titre
  markdown parfaitement valide — et le jour où elle le fait, la même chaîne désigne deux
  choses. Sur un axe séparé, la collision est impossible par CONSTRUCTION, pas par
  improbabilité : `section` reste l'espace de noms des titres, `region` celui des régions
  sans titre. Un `section="__preamble__"` qui ne résout pas est REFUSÉ, en nommant les
  sections disponibles et en pointant `region="preamble"` — jamais deviné.
- La suppression est un **MODE de plus** (`mode="delete"`), pas une op distincte. Elle
  partage tout avec `patch` : l'adressage, le verrou optimiste `expected_rev`, la
  révision posée, l'accusé, l'annonce des sous-sections emportées. Et surtout `op=delete`
  existe déjà — il supprime LA PAGE : un `op=delete_section` voisin serait à un mot près
  de la destruction du document.

Mesuré sur une page de la taille de celles que les signaux citent (caractères du JSON
d'ARGUMENTS que l'agent doit émettre — c'est le coût dont ils parlent, « not affordable
in context ») :

    geste                                            avant        après
    rafraîchir le bandeau de provenance            128 000+          ~250
    supprimer l'entrée J-14 du journal glissant    128 000+          ~120

Le pendant côté RÉPONSE est tenu par `test_doc_write_receipt.py` (l'accusé) ; ce
fichier-ci tient le pendant côté ARGUMENT.
"""
import json

import pytest

from oto_mcp import doc_patch
from oto_mcp.capabilities import docs as D
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=None)

# Le bandeau de provenance, tel que la convention de page du client le pose : AVANT le
# premier titre, donc dans aucune section.
BANDEAU = ("> **Source** : ingestion quotidienne — 5 sources.\n"
           "> **Last verified** : 16 August 2026.")
BANDEAU_A_JOUR = ("> **Source** : ingestion quotidienne — 6 sources.\n"
                  "> **Last verified** : 18 August 2026.")


def _page_kb(vise: int) -> str:
    """Une page réaliste de cette base : un bandeau de provenance, puis un journal
    glissant daté (celui que #583 n'arrivait pas à purger), puis des sections."""
    out = [BANDEAU, "", "# Fiche", "",
           "## Journal glissant", "",
           "### 10 August 2026", "", "entrée à purger au bout de quatorze jours.", "",
           "### 11 August 2026", "", "entrée encore dans la fenêtre.", ""]
    total = len("\n".join(out))
    n = 0
    while total < vise:
        bloc = (f"## Section {n}\n\nProcédure interne : le contexte, la règle, "
                "l'exception, et qui tranche en cas de doute.\n")
        out.append(bloc)
        total += len(bloc) + 1
        n += 1
    return "\n".join(out) + "\n"


# La taille des pages citées par les signaux (doc 662 : 92 K → 98 K → 128 K → 158 K au
# fil des trois semaines ; doc 692 : 54 K).
GROS = _page_kb(128_000)


def _page(body: str, doc_id: int = 662) -> dict:
    return {"id": doc_id, "project_id": 153, "parent_id": None, "title": "Fiche",
            "description": None, "position": 0, "body_md": body, "kind": "doc",
            "created_at": "2026-08-01 09:00:00", "updated_at": "2026-08-28 07:12:00"}


@pytest.fixture
def seams(monkeypatch):
    """Le chemin RÉEL d'une écriture de page, corps de taille réaliste, sans base.
    `db.update_doc` est le seul point stubbé : c'est lui qui porte le verrou optimiste
    et la pose de révision, et on veut voir ce que la capacité lui passe."""
    etat = {"body": GROS, "appels": []}
    monkeypatch.setattr(D.ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(D, "_public_doc_url", lambda tok, sub=None: None)
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(etat["body"], i))
    monkeypatch.setattr(D.db, "doc_rev", lambda t, b: "9f2c41a")
    monkeypatch.setattr(D.db, "log_project_activity", lambda *a, **k: None)

    def _update(did, title=None, body_md=None, kind=None, edited_by=None,
                description=None, expected_rev=None):
        etat["appels"].append({"doc_id": did, "expected_rev": expected_rev,
                               "edited_by": edited_by, "body_md": body_md})
        if body_md is not None:
            etat["body"] = body_md
    monkeypatch.setattr(D.db, "update_doc", _update)
    return etat


def _args(payload: dict) -> int:
    """Ce que l'AGENT doit émettre pour obtenir le geste — la charge dont les quatre
    signaux disent qu'elle ne tient pas dans le contexte."""
    return len(json.dumps(payload, ensure_ascii=False))


# ── (1) Le préambule : rafraîchir un bandeau sans réécrire la page ────────────────────

def test_le_bandeau_se_rafraichit_par_region_preamble(seams):
    """#481/#492/#507 : le geste quotidien qui n'existait pas."""
    D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble",
                           body_md=BANDEAU_A_JOUR))
    corps = seams["body"]
    assert corps.startswith(BANDEAU_A_JOUR)
    assert "16 August 2026" not in corps and "5 sources" not in corps
    # Et RIEN d'autre n'a bougé : c'est toute la promesse du patch.
    assert corps[corps.index("# Fiche"):] == GROS[GROS.index("# Fiche"):]


def test_rafraichir_le_bandeau_coute_250_caracteres_au_lieu_de_128_000(seams):
    """La mesure que ces signaux réclament. `op=update` demandait le corps ENTIER en
    argument ; `region="preamble"` ne demande que le bandeau."""
    avant = _args({"op": "update", "doc_id": 662,
                   "body_md": GROS.replace(BANDEAU, BANDEAU_A_JOUR)})
    apres = _args({"op": "patch", "doc_id": 662, "region": "preamble",
                   "body_md": BANDEAU_A_JOUR})
    assert avant > 128_000
    assert apres < 300
    assert avant / apres > 400
    # Et l'appel court vraiment : la capacité l'accepte tel quel.
    D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble",
                           body_md=BANDEAU_A_JOUR))
    assert "18 August 2026" in seams["body"]


def test_le_cout_du_geste_ne_suit_PLUS_la_taille_de_la_page(seams, monkeypatch):
    """L'invariant derrière les trois signaux : le bandeau des pages LES PLUS LONGUES
    était le plus périmé, parce que le coût du geste suivait la taille de la page."""
    petite = _page_kb(3_000)
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(petite, i))
    D._doc(CTX, D.DocInput(op="patch", doc_id=692, region="preamble",
                           body_md=BANDEAU_A_JOUR))
    assert seams["body"].startswith(BANDEAU_A_JOUR)
    # Même argument, à l'octet près, quelle que soit la page visée.
    a = _args({"op": "patch", "doc_id": 662, "region": "preamble", "body_md": BANDEAU_A_JOUR})
    b = _args({"op": "patch", "doc_id": 692, "region": "preamble", "body_md": BANDEAU_A_JOUR})
    assert a == b


def test_l_ecriture_du_preambule_rend_un_ACCUSE_pas_la_page(seams):
    """Acquis d'hier (#530) : une écriture ne rejoue pas le corps. Le nouveau chemin
    passe par le même seam de projection, il ne le contourne pas."""
    out = D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble",
                                 body_md=BANDEAU_A_JOUR))
    assert "body_md" not in out and out["body_md_length"] == len(seams["body"])
    assert out["rev"] == "9f2c41a" and out["id"] == 662
    assert len(json.dumps(out, ensure_ascii=False)) < 1_500


# ── (2) Supprimer une section, TITRE COMPRIS ─────────────────────────────────────────

def test_mode_delete_retire_l_entree_du_journal_et_son_titre(seams):
    """#583 : la purge à J-14 laissait un titre orphelin et une pierre tombale."""
    out = D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="10 August 2026",
                                 mode="delete"))
    corps = seams["body"]
    assert "### 10 August 2026" not in corps
    assert "entrée à purger" not in corps
    assert "10 August 2026" not in doc_patch.headings(corps)
    # La voisine reste, avec son titre.
    assert "### 11 August 2026" in corps and "encore dans la fenêtre" in corps
    assert "body_md" not in out


def test_supprimer_une_section_coute_120_caracteres_au_lieu_de_128_000(seams):
    avant = _args({"op": "update", "doc_id": 662,
                   "body_md": doc_patch.patch_section(GROS, "10 August 2026",
                                                      mode="delete")})
    apres = _args({"op": "patch", "doc_id": 662, "section": "10 August 2026",
                   "mode": "delete"})
    assert avant > 128_000
    assert apres < 150
    assert avant / apres > 800


def test_delete_annonce_les_sous_sections_emportees_comme_replace(seams):
    """La portée d'une section (signal #334) ne change pas : `delete` emporte les
    sous-sections, et l'annonce — c'est ce qu'on ne peut pas redécouvrir sans relire."""
    out = D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="Journal glissant",
                                 mode="delete"))
    assert out["removed_subsections"] == ["10 August 2026", "11 August 2026"]
    assert "delete" in out["warning"] and "revisions" in out["warning"]
    assert "## Journal glissant" not in seams["body"]


# ── Le verrou optimiste et les révisions survivent aux DEUX nouveaux chemins ──────────

@pytest.mark.parametrize("champs", [
    {"region": "preamble", "body_md": BANDEAU_A_JOUR},
    {"section": "10 August 2026", "mode": "delete"},
])
def test_les_nouveaux_chemins_HONORENT_expected_rev(seams, champs):
    """Ces pages sont éditées par plusieurs agents à la fois : tout chemin d'écriture
    passe par `db.update_doc`, qui porte le compare-and-set ET pose la révision."""
    D._doc(CTX, D.DocInput(op="patch", doc_id=662, expected_rev="9f2c41a", **champs))
    assert seams["appels"] == [{"doc_id": 662, "expected_rev": "9f2c41a",
                                "edited_by": "u1",
                                "body_md": seams["appels"][0]["body_md"]}]
    assert seams["appels"][0]["body_md"] is not None      # une révision sera posée


@pytest.mark.parametrize("champs", [
    {"region": "preamble", "body_md": BANDEAU_A_JOUR},
    {"section": "10 August 2026", "mode": "delete"},
])
def test_un_conflit_reste_un_409_sur_les_nouveaux_chemins(seams, monkeypatch, champs):
    def _boom(*a, **k):
        raise D.db.DocConflict("autre_rev")
    monkeypatch.setattr(D.db, "update_doc", _boom)
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, expected_rev="9f2c41a", **champs))
    assert e.value.status == 409 and e.value.code == "conflict"


# ── Les refus : une désignation qui ne résout pas se REFUSE en nommant ────────────────

def test_section_double_underscore_preamble_est_REFUSEE_et_POINTE_la_region(seams):
    """Le piège du mot réservé, tenu par le bout qui compte : la forme que les signaux
    proposaient ne devient JAMAIS un synonyme silencieux de la région. Elle se heurte au
    refus « section introuvable » — enrichi d'un pointeur, puisque la page A un
    préambule."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="__preamble__",
                               body_md=BANDEAU_A_JOUR))
    assert e.value.status == 404 and e.value.code == "unknown_section"
    assert "Journal glissant" in e.value.message          # les sections disponibles
    assert 'region="preamble"' in e.value.message         # et la bonne poignée
    assert seams["appels"] == []                          # rien n'a été écrit


def test_une_page_qui_a_VRAIMENT_une_section___preamble___la_sert(seams, monkeypatch):
    """Le contre-exemple qui justifie l'axe séparé : `## __preamble__` est un titre
    markdown valide. Avec un mot réservé dans `section`, cette page devenait
    inadressable ; avec deux axes, chacun désigne sa chose."""
    corps = ("bandeau.\n\n# T\n\n## __preamble__\n\nune vraie section ainsi nommée.\n")
    monkeypatch.setattr(D.db, "get_doc_by_id", lambda i: _page(corps, i))
    D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="__preamble__",
                           body_md="contenu neuf."))
    assert "## __preamble__" in seams["body"] and "contenu neuf." in seams["body"]
    assert seams["body"].startswith("bandeau.")           # le vrai préambule intact
    # …et la région reste atteignable séparément, sur l'autre axe.
    D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble", body_md="bandeau v2."))
    assert seams["body"].startswith("bandeau v2.")
    assert "## __preamble__" in seams["body"]


def test_section_ET_region_ensemble_est_REFUSE(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="Journal glissant",
                               region="preamble", body_md="x"))
    assert e.value.code == "ambiguous_target"
    assert seams["appels"] == []


def test_ni_section_ni_region_est_REFUSE(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, body_md="x"))
    assert e.value.code == "missing_target"


def test_un_body_md_avec_mode_delete_est_REFUSE_pas_avale(seams):
    """La leçon générale de #461 : un argument qu'une surface ne sait pas honorer se
    refuse en le nommant. `delete` ne prend pas de contenu."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, section="10 August 2026",
                               mode="delete", body_md="du contenu"))
    assert e.value.code == "unexpected_body"
    assert seams["appels"] == []


def test_un_titre_dans_le_corps_du_preambule_est_REFUSE(seams):
    """Écrire un titre dans le préambule le referme : la région se rétrécirait toute
    seule et le patch du lendemain n'atteindrait plus le bandeau."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble",
                               body_md="# Un titre\n\ntexte"))
    assert e.value.code == "heading_in_preamble"
    assert "Un titre" in e.value.message
    assert seams["appels"] == []


def test_supprimer_un_preambule_absent_est_REFUSE(seams, monkeypatch):
    monkeypatch.setattr(D.db, "get_doc_by_id",
                        lambda i: _page("# T\n\nx\n", i))
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble", mode="delete"))
    assert e.value.status == 404 and e.value.code == "empty_preamble"


def test_supprimer_le_preambule_d_une_page_sans_aucun_titre_est_REFUSE(seams, monkeypatch):
    """Sans premier titre, « ce qui précède le premier titre » est la page entière :
    supprimer la viderait. Forme ambiguë ET destructrice → refus nommé, pas une devinette."""
    monkeypatch.setattr(D.db, "get_doc_by_id",
                        lambda i: _page("juste du texte, aucun titre.\n", i))
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="patch", doc_id=662, region="preamble", mode="delete"))
    assert e.value.code == "preamble_is_whole_page"
    assert seams["appels"] == []
