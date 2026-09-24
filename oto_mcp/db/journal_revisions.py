"""Le JOURNAL des révisions de ligne — ce que chaque révision a changé, valeurs comprises.

oto#273, jalon M1 : le déclencheur écrit, ce module le pose. La LECTURE (M3) vit dans
`db/historique.py`, qui sert `GET …/rows/{row_id}/history`, `data_row_history` et le
parcours d'une ligne (`docs/datastore.md` §Journal des révisions).

**Pourquoi un déclencheur, comme `rev`.** Pour la même raison que `db/revision.py` : la
base est partagée entre la préproduction et la production, et une écriture de l'ANCIEN
code, pendant la bascule bleu/vert, doit être journalisée aussi. Seul PostgreSQL voit
toutes les faces d'écriture — MCP, REST, import, maintenance, une requête à la main.

**Pourquoi APRÈS, et pas dans le déclencheur de révision.** `datastore_rows_20_revision`
est un `BEFORE UPDATE` dont la condition inclut les colonnes du bail : il avance `rev`
quand seul le bail bouge, ce que le journal ne doit PAS voir. Un `AFTER` a trois
avantages : il lit la ligne FINALE (et donc la `rev` déjà avancée par le `BEFORE`), il
couvre l'INSERT que le déclencheur de révision ignore, et sa condition `WHEN` sur `data`
seule écarte un mouvement de bail SANS appeler la fonction — le coût d'une réservation,
d'un renouvellement ou d'une libération reste exactement celui d'aujourd'hui.

Deux déclencheurs et une fonction : la condition d'un déclencheur d'INSERT ne peut pas
nommer `OLD`, donc INSERT et UPDATE ne partagent pas le même `WHEN`.

**La forme du diff** : `{champ: {"avant": v, "apres": v}}`, clé par clé de `data`, valeur
ENTIÈRE (couches comprises). Un champ absent d'un côté n'a PAS la clé de ce côté :
`{"apres": 1}` est un ajout, `{"avant": 1}` un retrait, `{"avant": null}` une valeur
`null` qui a changé. Absent, `null` et `[]` restent trois états (`db/revision.py`).
Une insertion porte tous ses champs en `apres`. Une écriture sans effet n'écrit rien.

**L'estampille (acteur, run, source, geste), M2.** La fonction la lit dans des réglages
de transaction `oto.acteur`, `oto.run_id`, `oto.source`, `oto.geste_id`, que le serveur
pose au point de passage de toute écriture de ligne (`db/estampille.py`, `set_config(…,
true)`, jamais `SET` : une connexion du pool sert tout le monde). Une écriture qui ne
passe pas par le serveur ne pose rien : ses colonnes restent `NULL`.

**L'interrupteur** `OTO_JOURNAL_REVISIONS=off` (défaut `on`) coupe le journal POUR LES
ÉCRITURES DE CE PROCESSUS : le pool ouvre ses connexions avec
`-c oto.journal_revisions=off`, que la fonction lit à chaque ligne. Il n'est pas
posé dans le corps de la fonction, qui est commun à toutes les versions servies sur la
base partagée : un boot de préproduction réglé autrement rallumerait le journal de la
production. Toute autre valeur que `on`/`off` LÈVE — à l'ouverture du pool, donc au boot.

⚠️ **Posé seulement s'il manque** (même prix que `db/revision.py`) : `CREATE TRIGGER`
prend un verrou `SHARE ROW EXCLUSIVE` sur `datastore_rows`, et `CREATE INDEX` un verrou
`SHARE` sur le journal que chaque écriture de ligne alimente. Les reprendre à chaque
démarrage bloquerait les écritures de la production. Changer une CONDITION, c'est un
nouveau nom et le retrait explicite de l'ancien ; la FONCTION, elle, est reposée à
chaque démarrage — sans verrou de table.
"""
from __future__ import annotations

import os

VARIABLE = "OTO_JOURNAL_REVISIONS"
REGLAGE_PG = "oto.journal_revisions"
TABLE = "datastore_row_revisions"
INDEX = "idx_datastore_row_revisions_ligne"
NOM_FONCTION = "datastore_journal_revision"
# `_30_` : après `_20_revision` si un jour l'un d'eux passe du même côté. Les AFTER ROW
# d'une table s'exécutent eux aussi dans l'ordre alphabétique de leur nom.
DECLENCHEUR_INSERT = "datastore_rows_30_journal_insert"
DECLENCHEUR_UPDATE = "datastore_rows_30_journal_update"
# Le bail (`claimed_*`), `claims`, `rev`, `updated_at` : aucun ne fait une révision.
CONDITION_UPDATE = "OLD.data IS DISTINCT FROM NEW.data"


def journal_coupe() -> bool:
    """`True` si `OTO_JOURNAL_REVISIONS=off`. Absente ou `on` : le journal écrit.
    Illisible : lève — un interrupteur mal orthographié ne doit pas trancher en silence."""
    # Nom LITTÉRAL : l'inventaire (`env_inventory.py`) trouve ses lectures par l'AST.
    lu = os.environ.get("OTO_JOURNAL_REVISIONS", "on")
    brut = lu.strip().lower()
    if brut not in ("on", "off"):
        raise ValueError(
            f"{VARIABLE}={lu!r} : valeur illisible, attendu `on` ou `off`")
    return brut == "off"


# Une seule instruction par branche : le diff se calcule dans le moteur, clé par clé,
# sans boucle PL/pgSQL. `jsonb_object_agg` sur zéro ligne rend NULL : c'est « rien n'a
# changé », et rien ne s'écrit.
#
# JAMAIS BLOQUANT : le journal est une écriture fantôme, il ne fait pas échouer l'écriture
# d'une ligne pour une forme de données inattendue. Un `data` qui n'est pas un objet
# (`jsonb_each`/`jsonb_object_keys` lèveraient) n'écrit rien et émet un `WARNING` nommant
# `ns_id` et `row_id`. Aucune autre erreur n'est avalée : pas d'`EXCEPTION WHEN OTHERS`.
_FONCTION = f"""
CREATE OR REPLACE FUNCTION {NOM_FONCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    d jsonb;
BEGIN
    IF current_setting('{REGLAGE_PG}', true) = 'off' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(NEW.data) <> 'object'
       OR (TG_OP = 'UPDATE' AND jsonb_typeof(OLD.data) <> 'object') THEN
        RAISE WARNING '{NOM_FONCTION} : data n''est pas un objet JSON '
                      '(ns_id=%, row_id=%, avant=%, apres=%) : révision non journalisée',
                      NEW.ns_id, NEW.row_id,
                      CASE WHEN TG_OP = 'UPDATE' THEN jsonb_typeof(OLD.data) END,
                      jsonb_typeof(NEW.data);
        RETURN NULL;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT COALESCE(jsonb_object_agg(e.k, jsonb_build_object('apres', e.v)),
                        '{{}}'::jsonb)
          INTO d FROM jsonb_each(NEW.data) AS e(k, v);
    ELSE
        SELECT jsonb_object_agg(c.k,
                   CASE WHEN OLD.data ? c.k
                        THEN jsonb_build_object('avant', OLD.data -> c.k)
                        ELSE '{{}}'::jsonb END
                || CASE WHEN NEW.data ? c.k
                        THEN jsonb_build_object('apres', NEW.data -> c.k)
                        ELSE '{{}}'::jsonb END)
          INTO d
          FROM (SELECT jsonb_object_keys(OLD.data)
                UNION SELECT jsonb_object_keys(NEW.data)) AS c(k)
         WHERE (OLD.data -> c.k) IS DISTINCT FROM (NEW.data -> c.k);
        IF d IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;
    INSERT INTO {TABLE} (ns_id, row_id, rev, diff, acteur, run_id, source, geste_id)
    VALUES (NEW.ns_id, NEW.row_id, NEW.rev, d,
            NULLIF(current_setting('oto.acteur', true), ''),
            NULLIF(current_setting('oto.run_id', true), ''),
            NULLIF(current_setting('oto.source', true), ''),
            NULLIF(current_setting('oto.geste_id', true), ''));
    RETURN NULL;
END $$"""


def poser_journal_des_revisions(conn) -> list[str]:
    """Index, fonction, déclencheurs — sur la connexion DDL du boot, APRÈS `_SCHEMA`
    (qui crée la table) et après `revision.poser_revision_de_ligne` (qui crée `rev`).
    Rend les noms de ce qui vient d'être créé (vide au boot ordinaire)."""
    poses: list[str] = []
    # Pas d'import de `_init._index_absent` : ce module est importé PAR `_init.py`.
    if conn.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s",
                    (INDEX,)).fetchone() is None:
        # Non UNIQUE : une ligne supprimée puis recréée sous le même `row_id` repart
        # de `rev` 0, et ses révisions passées restent.
        conn.execute(f"CREATE INDEX IF NOT EXISTS {INDEX} "
                     f"ON {TABLE} (ns_id, row_id, rev)")
        poses.append(INDEX)
    conn.execute(_FONCTION)
    for nom, quand, condition in (
            (DECLENCHEUR_INSERT, "INSERT", None),
            (DECLENCHEUR_UPDATE, "UPDATE", CONDITION_UPDATE)):
        if conn.execute(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgrelid = 'datastore_rows'::regclass AND tgname = %s",
                (nom,)).fetchone():
            continue
        when = f"WHEN ({condition}) " if condition else ""
        conn.execute(
            f"CREATE TRIGGER {nom} AFTER {quand} ON datastore_rows "
            f"FOR EACH ROW {when}EXECUTE FUNCTION {NOM_FONCTION}()")
        poses.append(nom)
    return poses
