"""Le garde-fou anti-silence, et la preuve qu'il MORD.

`scripts/lint_silences.py` refuse un `except` large qui ne re-lève pas, ne journalise
pas et ne rend pas de refus nommé — le patron qui transforme une panne en succès. Il
est né de l'inventaire du 2026-08-27 (`docs/silences-2026-08-27.md`) : 333 handlers
qui n'atteignaient jamais un `raise`, dont dix produisaient un défaut cher.

⚠️ **Un garde-fou d'inventaire se prouve en lui présentant l'anomalie qu'il prétend
attraper** (convention du repo, née de trois bancs qui mentaient par omission en
août). D'où les cas synthétiques ci-dessous : chacun décrit une forme précise, et le
test échouerait si le garde-fou cessait de la voir OU se mettait à crier à tort.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from scripts.lint_silences import scanner  # noqa: E402


def _scan(source: str, tmp_path: pathlib.Path) -> list:
    f = tmp_path / "cas.py"
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    return list(scanner(f))


# ── ce qu'il DOIT attraper ───────────────────────────────────────────────────

@pytest.mark.parametrize("corps", [
    "pass",
    "return None",
    "return {}",
    "x = None",
    "print('raté')",            # un print n'est pas une trace de service
    "self.log.info('x')",       # un logger non reconnu ne compte pas
    "return JSONResponse({})",  # une réponse NUE ne dit pas si elle refuse
])
def test_il_mord_sur_un_silence(corps, tmp_path):
    silences = _scan(f"""
        def f():
            try:
                risque()
            except Exception:
                {corps}
        """, tmp_path)
    assert len(silences) == 1, f"silence non attrapé : {corps}"


@pytest.mark.parametrize("clause", ["except:", "except Exception:",
                                    "except BaseException:",
                                    "except (Exception, OSError):",
                                    "except Exception as e:"])
def test_il_mord_sur_toutes_les_formes_de_catch_all(clause, tmp_path):
    assert len(_scan(f"""
        def f():
            try:
                risque()
            {clause}
                pass
        """, tmp_path)) == 1


def test_un_except_etroit_n_est_pas_son_affaire(tmp_path):
    """Attraper `ValueError` est une décision, pas un filet : le garde-fou ne s'en
    mêle pas — sinon il crierait partout et finirait ignoré."""
    assert _scan("""
        def f():
            try:
                risque()
            except ValueError:
                pass
        """, tmp_path) == []


def test_un_noqa_SILENT_sans_raison_ne_sauve_pas(tmp_path):
    """L'échappatoire est NOMINATIVE : sans raison, elle deviendrait le chemin par
    défaut et le garde-fou ne vaudrait plus rien."""
    assert len(_scan("""
        def f():
            try:
                risque()
            except Exception:  # noqa: SILENT
                pass
        """, tmp_path)) == 1


# ── ce qu'il doit LAISSER PASSER ─────────────────────────────────────────────

@pytest.mark.parametrize("corps", [
    "raise",
    "raise MonErreur('nommée')",
    "logger.warning('raté', exc_info=True)",
    "_log.exception('raté')",
    "logging.getLogger(__name__).exception('raté')",
    "return json_error(request, 400, 'invalid_json')",
    "return _json_error(request, 400, 'invalid_json')",
    "return None, _json_error(request, 400, 'invalid_multipart')",
])
def test_un_handler_qui_parle_passe(corps, tmp_path):
    assert _scan(f"""
        def f():
            try:
                risque()
            except Exception:
                {corps}
        """, tmp_path) == []


def test_il_voit_ce_qui_parle_au_fond_d_une_branche(tmp_path):
    """Un `raise` conditionnel compte : le handler n'est pas muet, il trie."""
    assert _scan("""
        def f():
            try:
                risque()
            except Exception as e:
                if fatal(e):
                    raise
        """, tmp_path) == []


@pytest.mark.parametrize("placement", [
    "except Exception:  # noqa: SILENT — parce que",
    "except Exception:  # noqa: BLE001, SILENT — parce que",
])
def test_la_declaration_sur_la_ligne_du_except(placement, tmp_path):
    assert _scan(f"""
        def f():
            try:
                risque()
            {placement}
                pass
        """, tmp_path) == []


def test_la_declaration_juste_au_dessus_du_except(tmp_path):
    """Le placement retenu pour l'existant : la raison mérite plus de place qu'une
    fin de ligne, et à l'indentation du `except` elle s'y rattache sans ambiguïté."""
    assert _scan("""
        def f():
            try:
                risque()
            # noqa: SILENT — la raison, à l'indentation du except
            except Exception:
                pass
        """, tmp_path) == []


# ── le VRAI code servi ───────────────────────────────────────────────────────

def test_le_code_servi_ne_porte_aucun_silence_non_declare():
    """Le garde-fou s'exerce sur `oto_mcp/` entier, pas sur un échantillon."""
    silences = list(scanner(RACINE / "oto_mcp"))
    assert silences == [], "\n".join(str(s) for s in silences)


def test_le_script_est_utilisable_a_la_main():
    """Il tourne aussi hors pytest — c'est ainsi qu'on l'emploie pour trier un lot."""
    r = subprocess.run([sys.executable, "scripts/lint_silences.py", "oto_mcp"],
                       cwd=RACINE, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
