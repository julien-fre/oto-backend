"""La FORME d'une procédure : son digest d'ouverture et son schéma, tous deux requis.

Couvre : le test de présence (mêmes seuils que le `isDrawing` du front), le fait que
seuls les blocs NON TAGUÉS comptent (c'est le routeur du front qui en décide), le
régime non bloquant du check, sa remontée dans les deux faces d'écriture (org + équipe),
et les deux tripwires qui font que la CONSIGNE ne peut pas disparaître en silence :
le guide qui porte la grammaire, et la mention dans le socle injecté à chaque session.
"""
import asyncio
import pathlib

from oto_mcp import (guide_store, instructions, procedure_diagram as pd,
                     procedure_digest as pdg)
from oto_mcp.capabilities import groups_doctrine as gd
from oto_mcp.capabilities import orgs_instructions as oi

_GUIDE = pathlib.Path(__file__).resolve().parents[1] / "oto_mcp" / "guides" / "procedure-flowchart.md"

# Le dessin de référence — extrait du guide lui-même, pour qu'un test ne puisse pas
# passer sur un dessin que la doc ne montre pas (et inversement).
_DRAWING = """\
              Natural language input in Claude
              "Wholesale distributors in the East Bay, 200 employees. Source 40 accounts."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  0 · Find companies on Apollo                   │   apollo_search_organizations
│  Search, then filter by industry code.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Send the email                             │
│  Enrol the person in the matching sequence.     │
└─────────────────────────────────────────────────┘"""


def _fenced(block: str, lang: str = "") -> str:
    return f"Une procédure.\n\n```{lang}\n{block}\n```\n\n## Phase 1\n"


# ── Présence du dessin ──────────────────────────────────────────────────────
def test_a_real_drawing_is_found():
    assert pd.is_drawing(_DRAWING)
    assert pd.has_diagram(_fenced(_DRAWING))
    assert pd.diagram_check(_fenced(_DRAWING))["diagram_warning"] is None


def test_no_fence_at_all_warns():
    assert pd.diagram_check("Une procédure sans le moindre bloc.")["diagram_warning"] == pd.WARNING


def test_a_tagged_block_is_never_a_drawing():
    """Le routeur du front ne dessine QUE les blocs non tagués : compter un ```text
    plein de caractères de tracé serait un faux positif silencieux."""
    assert not pd.has_diagram(_fenced(_DRAWING, lang="text"))
    assert pd.diagram_check(_fenced(_DRAWING, lang="text"))["diagram_warning"] == pd.WARNING


def test_thresholds_match_the_front():
    # Assez de glyphes mais sur 2 lignes seulement → pas un dessin.
    two_lines = "─" * 30 + "\n" + "─" * 30
    assert not pd.is_drawing(two_lines)
    # Assez de lignes mais pas assez de glyphes → pas un dessin non plus.
    three_thin = "│ a\n│ b\n│ c"
    assert not pd.is_drawing(three_thin)
    # Le plancher exact, des deux côtés.
    assert pd.is_drawing("\n".join(["─" * 7] * 3))       # 3 lignes, 21 glyphes
    assert not pd.is_drawing("\n".join(["─" * 6] * 3))   # 3 lignes, 18 glyphes


def test_a_shell_sample_with_a_stray_arrow_is_not_a_drawing():
    sample = "cat f | grep x   # ─\nls -l\necho ▶"
    assert not pd.is_drawing(sample)


def test_the_check_never_raises(monkeypatch):
    monkeypatch.setattr(pd, "has_diagram",
                        lambda body: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pd.diagram_check("peu importe") == {"diagram_warning": None}


# ── Remontée dans les faces d'écriture ──────────────────────────────────────
class _Ctx:
    sub = "u1"
    org_id = 7


class _Inp:
    slug = "ma-procedure"
    title = None
    description = None
    from_version = None
    slots = None
    org = None

    def __init__(self, body_md):
        self.body_md = body_md


def _set_org(monkeypatch, body):
    monkeypatch.setattr(oi.org_store, "set_instruction",
                        lambda *a, **k: 3)
    monkeypatch.setattr(oi.org_store, "get_instruction", lambda *a, **k: {"slots": []})

    async def _wc(body_md, **k):
        return {"referenced_tools": [], "unresolved_tools": []}
    monkeypatch.setattr(oi.tool_registry, "write_check", _wc)
    return asyncio.run(oi._set_instruction(_Ctx(), _Inp(body)))


def test_org_set_surfaces_the_warning(monkeypatch):
    out = _set_org(monkeypatch, "Une procédure sans dessin.")
    assert out["diagram_warning"] == pd.WARNING
    assert out["ok"] is True and out["version"] == 3   # non bloquant : l'écriture a eu lieu


def test_org_set_is_silent_when_the_drawing_is_there(monkeypatch):
    out = _set_org(monkeypatch, _fenced(_DRAWING))
    assert out["diagram_warning"] is None


def test_group_set_surfaces_the_warning(monkeypatch):
    """Une procédure d'équipe est une procédure : même exigence, même régime."""
    monkeypatch.setattr(gd.group_store, "set_group_instruction", lambda *a, **k: 2)

    class _GInp(_Inp):
        group_id = 4

    out = gd._set(_Ctx(), _GInp("Une procédure d'équipe sans dessin."))
    assert out["diagram_warning"] == pd.WARNING
    assert gd._set(_Ctx(), _GInp(_fenced(_DRAWING)))["diagram_warning"] is None


def test_written_models_declare_the_field():
    assert "diagram_warning" in oi.InstructionWritten.model_fields
    assert "diagram_warning" in gd.GroupInstructionWritten.model_fields


# ── Tripwires : la consigne ne peut pas disparaître en silence ──────────────
def test_the_guide_ships_and_its_example_passes_our_own_gate():
    """Le guide qui PORTE la grammaire doit lui-même montrer un dessin que la garde
    accepte — sinon la doc prescrit ce que le serveur signale."""
    assert _GUIDE.is_file()
    seeds = {g["slug"]: g for g in guide_store.list_file_guides()}
    assert "procedure-flowchart" in seeds
    seed = seeds["procedure-flowchart"]
    assert seed["title"] and seed["description"]
    # Sur le corps PARSÉ (ce que l'agent recevra), pas sur le fichier brut.
    assert pd.has_diagram(seed["body_md"])


def test_the_guide_states_the_density_limits():
    """Les bornes de densité ne vivent QUE dans le guide : le check serveur ne les voit
    pas (il faudrait le parseur du front pour savoir ce qu'est un « détail »). Si elles
    tombent d'ici, plus rien ne les porte."""
    body = _GUIDE.read_text(encoding="utf-8")
    for token in ("~40", "~80", "~60", "~35", "~50", "note de marge"):
        assert token in body, token


def test_the_base_doctrine_still_asks_for_the_drawing():
    """Le socle injecté à chaque session est le seul endroit où l'agent apprend que
    le dessin est requis AVANT d'écrire. S'il tombe, plus personne ne dessine."""
    socle = instructions._SECRET_SAUCE
    assert "procedure-flowchart" in socle
    assert "diagram_warning" in socle


def test_the_set_tool_description_names_the_guide():
    from oto_mcp.capabilities import procedure_console
    from oto_mcp.capabilities.registry import CAPABILITIES

    caps = {c.key: c for c in CAPABILITIES}
    assert "procedure-flowchart" in (caps["org.procedure.console"].description or "")
    assert "procedure-flowchart" in (caps["org.instruction.set"].description or "")
    assert procedure_console  # l'import monte la console


def test_publish_and_fork_carry_the_warning(monkeypatch):
    """Publier ou forker, c'est faire circuler une procédure : le manque de schéma
    part avec elle, donc le signal aussi."""
    from oto_mcp.capabilities import doctrine_library as dl

    monkeypatch.setattr(dl, "_require_org_admin", lambda ctx, verb: 7)
    monkeypatch.setattr(dl, "_author_for", lambda ctx: ("org", 7, "Acme"))
    monkeypatch.setattr(dl.org_store, "get_instruction",
                        lambda org, slug: {"body_md": "Sans dessin.", "title": "t",
                                           "description": "d", "slots": []})
    monkeypatch.setattr(dl.org_store, "publish_doctrine",
                        lambda **k: {"id": 1, "slug": "s", "version": 1, "visibility": "public"})

    class _P:
        slug = "s"; public_slug = None; title = None; description = None
        category = None; tags = None; visibility = "public"

    assert dl._publish(_Ctx(), _P())["diagram_warning"] == pd.WARNING

    monkeypatch.setattr(dl.org_store, "get_library_entry",
                        lambda **k: {"id": 1, "body_md": _fenced(_DRAWING)})
    monkeypatch.setattr(dl.org_store, "fork_into_org",
                        lambda **k: {"org_id": 7, "slug": "s", "version": 1,
                                     "forked_from": 1, "source_title": "t"})

    class _F:
        slug = "s"; new_slug = None

    assert dl._fork(_Ctx(), _F())["diagram_warning"] is None


# ── Le digest d'ouverture ───────────────────────────────────────────────────
_DIGEST = "> **Self-improvement digest** — Never run end to end; nothing to report yet."


def test_the_digest_may_follow_a_leading_title_heading():
    """La page du process RETIRE un H1 qui répète le titre et affiche le sien : le
    digest posé dessous est donc bien la première chose que le lecteur voit."""
    assert pdg.has_digest(f"# Ma procédure\n\n{_DIGEST}\n\nDe la prose.\n")


def test_the_digest_may_be_the_very_first_block():
    assert pdg.has_digest(f"{_DIGEST}\n\n## Goal\n\nDe la prose.\n")


def test_a_digest_that_is_not_the_opening_block_does_not_count():
    """« Quelque part dans le corps » n'est pas la consigne — c'est l'OUVERTURE."""
    assert not pdg.has_digest(f"# T\n\nDe la prose.\n\n{_DIGEST}\n")
    assert not pdg.has_digest(f"## Goal\n\n{_DIGEST}\n")
    # Un seul H1 est sauté : deux titres, ou un titre collé à de la prose, ne le sont pas.
    assert not pdg.has_digest(f"# T\n\n## Goal\n\n{_DIGEST}\n")
    assert not pdg.has_digest(f"# T\nDe la prose.\n\n{_DIGEST}\n")


def test_an_ordinary_blockquote_is_not_a_digest():
    assert not pdg.has_digest("# T\n\n> Une citation quelconque.\n")


def test_the_digest_check_never_raises(monkeypatch):
    monkeypatch.setattr(pdg, "has_digest",
                        lambda body: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pdg.digest_check("peu importe") == {"digest_warning": None}


def test_every_write_face_surfaces_the_digest_warning(monkeypatch):
    """Les quatre faces qui écrivent ou font circuler une procédure le signalent."""
    out = _set_org(monkeypatch, _fenced(_DRAWING))          # dessin OK, digest absent
    assert out["digest_warning"] == pdg.WARNING and out["diagram_warning"] is None
    out = _set_org(monkeypatch, f"# T\n\n{_DIGEST}\n\n{_fenced(_DRAWING)}")
    assert out["digest_warning"] is None and out["diagram_warning"] is None

    monkeypatch.setattr(gd.group_store, "set_group_instruction", lambda *a, **k: 2)

    class _GInp(_Inp):
        group_id = 4

    assert gd._set(_Ctx(), _GInp("Sans digest."))["digest_warning"] == pdg.WARNING
    for model in (oi.InstructionWritten, oi.InstructionReverted, gd.GroupInstructionWritten):
        assert "digest_warning" in model.model_fields, model


def test_the_guide_and_the_socle_carry_the_opening_rule():
    body = _GUIDE.read_text(encoding="utf-8")
    assert "Self-improvement digest" in body
    # La règle de placement vient du rendu : si la raison tombe, la consigne devient
    # arbitraire et le premier relecteur la « simplifiera ».
    assert "retire" in body and "At a glance" in body
    socle = instructions._SECRET_SAUCE
    assert "Self-improvement digest" in socle and "digest_warning" in socle

