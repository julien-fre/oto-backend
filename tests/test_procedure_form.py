"""La FORME d'une procédure : son schéma, requis.

Couvre : le test de présence (mêmes seuils que le `isDrawing` du front), le fait que
seuls les blocs NON TAGUÉS comptent (c'est le routeur du front qui en décide), le
régime non bloquant du check, sa remontée dans les deux faces d'écriture (org + équipe),
et les deux tripwires qui font que la CONSIGNE ne peut pas disparaître en silence :
le guide qui porte la grammaire, et la mention dans le socle injecté à chaque session.

⚠️ Le DIGEST d'ouverture, longtemps l'autre moitié de cette « forme », a été retiré le
10/09/2026 (oto#159) — son retrait est gardé par `test_digest_retire_159.py`.
"""
import asyncio
import pathlib

from oto_mcp import guide_store, instructions, procedure_diagram as pd
from oto_mcp.capabilities.groups import guide as gd
from oto_mcp.capabilities.orgs import instructions as oi

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
    """⚠️ La mutation doit viser ce que `diagram_check` APPELLE vraiment — depuis qu'il
    compte les dessins au lieu d'en tester la présence, c'est `compter_les_dessins`.
    Posée sur `has_diagram`, elle ne mordait plus : le test aurait viré au vert creux
    (il a viré au rouge, ce qui l'a signalé — mais le vert était le mode d'échec
    possible)."""
    monkeypatch.setattr(pd, "compter_les_dessins",
                        lambda body: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pd.diagram_check("peu importe") == {"diagram_warning": None}


def test_deux_dessins_sont_annonces():
    """La page n'en rend qu'un — le premier. Le cas se fabrique quand un corps garde le
    marqueur ET porte un dessin neuf : `avec_le_dessin` remet le tracé stocké à côté."""
    deux = _fenced(_DRAWING) + "\n```\n" + _DRAWING + "\n```\n"
    assert pd.compter_les_dessins(deux) == 2
    assert pd.has_diagram(deux), "deux dessins, c'est toujours « il y a un dessin »"
    assert pd.diagram_check(deux) == {"diagram_warning": pd.DOUBLE}
    # Un seul reste muet : la garde vise le SECOND bloc, pas la présence.
    assert pd.diagram_check(_fenced(_DRAWING)) == {"diagram_warning": None}


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
    monkeypatch.setattr(gd.org_store, "set_instruction", lambda *a, **k: 2)

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


def test_the_base_guide_still_asks_for_the_drawing():
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
    from oto_mcp.capabilities import guide_library as dl

    # Seul un super_admin publie ; le rôle se pose à sa source, d'où dérivent les deux
    # prédicats plateforme.
    monkeypatch.setattr(dl.access, "get_user_role", lambda sub: "super_admin")
    monkeypatch.setattr(dl, "_require_org_admin", lambda ctx, verb: 7)
    monkeypatch.setattr(dl, "_author_for", lambda ctx: ("otomata", None, "Otomata"))
    monkeypatch.setattr(dl.org_store, "get_instruction",
                        lambda otype, oid, slug: {"body_md": "Sans dessin.", "title": "t",
                                                  "description": "d", "slots": []})
    monkeypatch.setattr(dl.org_store, "publish_guide",
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
