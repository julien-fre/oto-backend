"""Lentille REST scopable (#451) — `db.rest_call_stats`, au ras du SQL.

La console passait `sub`/`org_id` à un `WindowInput(days=…)` : le filtre mourait
avant la requête, et la réponse — celle de TOUTE la plateforme — était indiscernable
de celle d'un compte. Le SQL de la lentille se construit maintenant par morceaux ;
deux requêtes partagent la clause, donc chacune doit porter ses paramètres, et
l'arithmétique placeholders/params est la seule preuve locale qu'aucune n'en a un de
trop (l'erreur ne se voit sinon qu'en prod, sur un `%s` orphelin).
"""
from oto_mcp.db import usage


class _Cur:
    def fetchone(self):
        return {"total": 0, "errors": 0, "users": 0}

    def fetchall(self):
        return []


class _Conn:
    """Enregistre CHAQUE requête de la lentille (elle en fait deux)."""

    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params):
        self.sink.append((sql, params))
        return _Cur()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, **kw):
    vues: list = []
    monkeypatch.setattr(usage, "_connect", lambda: _Conn(vues))
    return usage.rest_call_stats(**kw), vues


def test_sans_filtre_la_lentille_reste_plateforme(monkeypatch):
    out, vues = _run(monkeypatch, since_days=7)
    assert len(vues) == 2
    for sql, _ in vues:
        assert "sub = %s" not in sql and "org_id = %s" not in sql
    # Rien n'a été restreint : rien à annoncer (le contrat du dashboard ne bouge pas).
    assert "filters" not in out and "org_id_caveat" not in out


def test_les_deux_requetes_portent_le_filtre_et_ses_parametres(monkeypatch):
    """Les totaux et la ventilation par route sont DEUX requêtes : un filtre posé
    sur une seule rendrait un total d'un compte et des routes de la plateforme —
    incohérence muette, plus trompeuse que l'absence de filtre."""
    out, vues = _run(monkeypatch, since_days=30, org_id=42, sub="sub-jane")

    assert len(vues) == 2
    for sql, params in vues:
        assert "org_id = %s" in sql and "sub = %s" in sql
        assert sql.count("%s") == len(params), f"placeholders ≠ params : {params}"
        assert list(params) == [30, 42, "sub-jane"]   # l'ordre est celui des clauses
    assert out["filters"] == {"org_id": 42, "sub": "sub-jane"}


def test_le_scope_par_org_dit_ce_qu_il_laisse_dehors(monkeypatch):
    """`tool_calls.org_id` d'une ligne REST vient d'un EN-TÊTE de consultation
    (best-effort, `RestCallLogger`), pas d'une résolution : une requête sans cet
    en-tête ne porte aucune org et sort du filtre. Sans ce mot, on remplace une
    lecture trop large par un zéro trop étroit — le même défaut, à l'envers."""
    out, _ = _run(monkeypatch, since_days=7, org_id=42)
    assert "en-tête" in out["org_id_caveat"] and "0" in out["org_id_caveat"]

    out, _ = _run(monkeypatch, since_days=7, sub="sub-jane")
    assert "org_id_caveat" not in out          # rien à nuancer sans scope d'org
