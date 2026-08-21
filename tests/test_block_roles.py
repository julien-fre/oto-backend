"""L'étiquetage des blocs (lot ⑦) — un RÔLE, jamais un `type`, et zéro rotation d'id.

Deux propriétés, et la seconde est celle qui coûtait cher :

1. le rôle de présentation descend en PROPRIÉTÉ du bloc (0054-D2 : `type` = le support),
   avec les puces d'une liste déjà extraites ;
2. **ré-étiqueter un corps inchangé ne fait tourner AUCUN identifiant** — c'est ce que
   la clé de rapprochement sur la source seule achète, et c'est ce qui lève l'interdit
   d'ancrage promis au front.
"""
import pytest

from oto_mcp.db.blocks import CODE, TEXT, parse_blocks


def _roles(md: str) -> list:
    return [(b["type"], b.get("role"), b.get("items")) for b in parse_blocks(md)]


# ── Le rôle est une propriété, et il ne ment pas ───────────────────────────────
def test_un_titre_une_prose_une_liste_recoivent_leur_role():
    assert _roles("# Titre\n\nDu texte.\n\n- un\n- deux\n") == [
        (TEXT, "heading", None),
        (TEXT, "paragraph", None),
        (TEXT, "list", ["un", "deux"]),
    ]


def test_une_liste_NUMEROTEE_est_une_liste_avec_ses_puces():
    assert _roles("1. premier\n2. second\n") == [(TEXT, "list", ["premier", "second"])]


@pytest.mark.parametrize("md", ["| a | b |\n|---|---|\n", "> une citation\n",
                                "du texte\n- puis une puce\n"])
def test_ce_qu_on_ne_SAIT_PAS_classer_ne_recoit_AUCUN_role(md):
    """Un `paragraph` posé par défaut mentirait.

    Tableau, citation, prose mêlée de puces : l'absence de rôle dit « on ne classe pas »,
    et le front rend la source comme il l'entend. C'est possible parce que son `type` est
    une chaîne libre et son objet ouvert — précisément pour ce cas.
    """
    (_, role, items), = _roles(md)
    assert role is None and items is None


def test_le_ROLE_n_est_JAMAIS_une_valeur_de_type():
    # 0054-D2 : `type` dit le SUPPORT. En faire porter la présentation rouvrirait le
    # second axe qu'on a demandé au front d'éviter, et entrerait en collision le jour
    # où `image` et `référence` arrivent.
    for t, role, _ in _roles("# T\n\nprose\n\n- a\n\n```py\nx=1\n```\n"):
        assert t in (TEXT, CODE)
        assert role in (None, "heading", "paragraph", "list")


def test_un_bloc_de_code_ne_recoit_pas_de_role():
    (t, role, _), = _roles("```py\nx=1\n```\n")
    assert t == CODE and role is None


def test_ordered_n_est_PAS_servi_et_c_est_deliberе():
    """Chez le front, `ordered` = « ce bloc est UN PAS d'une suite numérotée », les blocs
    consécutifs formant un même `<ol>` — donc N blocs, N identifiants ancrables. Notre
    parse garde une liste markdown dans UN bloc : lui coller `ordered` mettrait deux
    notions sous un même nom.
    """
    assert all("ordered" not in b for b in parse_blocks("1. a\n2. b\n"))


# ── L'invariant du parse survit à l'étiquetage ─────────────────────────────────
def test_la_concatenation_rend_le_corps_au_CARACTERE_pres():
    md = "# T\n\nprose\n\n- a\n- b\n\n```py\nx=1\n```\n\n| a |\n"
    assert "".join(b["md"] for b in parse_blocks(md)) == md


# ── La propriété qui lève l'interdit d'ancrage ─────────────────────────────────
def test_re_etiqueter_un_corps_INCHANGE_ne_fait_tourner_AUCUN_id():
    """La preuve que l'étiquetage est gratuit pour les références externes.

    On rejoue le rapprochement de `write_node_blocks` (clé = SOURCE SEULE) entre des
    blocs « d'avant » — sans rôle, comme ceux déjà en base — et le parse d'aujourd'hui,
    qui en pose un. Avec le type dans la clé, ces identités auraient toutes tourné pour
    un texte que personne n'avait touché.
    """
    md = "# Titre\n\nprose\n\n- a\n- b\n"
    avant = [{"public_id": f"blk_{i}", "md": b["md"]}
             for i, b in enumerate(parse_blocks(md))]

    dispo: dict = {}
    for r in avant:
        dispo.setdefault(r["md"] or "", []).append(r["public_id"])

    apres = []
    for b in parse_blocks(md):
        reconnus = dispo.get(b["md"])
        apres.append(reconnus.pop(0) if reconnus else "NEUF")

    assert apres == [r["public_id"] for r in avant]
    assert "NEUF" not in apres


def test_deux_blocs_IDENTIQUES_sont_departages_par_l_ordre():
    # Le seul cas ambigu de la clé source-seule, et il était déjà couvert.
    md = "même\n\nmême\n"
    blocs = parse_blocks(md)
    dispo: dict = {}
    for i, b in enumerate(blocs):
        dispo.setdefault(b["md"], []).append(f"blk_{i}")
    assert [dispo[b["md"]].pop(0) for b in blocs] == ["blk_0", "blk_1"]
