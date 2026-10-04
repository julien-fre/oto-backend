"""La population de `scripts/repointer_residus_alias.py` (#439) : qui est repris, qui
est ÉCARTÉ et nommé — jamais un repointage deviné."""
from __future__ import annotations

import pathlib
import sys

import pytest

from oto_mcp import db
from oto_mcp.db.sub_aliases import AliasNonResolvable

# La racine du dépôt n'est pas dans `sys.path` en CI (pas de `__init__.py`) : `scripts`
# ne s'importait que parce qu'un banc collecté avant l'y avait mise (oto#116).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts import repointer_residus_alias as script  # noqa: E402


class _Res:
    def __init__(self, rows=None, one=None):
        self._rows, self._one = rows or [], one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _Conn:
    """`sub_aliases` + `users` en mémoire — seules les lectures de `population`."""

    def __init__(self, aliases, users):
        self.aliases, self.users = aliases, users   # {old: new}, {sub: suspended_at}

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT a.old_sub, EXISTS"):
            return _Res([{"old_sub": o, "vivant": o in self.users}
                         for o in sorted(self.aliases)])
        if sql.startswith("SELECT a.old_sub FROM sub_aliases a JOIN users"):
            return _Res([{"old_sub": o} for o in sorted(self.aliases) if o in self.users])
        if sql.startswith("SELECT suspended_at FROM users"):
            s = params[0]
            return _Res(one={"suspended_at": self.users[s]} if s in self.users else None)
        raise AssertionError(f"SQL imprévu : {sql}")


def test_population_reprend_les_fusionnes_et_nomme_les_ecartes(monkeypatch):
    conn = _Conn(
        aliases={"a-fusionne": "a-canon", "b-recree": "b-canon",
                 "c-casse": "c-canon", "d-pause": "d-canon"},
        users={"a-canon": None, "b-recree": None, "b-canon": None,
               "d-canon": "2026-09-01"})

    def resolve(sub):
        if sub == "c-casse":
            raise AliasNonResolvable(sub, "compte_disparu", "plus de compte")
        return conn.aliases[sub]

    monkeypatch.setattr(db, "resolve_sub", resolve)
    paires, ecartes = script.population(conn)
    assert paires == [("a-fusionne", "a-canon")]
    texte = "\n".join(ecartes)
    assert "b-recree : ancien identifiant RECRÉÉ" in texte
    assert "c-casse : chaîne non résolvable (compte_disparu)" in texte
    assert "d-pause → d-canon : compte canonique en pause" in texte


@pytest.mark.parametrize("colonne", [("tool_calls", "sub"), ("tool_calls", "effective_sub"),
                                     ("tool_calls", "view_as_sub"),
                                     ("user_account_profile", "sub")])
def test_le_constat_couvre_le_journal_et_le_profil(colonne):
    assert colonne in script._colonnes()


# ── #439, retour de prod : aucune colonne sans index traitée d'un bloc ─────────
#
# Jouée à blanc en production le 30/09, la reprise est morte sur `statement_timeout`
# (15 s) : `count(*) … WHERE col = ANY(…)` sur une colonne sans index balaie la table
# entière (`tool_calls`, `nodes`…). La garde dérive les index du DDL — le schéma
# déclaratif, les DDL du boot et les migrations versionnées — et refuse toute colonne
# que la TRANSACTION compte ou repointe d'un bloc sans index utilisable, sauf table
# bornée par nature, déclarée avec sa raison.

import pathlib  # noqa: E402
import re  # noqa: E402

from oto_mcp.db import users as db_users  # noqa: E402
from oto_mcp.db._schema import _SCHEMA  # noqa: E402

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"


def _ddl() -> str:
    src = _SCHEMA + (_DB / "_init.py").read_text(encoding="utf-8")
    for p in sorted((_DB / "migrations" / "versions").glob("*.py")):
        src += p.read_text(encoding="utf-8")
    return re.sub(r'"\s*\n\s*"', "", src)   # littéraux Python concaténés sur 2 lignes


def _colonnes_indexees() -> dict[str, set]:
    """{table: colonnes en TÊTE d'un index utilisable pour `col = ANY(…)`} — index
    (partiel seulement si son prédicat est `col IS NOT NULL`), clé primaire, UNIQUE."""
    tete: dict[str, set] = {}
    for m in re.finditer(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
            r"\w+\s+ON\s+(\w+)\s*(?:USING\s+\w+\s*)?\(([^)]*)\)\s*(WHERE\s+[^;\"]*)?",
            _ddl(), re.I | re.S):
        table, col = m.group(1), m.group(2).split(",")[0].strip().split()[0]
        pred = (m.group(3) or "").strip()
        if pred and not re.fullmatch(rf"WHERE\s+{col}\s+IS\s+NOT\s+NULL", pred, re.I):
            continue          # partiel sur autre chose : inutilisable pour `col = ANY`
        tete.setdefault(table, set()).add(col)
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", _SCHEMA, re.S):
        table, corps = m.group(1), m.group(2)
        pk = re.search(r"PRIMARY KEY\s*\(([^)]*)\)", corps)
        if pk:
            tete.setdefault(table, set()).add(pk.group(1).split(",")[0].strip())
        for col in re.findall(r"^\s*(\w+)\s+[^,\n]*\b(?:PRIMARY KEY|UNIQUE)\b", corps, re.M):
            tete.setdefault(table, set()).add(col)
        for cols in re.findall(r"UNIQUE\s*\(([^)]*)\)", corps):
            tete.setdefault(table, set()).add(cols.split(",")[0].strip())
    assert tete.get("tool_calls"), "le parse du DDL ne trouve plus d'index — à réparer"
    return tete


def test_la_transaction_ne_balaie_aucune_table():
    """Chaque colonne comptée et repointée D'UN BLOC a un index utilisable, ou sa
    table est bornée par nature. Une colonne neuve sans index arrive ROUGE : la ranger
    dans `PAR_LOTS` (table à clé `id`) ou dans `BORNEES` avec sa raison."""
    tete = _colonnes_indexees()
    fautives = [f"{t}.{c}" for t, c in script.colonnes_d_un_bloc()
                if c not in tete.get(t, set()) and (t, c) not in script.BORNEES]
    assert not fautives, (
        "colonnes traitées d'un bloc SANS index (balayage complet, statement_timeout "
        "en production) :\n  " + "\n  ".join(fautives))


def test_une_colonne_bornee_n_est_declaree_que_si_elle_n_a_pas_d_index():
    """Une entrée de `BORNEES` qui a un index (ou qui n'est plus traitée) est morte :
    elle rendrait la garde aveugle le jour où l'index disparaît sans qu'on le voie."""
    tete = _colonnes_indexees()
    traitees = set(script.colonnes_d_un_bloc())
    mortes = [f"{t}.{c}" for t, c in script.BORNEES
              if (t, c) not in traitees or c in tete.get(t, set())]
    assert not mortes, f"entrées BORNEES mortes : {mortes}"


def test_les_lots_portent_sur_une_cle_id_et_sur_l_inventaire():
    corps = {m.group(1): m.group(2) for m in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", _SCHEMA, re.S)}
    sans_id = [t for t in script.PAR_LOTS
               if not re.search(r"^\s*id\s+BIGSERIAL\s+PRIMARY KEY", corps.get(t, ""), re.M)]
    assert not sans_id, f"PAR_LOTS exige une clé `id BIGSERIAL` : {sans_id}"
    hors = script.colonnes_par_lots() - set(db_users._SUB_COLUMNS)
    assert not hors, f"PAR_LOTS hors de _SUB_COLUMNS (jamais repointé) : {sorted(hors)}"
    assert not script.colonnes_par_lots() & set(script.BORNEES)


def test_un_lot_est_une_plage_de_cle_primaire():
    sql = script.sql_lot("tool_calls", ("effective_sub", "view_as_sub"))
    assert "t.id >= %(a)s AND t.id < %(b)s" in sql
    assert "t.effective_sub = ANY(%(olds)s) OR t.view_as_sub = ANY(%(olds)s)" in sql


class _Trace:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        return _Res()


def test_la_transaction_ne_touche_pas_ce_que_les_lots_repointent():
    conn = _Trace()
    db_users.repointer_patrimoine(conn, "ancien", "nouveau",
                                  sauf=script.colonnes_par_lots())
    for t, c in script.colonnes_par_lots():
        assert not [s for s in conn.sql if s.startswith(f"UPDATE {t} SET {c}=")], (t, c)
    assert any(s.startswith("UPDATE tool_calls SET sub=") for s in conn.sql), \
        "tool_calls.sub est indexée : elle reste dans la transaction"


def test_un_sauf_hors_inventaire_leve():
    with pytest.raises(ValueError):
        db_users.repointer_patrimoine(_Trace(), "a", "b", sauf=frozenset({("x", "y")}))


class _ConnLot:
    def __init__(self, journal, echoue_a):
        self.journal, self.echoue_a = journal, echoue_a

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("SELECT min(id)"):
            table = sql.rsplit(" ", 1)[1]
            return _Res(one={"lo": 1, "hi": 250} if table == "tool_calls"
                        else {"lo": None, "hi": None})
        if sql.startswith("SET LOCAL"):
            return _Res()
        if (params or {}).get("a") == self.echoue_a:
            raise RuntimeError("canceling statement due to statement timeout")
        self.journal.append(("lot", params["a"], params["b"]))
        r = _Res()
        r.rowcount = 1
        return r

    def commit(self):
        self.journal.append(("commit",))

    def rollback(self):
        self.journal.append(("rollback",))


def test_un_lot_valide_n_est_pas_defait_par_un_lot_suivant_qui_expire():
    journal: list = []
    with pytest.raises(script.LotEnEchec) as leve:
        script.lots([("ancien", "nouveau")], apply=True, taille=100,
                    connect=lambda: _ConnLot(journal, echoue_a=201), dire=lambda *_: None)
    assert (leve.value.table, leve.value.debut) == ("tool_calls", 201)
    assert journal == [("lot", 1, 101), ("commit",), ("lot", 101, 201), ("commit",)]


def test_a_blanc_chaque_lot_est_annule_et_la_reprise_saute_le_fait():
    journal: list = []
    bilan = script.lots([("ancien", "nouveau")], apply=False, taille=100,
                        depuis=("tool_calls", 101),
                        connect=lambda: _ConnLot(journal, echoue_a=None),
                        dire=lambda *_: None)
    assert journal == [("lot", 101, 201), ("rollback",), ("lot", 201, 301), ("rollback",)]
    assert bilan == {"tool_calls": 2}
