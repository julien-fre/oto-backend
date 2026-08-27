"""Une colonne de `projects` écrite mais jamais relue est une colonne morte (#276).

`db.projects._PROJECT_COLS` est le SEUL chemin de lecture d'un projet — `get_project_by_id`,
`get_project_by_mcp_slug`, toutes les listes. Ajouter une colonne au DDL et à l'écriture
sans l'ajouter là produit une panne qui ne ressemble pas à sa cause : la valeur part en
base, l'UPDATE réussit, et la relecture rend `None`.

Vécu avec `mcp_expose_docs` et `mcp_instructions_md` (#310), trois symptômes pour un oubli :

- `publish_mcp(mcp_expose_docs=True)` relu à `false` — et le flag INERTE au serving,
  `subdomain_project` lisant la même ligne : la fonctionnable « exposer les pages du
  projet » n'a jamais fonctionné ;
- `mcp_instructions_md` absent du handshake MCP, le destinataire d'un projet partagé
  recevant le préambule générique au lieu des engagements de la mission ;
- l'avertissement « aucune instruction publiée » émis à CHAQUE publication, y compris
  quand on venait d'en publier — le seul indice visible, et il accusait l'utilisateur.

Le garde-fou croise le DDL : c'est le DDL qui fait foi, pas une liste tenue à la main.
"""
import pathlib
import re

from oto_mcp.db import _schema as _schema_mod
from oto_mcp.db.projects import _PROJECT_COLS

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"

# Colonnes délibérément NON relues. Une entrée ici est une décision, pas un oubli.
NOT_READ = {
    # Lignage d'un fork « Ajouter à mon Oto » : sert en WHERE (idempotence de l'import),
    # jamais en sortie — aucune surface ne l'affiche.
    "copied_from",
    # Drapeau d'outbox du worker d'embeddings : drainé par sa propre requête
    # (`db/aux_embed.py`), n'a rien à faire dans la vue d'un projet.
    "embed_dirty",
}


def _ddl_columns() -> set[str]:
    """Colonnes de `CREATE TABLE projects` (+ les `ADD COLUMN` de migration, qui sont
    la vraie porte d'entrée d'une colonne neuve sur une base vivante)."""
    schema = _schema_mod._SCHEMA
    block = re.search(r"CREATE TABLE IF NOT EXISTS projects \((.*?)\n\);", schema, re.S)
    assert block, "table `projects` introuvable dans _schema.py"
    cols = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r"([a-z_][a-z0-9_]*)\s+[A-Z]", line)
        if m and m.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
            cols.add(m.group(1))
    return cols


def _init_added_columns() -> set[str]:
    init = (_DB / "_init.py").read_text(encoding="utf-8")
    return set(re.findall(
        r"ALTER TABLE projects ADD COLUMN IF NOT EXISTS ([a-z_][a-z0-9_]*)", init))


def test_every_project_column_is_read_back():
    read = {c.strip() for c in _PROJECT_COLS.split(",")}
    declared = (_ddl_columns() | _init_added_columns()) - NOT_READ
    missing = sorted(declared - read)
    assert not missing, (
        f"colonnes de `projects` écrites mais jamais relues : {missing}. Ajoute-les à "
        f"`_PROJECT_COLS` (elles rendront `None` partout sinon), ou déclare-les dans "
        f"`NOT_READ` de ce test si l'omission est voulue.")


def test_the_publication_columns_are_in_the_read_path():
    """Nommées explicitement : ce sont elles qui ont cassé, et elles servent un chemin
    SANS session (endpoint publié) où personne ne voit passer l'anomalie."""
    for col in ("mcp_access", "mcp_tools", "mcp_slug", "mcp_expose_datastore",
                "mcp_expose_datastore_write", "mcp_expose_docs", "mcp_instructions_md"):
        assert col in _PROJECT_COLS, f"`{col}` doit être relue"
