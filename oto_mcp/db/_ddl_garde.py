"""Le garde des DDL du démarrage : un ordre qui n'a rien à faire ne part pas.

**Le défaut** (#1015, puis préprod du 23/09/2026) : `ALTER TABLE … ADD COLUMN IF NOT
EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ALTER COLUMN … DROP NOT NULL` prennent leur
verrou AVANT de constater qu'ils n'ont rien à faire — le `IF NOT EXISTS` évite
l'ERREUR, pas le VERROU. Sur une base déjà à jour, c'est-à-dire à CHAQUE démarrage
après le premier, le boot demandait donc un `AccessExclusiveLock` sur une
quarantaine de tables et un `ShareLock` (qui bloque les écritures) sur une
cinquantaine d'autres. Deux déploiements préprod sont morts en `LockNotAvailable` sur
`ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS request_id` — une colonne posée
depuis des semaines — parce qu'une simple lecture longue (un `psql` d'enquête) tenait
`tool_calls`.

**Le remède, mécanique et non au cas par cas** : `GardeDdl` enveloppe la connexion
d'`apply_boot_schema`. Chaque ordre qu'on lui passe — y compris ceux des aides
`_migrate_*` et chacun des ordres du `_SCHEMA` assemblé, découpé ici — est confronté
au catalogue (lecture pure : `AccessShareLock` sur le catalogue seulement, aucun
verrou sur la table visée) et **n'est envoyé que s'il a quelque chose à faire**.

⚠️ **Le sens du doute est toujours « exécuter »** : une forme non reconnue, une
sous-commande inconnue, une comparaison incertaine (un défaut qu'on ne sait pas
normaliser) ⟹ l'ordre part tel quel, comme avant. Le pire cas d'un garde trop
prudent est un verrou pris pour rien ; le pire cas d'un garde trop hardi serait un
schéma qui ne bouge plus. Le banc `tests/test_boot_ddl_aucun_verrou_fort.py` rend
visible tout ordre qui prend encore un verrou fort sur une base à jour.

⚠️ **Sur une base NEUVE ou en retard, rien ne change** : chaque vérification dit
« à faire », et l'ordre part exactement comme avant, dans le même ordre, dans la
même transaction.
"""
from __future__ import annotations

import re

__all__ = ["GardeDdl", "ddl_a_faire", "decouper_sql"]


# ── Découpage ───────────────────────────────────────────────────────────────────

def decouper_sql(sql: str) -> list[str]:
    """Découpe un texte SQL en ordres, aux `;` de premier niveau.

    Respecte ce qui peut contenir un `;` sans terminer l'ordre : chaînes
    (`'…'`, `E'…'` avec échappements), identifiants entre guillemets, chaînes
    dollar (`$$…$$`, `$tag$…$tag$` — les corps `DO` et `CREATE FUNCTION`),
    commentaires de ligne et de bloc (imbriqués, comme PostgreSQL). Un morceau qui
    ne contient que des blancs et des commentaires est écarté : envoyé seul, il ne
    ferait rien."""
    ordres: list[str] = []
    i, n, debut = 0, len(sql), 0
    while i < n:
        c = sql[i]
        if c == "'":
            echappe = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            i += 1
            while i < n:
                if echappe and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif c == '"':
            i = sql.find('"', i + 1)
            i = n if i < 0 else i + 1
        elif c == "-" and sql.startswith("--", i):
            i = sql.find("\n", i)
            i = n if i < 0 else i + 1
        elif c == "/" and sql.startswith("/*", i):
            profondeur, i = 1, i + 2
            while i < n and profondeur:
                if sql.startswith("/*", i):
                    profondeur, i = profondeur + 1, i + 2
                elif sql.startswith("*/", i):
                    profondeur, i = profondeur - 1, i + 2
                else:
                    i += 1
        elif c == "$":
            m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if m and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                balise = m.group(0)
                fin = sql.find(balise, i + len(balise))
                i = n if fin < 0 else fin + len(balise)
            else:
                i += 1
        elif c == ";":
            ordres.append(sql[debut:i])
            i += 1
            debut = i
        else:
            i += 1
    ordres.append(sql[debut:])
    return [o for o in (o.strip() for o in ordres) if _normaliser(o)]


def _normaliser(ordre: str) -> str:
    """L'ordre sans ses commentaires, blancs réduits — la forme qu'on reconnaît."""
    sans = re.sub(r"--[^\n]*", " ", ordre)
    sans = re.sub(r"/\*.*?\*/", " ", sans, flags=re.DOTALL)
    return " ".join(sans.split())


def _au_premier_niveau(texte: str, separateur: str = ",") -> list[str]:
    """Découpe `texte` sur `separateur` hors parenthèses et hors chaînes."""
    morceaux, profondeur, debut, i = [], 0, 0, 0
    while i < len(texte):
        c = texte[i]
        if c == "'":
            i = texte.find("'", i + 1)
            if i < 0:
                break
        elif c == "(":
            profondeur += 1
        elif c == ")":
            profondeur -= 1
        elif c == separateur and profondeur == 0:
            morceaux.append(texte[debut:i])
            debut = i + 1
        i += 1
    morceaux.append(texte[debut:])
    return [m.strip() for m in morceaux if m.strip()]


# ── Le catalogue ────────────────────────────────────────────────────────────────
# Lectures pures. `to_regclass` résout un nom SANS verrouiller la relation ; les
# autres requêtes ne lisent que des tables du catalogue.

def _valeurs(row) -> tuple:
    """Une ligne en tuple, quelle que soit la fabrique de lignes de la connexion."""
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _relation_existe(conn, nom: str) -> bool:
    return _valeurs(conn.execute("SELECT to_regclass(%s) IS NOT NULL", (nom,)).fetchone())[0]


def _colonne(conn, table: str, colonne: str):
    """`(attnotnull, défaut)` de la colonne, ou `None` si elle n'existe pas."""
    row = conn.execute(
        "SELECT a.attnotnull, pg_get_expr(d.adbin, d.adrelid) "
        "FROM pg_attribute a "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = to_regclass(%s) AND a.attname = %s "
        "AND a.attnum > 0 AND NOT a.attisdropped",
        (table, colonne),
    ).fetchone()
    return None if row is None else _valeurs(row)


def _contrainte_existe(conn, table: str, nom: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND conname = %s",
        (table, nom),
    ).fetchone() is not None


def _cle_primaire(conn, table: str) -> tuple[str, ...] | None:
    """Les colonnes de la clé primaire de `table`, dans l'ordre, ou `None`."""
    row = conn.execute(
        "SELECT array_agg(a.attname::text ORDER BY k.ord) "
        "FROM pg_constraint c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conrelid = to_regclass(%s) AND c.contype = 'p'",
        (table,),
    ).fetchone()
    cols = _valeurs(row)[0] if row else None
    return tuple(cols) if cols else None


def _colonnes(conn, relation: str) -> list[str]:
    return [_valeurs(r)[0] for r in conn.execute(
        "SELECT attname::text FROM pg_attribute WHERE attrelid = to_regclass(%s) "
        "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        (relation,),
    ).fetchall()]


# ── Le verdict ──────────────────────────────────────────────────────────────────

_ID = r'"?(\w+)"?'
_RE_CREATE_REL = re.compile(
    rf"^CREATE (?:UNIQUE )?(?:TABLE|INDEX|SEQUENCE) IF NOT EXISTS {_ID}(?:\s|\(|$)", re.I)
_RE_DROP_REL = re.compile(r"^DROP (TABLE|INDEX|VIEW|SEQUENCE) IF EXISTS ([\w\", ]+?)( CASCADE| RESTRICT)?$", re.I)
_RE_DROP_SCHEMA = re.compile(rf"^DROP SCHEMA IF EXISTS {_ID}( CASCADE| RESTRICT)?$", re.I)
_RE_VUE_ETOILE = re.compile(rf"^CREATE OR REPLACE VIEW {_ID} AS SELECT \* FROM {_ID}$", re.I)
_RE_ALTER_TABLE = re.compile(rf"^ALTER TABLE (IF EXISTS )?(?:ONLY )?{_ID} (.+)$", re.I | re.S)


def ddl_a_faire(conn, ordre: str) -> bool:
    """`False` seulement si le catalogue PROUVE que `ordre` n'a rien à faire.

    Tout le reste — forme inconnue, doute — rend `True` : l'ordre part."""
    sql = _normaliser(ordre)
    m = _RE_CREATE_REL.match(sql)
    if m:
        return not _relation_existe(conn, m.group(1))
    m = _RE_DROP_REL.match(sql)
    if m:
        noms = [x.strip().strip('"') for x in m.group(2).split(",")]
        return any(_relation_existe(conn, x) for x in noms)
    m = _RE_DROP_SCHEMA.match(sql)
    if m:
        return conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (m.group(1),)
        ).fetchone() is not None
    m = _RE_VUE_ETOILE.match(sql)
    if m:
        # Une vue `SELECT *` fige ses colonnes à sa création : la rejouer n'a de
        # sens que si la table en a gagné depuis (cf. `guide_library`).
        vue, table = m.group(1), m.group(2)
        return not (_relation_existe(conn, vue) and _colonnes(conn, vue) == _colonnes(conn, table))
    m = _RE_ALTER_TABLE.match(sql)
    if m:
        si_existe, table, reste = m.group(1), m.group(2), m.group(3)
        if not _relation_existe(conn, table):
            return not si_existe          # sans IF EXISTS : qu'il lève, comme avant
        return any(_sous_commande_a_faire(conn, table, s) for s in _au_premier_niveau(reste))
    return True


def _sous_commande_a_faire(conn, table: str, sc: str) -> bool:
    m = re.match(rf"^ADD (?:COLUMN )?IF NOT EXISTS {_ID}\b", sc, re.I)
    if m:
        return _colonne(conn, table, m.group(1)) is None
    m = re.match(rf"^DROP (?:COLUMN )?IF EXISTS {_ID}( CASCADE| RESTRICT)?$", sc, re.I)
    if m:
        return _colonne(conn, table, m.group(1)) is not None
    m = re.match(rf"^DROP CONSTRAINT IF EXISTS {_ID}( CASCADE| RESTRICT)?$", sc, re.I)
    if m:
        return _contrainte_existe(conn, table, m.group(1))
    m = re.match(r"^ADD PRIMARY KEY \(([^)]*)\)$", sc, re.I)
    if m:
        voulue = tuple(c.strip().strip('"') for c in m.group(1).split(","))
        return _cle_primaire(conn, table) != voulue
    m = re.match(rf"^ALTER (?:COLUMN )?{_ID} (DROP NOT NULL|SET NOT NULL|DROP DEFAULT|SET DEFAULT (.+))$", sc, re.I)
    if m:
        col = _colonne(conn, table, m.group(1))
        if col is None:
            return True                   # colonne absente : qu'il lève, comme avant
        non_nul, defaut = col
        geste = m.group(2).upper()
        if geste == "DROP NOT NULL":
            return non_nul
        if geste == "SET NOT NULL":
            return not non_nul
        if geste == "DROP DEFAULT":
            return defaut is not None
        return defaut is None or _sans_transtypage(defaut) != _sans_transtypage(m.group(3))
    return True


def _sans_transtypage(expr: str) -> str:
    """`'member'::text` → `'member'`, `nextval('s'::regclass)` → `nextval('s')`.

    PostgreSQL rend un défaut sous sa forme analysée, transtypages explicités.
    On ne compare donc qu'après les avoir retirés des DEUX côtés ; si les formes
    diffèrent encore, le doute l'emporte et l'ordre part."""
    return " ".join(re.sub(r"::\s*\w+(?:\s+(?:varying|precision|with(?:out)? time zone))?(?:\[\])?", "", expr).split())


# ── La connexion gardée ─────────────────────────────────────────────────────────

class GardeDdl:
    """Enveloppe la connexion du boot : un ordre sans rien à faire n'est pas envoyé.

    Un texte à plusieurs ordres (le `_SCHEMA` assemblé) est découpé et chaque ordre
    passe par le même verdict — c'est là que vivaient les `CREATE INDEX IF NOT
    EXISTS` qui prenaient leur `ShareLock` à chaque démarrage. Un ordre paramétré
    (`%s`) n'est jamais découpé : il n'en porte qu'un.

    Tout le reste (`transaction`, `cursor`, `row_factory`…) est la connexion
    enveloppée, inchangée. Envelopper deux fois est sans effet."""

    def __init__(self, conn):
        self._conn = conn._conn if isinstance(conn, GardeDdl) else conn

    def execute(self, query, params=None, **kw):
        if isinstance(query, str) and params is None:
            dernier = None
            for ordre in decouper_sql(query):
                dernier = self._un(ordre, None, kw)
            return dernier
        return self._un(query, params, kw)

    def _un(self, ordre, params, kw):
        if isinstance(ordre, str) and not ddl_a_faire(self._conn, ordre):
            return None
        if params is None:
            return self._conn.execute(ordre, **kw)
        return self._conn.execute(ordre, params, **kw)

    def __getattr__(self, nom):
        return getattr(self._conn, nom)
