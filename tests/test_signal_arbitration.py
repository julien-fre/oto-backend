"""L'arbitrage d'un signal d'usage : quatre états, pas deux (#450).

**Le défaut fermé ici est un défaut de VOCABULAIRE, pas de code.** Le modèle
d'origine n'avait que « ouvert » et « traité ». Il ne savait donc dire ni « je l'ai
lu, je ne sais pas encore quoi en faire » — l'état où vit l'essentiel d'une pile de
retours — ni « je ne le ferai pas ». Deux conséquences mesurées le 27/08, sur 534
signaux reçus depuis le 19/06 :

- **203 ouverts, dont 125 de plus d'une semaine**, sans qu'on puisse distinguer ce
  que personne n'a lu de ce qu'on a lu sans trancher ;
- **zéro arbitrage depuis le 16/08**, pendant que 118 signaux arrivaient — un stock
  où le refus est indicible ne peut que monter, puisque rien n'en sort sans travail.

Un compteur qui mélange le retard et le désaccord ne se lit plus, donc on cesse de
le lire. C'est ce qui rend la boucle d'usage muette : elle enregistre toujours, mais
plus personne n'en tire de décision.
"""
import pytest

from oto_mcp.capabilities import usage as cap
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.db import usage as db_usage

CTX = ResolvedCtx(sub="op-1", org_id=1)


# ── la lecture ────────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, sink, rows): self._sink, self._rows = sink, rows
    def execute(self, sql, params=None):
        self._sink.setdefault("calls", []).append((sql, params))
        return _Cur(self._rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _wire(monkeypatch, rows=()):
    sink = {}
    monkeypatch.setattr(db_usage, "_connect", lambda: _Conn(sink, list(rows)))
    return sink


def test_le_filtre_lit_la_colonne_jamais_la_date(monkeypatch):
    """⚠️ La régression qu'on ne verrait pas : dériver l'état de `resolved_at IS NULL`.
    Depuis qu'un REFUS porte lui aussi une date d'arbitrage, cette dérivation rendrait
    un signal refusé indistinguable d'un signal traité — le compteur mentirait dans le
    sens qui arrange, et on croirait la pile plus saine qu'elle n'est."""
    sink = _wire(monkeypatch)
    db_usage.list_usage_signals(status="declined")
    sql, params = sink["calls"][0]

    assert "s.status = %s" in sql and "declined" in params
    assert "resolved_at IS NULL" not in sql and "resolved_at IS NOT NULL" not in sql


def test_pending_est_un_filtre_pas_un_etat(monkeypatch):
    """« Ce qui reste à arbitrer » est LA question qu'on pose en ouvrant la pile, et
    depuis qu'il y a quatre états, `open` seul n'y répond plus."""
    sink = _wire(monkeypatch)
    db_usage.list_usage_signals(status=db_usage.SIGNAL_PENDING)
    sql, params = sink["calls"][0]

    assert "s.status <> ALL(%s)" in sql
    assert list(db_usage.SIGNAL_TERMINAL) in params
    assert db_usage.SIGNAL_PENDING not in db_usage.SIGNAL_STATUSES


def test_un_refus_est_un_arbitrage_donc_il_sort_de_la_pile():
    """Si `declined` restait « à traiter », refuser ne servirait à rien : la pile ne
    baisserait pas, et on aurait ajouté un mot sans changer le problème."""
    assert "declined" in db_usage.SIGNAL_TERMINAL
    assert "resolved" in db_usage.SIGNAL_TERMINAL
    assert "acknowledged" not in db_usage.SIGNAL_TERMINAL
    assert "open" not in db_usage.SIGNAL_TERMINAL


# ── l'écriture ────────────────────────────────────────────────────────────────

def test_rouvrir_efface_l_arbitrage(monkeypatch):
    """Un signal remis dans la pile n'a plus été arbitré. Garder l'ancienne note
    ferait lire une décision qui n'a plus cours — et c'est la note qu'on relit en
    premier pour savoir quoi faire."""
    sink = _wire(monkeypatch, rows=[{"id": 1, "status": "open"}])
    db_usage.set_usage_signal_status(1, status="open", by="op-1", note="ignorée")
    sql, _ = sink["calls"][0]

    assert "resolved_at = NULL" in sql and "resolved_by = NULL" in sql
    assert "resolution = NULL" in sql


def test_tout_autre_etat_pose_l_arbitrage(monkeypatch):
    for etat in ("acknowledged", "declined", "resolved"):
        sink = _wire(monkeypatch, rows=[{"id": 1, "status": etat}])
        db_usage.set_usage_signal_status(1, status=etat, by="op-1", note="vu")
        sql, params = sink["calls"][0]
        assert "resolved_at = NOW()" in sql, etat
        assert etat in params and "op-1" in params, etat


def test_un_etat_inconnu_ne_s_ecrit_pas(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(ValueError) as e:
        db_usage.set_usage_signal_status(1, status="en_cours", by="op-1")
    assert "en_cours" in str(e.value)


# ── la règle de surface ───────────────────────────────────────────────────────

def test_refuser_sans_motif_est_refuse():
    """Sans motif, un refus est indistinguable d'un oubli : la pile redeviendrait un
    endroit où des signaux disparaissent sans qu'on sache pourquoi — le défaut qu'on
    ferme, sous un autre nom."""
    with pytest.raises(AuthzDenied) as e:
        cap._set_signal_status(CTX, cap.SetSignalStatusInput(
            signal_id=1, status="declined"))
    assert e.value.code == "missing_note"

    with pytest.raises(AuthzDenied):
        cap._set_signal_status(CTX, cap.SetSignalStatusInput(
            signal_id=1, status="declined", note="   "))


def test_traiter_n_exige_aucun_motif(monkeypatch):
    """Le travail livré parle de lui-même ; exiger une note ferait écrire « fait »
    deux cents fois, et une note qu'on écrit par obligation n'est plus lue."""
    monkeypatch.setattr(cap.db, "set_usage_signal_status",
                        lambda *a, **k: {"id": 1, "status": "resolved"})
    monkeypatch.setattr(cap.db, "count_usage_signals_by_status", lambda: {})
    out = cap._set_signal_status(CTX, cap.SetSignalStatusInput(
        signal_id=1, status="resolved"))
    assert out["ok"] is True


def test_un_filtre_de_statut_inconnu_est_nomme(monkeypatch):
    """Le silence sur un filtre non interprété rend 0 ligne — indiscernable d'une pile
    vide. C'est la famille #347 : ce qu'une surface ne sait pas lire, elle le REFUSE."""
    with pytest.raises(AuthzDenied) as e:
        cap._signals(CTX, cap.SignalsInput(status="ouvert"))
    assert e.value.code == "unknown_status" and "ouvert" in e.value.message


def test_la_liste_rend_les_comptes_de_toute_la_table(monkeypatch):
    """Une page de 200 lignes ne dit pas si la pile en compte 203 ou 2 000 — et c'est
    ce chiffre qu'on vient chercher en ouvrant la liste."""
    monkeypatch.setattr(cap.db, "list_usage_signals", lambda *a, **k: [])
    monkeypatch.setattr(cap.db, "count_usage_signals_by_status",
                        lambda: {"open": 71, "acknowledged": 132, "declined": 0,
                                 "resolved": 331, "pending": 203})
    out = cap._signals(CTX, cap.SignalsInput())
    assert out["counts"]["pending"] == 203


def test_les_etats_a_zero_figurent_dans_les_comptes(monkeypatch):
    """Un état absent de la réponse se lit « pas encore implémenté », pas « personne
    ne l'a utilisé » — et `declined` sera longtemps à zéro."""
    sink = _wire(monkeypatch, rows=[{"status": "open", "n": 3}])
    comptes = db_usage.count_usage_signals_by_status()

    assert comptes == {"open": 3, "acknowledged": 0, "declined": 0, "resolved": 0,
                       "pending": 3}
    assert sink  # la requête a bien eu lieu
