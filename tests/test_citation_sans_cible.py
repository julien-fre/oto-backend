"""Une citation qui ne trouve rien se DIT, et l'asymétrie du graphe aussi (#611).

Signalé le 28/08 : une page citait six autres pages, en tableau ET en ligne de
liens ; aucune des six ne la voyait dans ses liens entrants, alors que le sens
inverse s'indexait correctement. Quatre hypothèses avaient été éprouvées et
écartées par l'auteur du signal (liens en cellule, réindexation partielle,
cohérence différée, cibles du même jour).

**La cause, reproduite ici le 03/09 sur le banc factice, est une ASYMÉTRIE de
portée** : une page de la **base de connaissance** résout ses `[[…]]` contre la
base SEULE, tandis qu'une page de **projet** résout contre `[projet, base]`. Une
page de la base ne peut donc jamais citer une page de projet ; l'inverse marche.
Le lien est à sens unique dans l'index et à double sens dans la prose — et aucune
réécriture ne le répare, ce qui explique qu'un `op=update` complet n'ait rien
changé.

⚠️ **Ce banc ne corrige PAS l'asymétrie, il la rend visible** — et c'est
délibéré : élargir la résolution à tous les projets d'une org ferait résoudre
« Start Here » n'importe où, et transformerait chaque écriture en scan de toute
l'org. Ce qui manquait n'était pas la portée, c'était de SAVOIR : le lien-souche
n'est stocké nulle part et n'était dit nulle part, donc la page se croyait citée.

Éprouvé rouge le 2026-09-03 : le relevé retiré ⟹ le deuxième test constate qu'une
écriture peut laisser six citations mortes sans un mot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oto_mcp.db import backlinks as B          # noqa: E402
from test_backlinks import _Conn               # noqa: E402

_ORG = {"owner_type": "org", "owner_id": "196", "context_org_id": None}
# La carte de tête vit dans la BASE (projet 900) ; ses six cibles dans un PROJET.
_DOCS = [
    {"id": 627, "project_id": 900, "title": "Company OS: Start Here"},
    {"id": 1191, "project_id": 100, "title": "Process Intelligence: Start Here"},
    {"id": 1196, "project_id": 100, "title": "Product: Start Here"},
]
_CORPS = "voir [[Product: Start Here]] et [[Process Intelligence: Start Here]]"


def test_l_asymetrie_est_REELLE_et_reproductible():
    """Le fait qui manquait au signal : ce n'est ni l'extraction ni l'indexation
    qui échoue, c'est la PORTÉE, et elle dépend d'où vit la page qui cite."""
    depuis_la_base = _Conn(project=_ORG, kb=900, docs=_DOCS)
    B.refresh_links(depuis_la_base, 627, 900, _CORPS)
    assert depuis_la_base.inserted == [], (
        "une page de la base ne voit pas les pages de projet — si ce jour arrive, "
        "c'est ce banc qu'il faut relire, pas le signal")

    depuis_le_projet = _Conn(project=_ORG, kb=900, docs=_DOCS)
    B.refresh_links(depuis_le_projet, 1196, 100, "voir [[Company OS: Start Here]]")
    assert depuis_le_projet.inserted == [(1196, 627)], (
        "le sens inverse, lui, s'indexe : c'est bien une asymétrie")


def test_l_ecriture_NOMME_les_citations_qui_ne_prennent_pas():
    """Le remède : un lien-souche n'est stocké nulle part, donc il doit être DIT
    au moment où il est écrit — le seul moment où son auteur peut agir."""
    trace: dict = {}
    conn = _Conn(project=_ORG, kb=900, docs=_DOCS)
    B.refresh_links(conn, 627, 900, _CORPS, trace)
    assert trace["citations_sans_cible"] == ["Product: Start Here",
                                             "Process Intelligence: Start Here"]
    hint = trace["citations_sans_cible_hint"].casefold()
    assert "aucun lien entrant" in hint, "il faut dire la CONSÉQUENCE, pas le fait"
    assert "hors de portée" in hint, "et la cause, sinon on cherche le mauvais défaut"


def test_le_releve_reste_VIDE_quand_tout_resout():
    trace: dict = {}
    conn = _Conn(project=_ORG, kb=900, docs=_DOCS)
    B.refresh_links(conn, 1196, 100, "voir [[Company OS: Start Here]]", trace)
    assert trace == {}, "pas de clé parasite dans une écriture normale"


def test_la_LIMITE_est_dite_dans_la_description_servie():
    """Sans elle, `backlinks` continue de passer pour un contrôle d'orphelin — et
    c'est précisément l'usage qui a produit le signal."""
    from oto_mcp.capabilities.docs import core as C
    prose = " ".join(c.description or "" for c in C.CAPABILITIES
                     if c.key.startswith("me.doc"))
    assert "not symmetric" in prose
    assert "orphan check" in prose
    assert "citations_sans_cible" in prose


def test_la_description_servie_ne_promet_plus_une_portee_UNIQUE():
    """L'autre moitié, signal #696 : la description jurait « that scope is the
    WHOLE reach ». Vrai de la RÉSOLUTION, faux du GRAPHE — `op=backlinks` rend
    aussi les liens stockés avant qu'une page ne change de projet, que plus
    aucune écriture ne referait. Un agent qui voyait ces liens entrants venus
    d'un autre projet en a conclu que l'avertissement d'écriture mentait, et a
    réécrit tous ses renvois inter-projets en clair, perdant la navigation.

    Le fait est mesuré sur vrai PostgreSQL par `test_backlinks.py`
    (`test_un_lien_STOCKE_survit_au_deplacement_de_sa_cible_et_reste_rendu`) :
    ici on exige seulement que la surface servie le DISE."""
    from oto_mcp.capabilities.docs import core as C
    prose = " ".join(c.description or "" for c in C.CAPABILITIES
                     if c.key.startswith("me.doc"))
    assert "WHOLE reach" not in prose, (
        "la portée de résolution n'est PAS la portée du graphe — c'est cette "
        "phrase-là qui a fait croire à une contradiction")
    assert "every STORED link whatever its project" in prose
    assert "MOVED between projects" in prose
    # …et le cran doit être dit du côté qui le SUBIT : celui qui déplace une page.
    assert "A move is NOT free for links" in prose
