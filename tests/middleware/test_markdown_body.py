"""Un corps markdown se sert en markdown, pas en JSON échappé.

Le banc fait traverser la chaîne RÉELLE montée sur `_test_mcp()` : ce qu'on prouve est
autant l'ORDRE (la rédaction a tourné avant, l'en-tête ne rend pas un champ rédigé)
que le rendu.
"""
from __future__ import annotations

import asyncio
import json

from _mcp_app import static_mcp as _test_mcp

from fastmcp import Client, FastMCP
from oto.tools.common import FieldFilter

from oto_mcp import redaction
from oto_mcp.middleware import markdown_body

_CORPS = "# Titre\n\nUne « phrase » avec des \"guillemets\", un `code` et\n\n## Étape 1\nDeux lignes.\n"


def _banc(fn, nom: str = "fiche"):
    m = FastMCP("banc")
    for mw in _test_mcp().middleware:
        m.add_middleware(mw)
    m.tool(name=nom)(fn)
    return m


def _servir(m: FastMCP, nom: str = "fiche"):
    async def appel():
        async with Client(m) as c:
            return await c.call_tool(nom, {}, raise_on_error=False)
    r = asyncio.run(appel())
    return "".join(getattr(b, "text", "") for b in r.content), r.structured_content, r.is_error


def test_une_fiche_est_servie_en_markdown_et_garde_sa_structure():
    charge = {"slug": "p", "version": 7, "slots": [], "body_md": _CORPS, "note": None}

    def fiche() -> dict:
        return charge

    texte, structure, err = _servir(_banc(fiche))
    assert not err
    assert structure == charge, "le canal structuré ne bouge pas"
    entete, corps = texte.split("\n\n", 1)
    assert entete.splitlines() == ["slug: p", "version: 7", "slots: []"], entete
    assert corps == _CORPS
    assert "\\n" not in texte and '\\"' not in texte
    assert len(texte) < len(json.dumps(charge, ensure_ascii=False))


def test_un_dict_dont_le_corps_n_est_qu_un_champ_reste_en_json():
    charge = {"body_md": "court", "rows": [{"id": i, "nom": "x" * 20} for i in range(10)]}
    texte, structure, _ = _servir(_banc(lambda: charge))
    assert json.loads(texte) == charge


def test_sans_corps_ou_hors_dict_rien_ne_change():
    texte, _, _ = _servir(_banc(lambda: {"rows": [{"id": 1}], "count": 1}))
    assert json.loads(texte) == {"rows": [{"id": 1}], "count": 1}
    texte, structure, _ = _servir(_banc(lambda: [{"body_md": _CORPS}]))
    assert json.loads(texte) == [{"body_md": _CORPS}]


def test_une_erreur_n_est_pas_touchee():
    def fiche() -> dict:
        raise ValueError("boum")
    _, _, err = _servir(_banc(fiche))
    assert err


def test_la_redaction_tourne_avant_et_l_entete_ne_rend_pas_le_champ(monkeypatch):
    monkeypatch.setattr(redaction, "_resolve_field_filter",
                        lambda _s: FieldFilter(rules=[{"fields": ["secret"], "action": "drop"}]))

    def fiche() -> dict:
        return {"slug": "p", "secret": "NE-DOIT-PAS-SORTIR", "body_md": _CORPS}

    texte, structure, _ = _servir(_banc(fiche))
    assert "NE-DOIT-PAS-SORTIR" not in texte
    assert "secret" not in structure
    assert texte.startswith("slug: p\n\n# Titre")


def test_le_rendu_seul():
    assert markdown_body.rendu({"a": 1}) is None
    assert markdown_body.rendu({"body_md": "   "}) is None
    assert markdown_body.rendu({"body_md": "x" * 100, "meta": {"k": [1, 2]}, "n": None}) == (
        'meta: {"k":[1,2]}\n\n' + "x" * 100)
