"""Couches de contexte & how-to : la façade `guides`, servie par la table `nodes`.

Historiquement la table `guides` (ADR 0042). Depuis le **lot M1** du chantier
modèle de contenu (blueprint ADR 0054/0063), les lignes vivent dans **`nodes`** —
et le mot « guide » ne désigne plus qu'une **surface** (`oto_guide`,
`/api/me/guides/*`), plus un objet du modèle :

- **une couche de contexte EST une page** (0055-D4) — d'où `kind='page'` ici, et
  pas un hypothétique `kind='guide'` ;
- la **livraison** (`init` injecté au handshake / `on-demand` chargé par
  `oto_guide`) était la NATURE d'une ligne de `guides` ; ce n'est plus qu'une
  **propriété** du nœud, `props->>'delivery'`. Les deux familles de fonctions
  ci-dessous ne survivent que parce que les surfaces, elles, ne changent pas :
  elles ne se distinguent plus que par la valeur d'une clé JSON.

**Contrat inchangé** : mêmes signatures, mêmes clés de retour (`scope`,
`owner_id`, `slug`, `title`, `description`, `body_md`, `delivery`, `updated_at`)
que du temps de la table `guides` — `guide_store` et tout ce qui est au-dessus
n'a pas bougé d'une ligne, et les tests qui bouchonnent ces fonctions non plus.
Le `scope` de la surface est l'`owner_type` du nœud (même vocabulaire :
platform | org | group | user), le `owner_id` reste le même texte.

⚠️ **La table `guides` a été retirée du code le 23/09/2026** (otomata-tech/oto#239) :
son DDL, ses `ALTER` de démarrage, ses index de recherche et la recopie
`guides` → `nodes` jouée à chaque boot. Elle n'avait plus d'écrivain applicatif
depuis le lot M1, mais le démarrage continuait d'y écrire, et un `CREATE TABLE IF
NOT EXISTS` l'aurait fait renaître après un `DROP`. Le `DROP` lui-même est un geste
d'exploitation, pas un geste de démarrage (docs/live-migrations.md).

L'identifiant public (0059-D3) d'une couche de contexte est **dérivé de sa clé
naturelle** `(scope, owner, slug)` — cf. `_PID`. Distinct des PROCÉDURES
(`org_instructions`, slots/versioning), qui restent une table à part jusqu'à leur
propre lot. Ré-exporté par `db/__init__`.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from ._conn import _connect

# Le `kind` d'une couche de contexte. Une page, comme les autres (0055-D4) : ce
# qui la distingue d'une page de projet est une PROPRIÉTÉ (`delivery`), pas sa
# nature — c'est tout le propos du lot M1.
_KIND = "page"


# L'identifiant public (0059-D3) d'une couche de contexte, DÉRIVÉ de sa clé naturelle
# `(scope, owner, slug)`. Les casts `::text` sont nécessaires : sans eux, `||` sur des
# paramètres de type inconnu laisse PostgreSQL sans résolution d'opérateur.
#
# **Pourquoi dérivé, alors que 0059-D3 veut un opaque tiré au sort** : l'unicité de
# `public_id` porte exactement l'invariant que la table `guides` portait en
# `UNIQUE (scope, owner_id, slug)` — une couche par (scope, propriétaire, slug) — et
# `ON CONFLICT (public_id)` l'arbitre sans index supplémentaire. La dérivation exige
# que la clé naturelle soit IMMUABLE : elle l'est ici, la surface guide n'a pas de
# renommage (on écrit et on supprime par slug). Un nœud NATIF (pages, tableaux,
# lignes — lots M2+) se renomme, lui : son identifiant est tiré au sort, jamais
# dérivé. Ne pas généraliser ceci.
_PID = ("'nod_' || substr(md5('ctx:' || %s::text || ':' || %s::text "
        "|| ':' || %s::text), 1, 24)")

# Projection nœud → forme historique d'une ligne `guides`. `scope` EST l'owner_type
# (même vocabulaire), les champs de prose vivent dans `props`. COALESCE parce que
# `guides` les portait NOT NULL DEFAULT '' : une clé absente ne doit pas rendre None.
#
# `seed_sha256` est ici depuis le 23/09/2026 (oto#236) : tous les appelants
# reprojettent déjà ce dict en clés explicites (`guide_store`, les routes REST,
# `scripts/aligner_guides_plateforme.py`) — l'ajouter ne fuite nulle part, et son
# absence faisait lire `None` au script de maintenance : chaque guide plateforme
# réaligné se voyait donc rapporté « empreinte ABSENTE » à la relecture qui suit
# `--aligner`, alors qu'elle vient d'être posée — un opérateur croit l'alignement
# raté et le rejoue sans fin.
_COLS = ("id, owner_type AS scope, owner_id, props->>'slug' AS slug, "
         "COALESCE(props->>'title', '') AS title, "
         "COALESCE(props->>'description', '') AS description, "
         "COALESCE(props->>'body_md', '') AS body_md, "
         "props->>'delivery' AS delivery, props->>'seed_sha256' AS seed_sha256, "
         "created_at, updated_at")

# --- On-demand (catalogue `oto_guide`) : delivery='on-demand' UNIQUEMENT ------

def _body_before_write(conn, scope: str, owner_id: str, slug: str) -> Optional[str]:
    row = conn.execute(
        f"SELECT COALESCE(props->>'body_md', '') AS body FROM nodes WHERE public_id = {_PID} FOR UPDATE",
        (scope, str(owner_id), slug),
    ).fetchone()
    return row["body"] if row else None


def _maintain_projections(conn, row: dict, body_md: Optional[str]) -> None:
    # Corps explicite modifié seulement : une édition de titre ne réécrit aucun
    # bloc. Même transaction que le contenu, aucune réparation nocturne requise.
    from .blocks import write_node_blocks
    from .search import stamp_rank_vector
    if body_md is not None:
        write_node_blocks(conn, row["id"], body_md)
    stamp_rank_vector(conn, "nodes", "id = %s", (row["id"],))

def list_guides_db(scope: str, owner_id: str) -> list[dict]:
    """Guides ON-DEMAND d'un (scope, owner), triés par slug — métadonnées + corps.
    Exclut les readmes init (delivery='init')."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM nodes "
            f"WHERE kind = '{_KIND}' AND owner_type = %s AND owner_id = %s "
            "AND props->>'delivery' = 'on-demand' ORDER BY props->>'slug'",
            (scope, str(owner_id)),
        ).fetchall()
        return [dict(r) for r in rows]


def _get_one(scope: str, owner_id: str, slug: str, delivery: str) -> Optional[dict]:
    """La couche de contexte `(scope, owner, slug)` SI elle a cette livraison.
    Lookup par identifiant public dérivé = un accès à l'index d'identité."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM nodes WHERE public_id = {_PID} "
            "AND props->>'delivery' = %s",
            (scope, str(owner_id), slug, delivery),
        ).fetchone()
        return dict(row) if row else None


def get_guide_db(scope: str, owner_id: str, slug: str) -> Optional[dict]:
    return _get_one(scope, owner_id, slug, "on-demand")


def set_guide_db(scope: str, owner_id: str, slug: str, body_md: str,
                 title: str = "", description: str = "") -> Optional[dict]:
    """Crée ou met à jour (upsert par `(scope, owner_id, slug)`) un guide ON-DEMAND.

    La mise à jour ne touche QUE la prose — `delivery` n'est posé qu'à l'insertion,
    exactement comme la table `guides` ne le mettait pas à jour. `embed_dirty` suit
    la prose (#282) : écrire une couche la remet dans l'outbox sémantique, comme
    `guides` le faisait par sa colonne.

    **Renvoie `None` quand la clé est déjà occupée par une couche `init`** — et rien
    n'a alors été écrit. C'est le trou du 09/09/2026 : `public_id` dérive de
    `(scope, owner, slug)` et IGNORE `delivery`, alors que toutes les lectures
    filtrent dessus. Un `PUT …/guides/org/readme` on-demand tombait donc sur la MÊME
    ligne que le readme injecté de l'org, en remplaçait le corps sans changer son
    `delivery`, et rendait 200 : la prose reçue par toutes les sessions de l'org était
    détruite, et le guide écrit restait introuvable à la lecture (qui, elle, exige
    `delivery='on-demand'`). Le refus est porté par le `WHERE` de l'`ON CONFLICT` —
    donc par l'instruction elle-même, pas par un pré-contrôle qu'une écriture
    concurrente traverserait."""
    with _connect() as conn:
        previous_body = _body_before_write(conn, scope, owner_id, slug)
        row = conn.execute(
            f"INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            f"VALUES ({_PID}, '{_KIND}', %s, %s, "
            "        jsonb_build_object('slug', %s::text, 'delivery', 'on-demand', "
            "                           'title', %s::text, 'description', %s::text, "
            "                           'body_md', %s::text, 'embed_dirty', TRUE)) "
            "ON CONFLICT ON CONSTRAINT nodes_public_id_key DO UPDATE SET "
            "  props = nodes.props || jsonb_build_object("
            "      'title', %s::text, 'description', %s::text, 'body_md', %s::text, "
            "      'embed_dirty', TRUE), "
            "  updated_at = NOW() "
            "  WHERE nodes.props->>'delivery' = 'on-demand' "
            f"RETURNING {_COLS}",
            (scope, str(owner_id), slug,                       # public_id dérivé
             scope, str(owner_id),                             # owner_type, owner_id
             slug, title, description, body_md,                # props à l'insertion
             title, description, body_md),                     # prose à la mise à jour
        ).fetchone()
        if row is None:                       # une couche `init` occupe la clé
            return None
        _maintain_projections(conn, row, body_md if body_md != previous_body else None)
        return dict(row)


def empreinte_de_couche(title: str, description: str, body_md: str) -> str:
    """L'empreinte d'une couche de contexte — ce qui permet de dire si la BASE a
    bougé depuis le dernier semis, et si le FICHIER a bougé depuis celui-ci.

    Sur les TROIS champs que le semis pose, pas seulement le corps : un titre ou une
    description corrigés dans le dépôt doivent atteindre la base comme un corps
    réécrit — c'est ce que l'index d'`oto_guide` sert à l'agent avant qu'il ne charge
    quoi que ce soit. Le séparateur `\0` n'apparaît dans aucun des trois : sans lui,
    déplacer une fin de titre au début de la description rendrait la même empreinte.
    """
    brut = "\0".join([title or "", description or "", body_md or ""])
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()


class SemisConflitDeLivraison(ValueError):
    """Le slug d'un guide plateforme est occupé par une couche `init` (readme injecté).

    Écrire là remplacerait le readme d'accueil par un how-to sans que sa livraison
    change : la prose servie à toutes les sessions serait détruite, et le guide semé
    resterait introuvable à la lecture (qui exige `delivery='on-demand'`). Même trou
    que celui fermé le 09/09/2026 dans `set_guide_db`, pris du côté du semis."""


def seed_guide_db(scope: str, owner_id: str, slug: str, body_md: str,
                  title: str = "", description: str = "", *,
                  seed_sha256: str) -> str:
    """Sème un guide ON-DEMAND depuis son FICHIER, et le met à jour si le fichier a
    changé sans que la base ait été éditée. Renvoie le verdict de ce guide :

    - `seme` — la ligne n'existait pas, elle est posée ;
    - `mis_a_jour` — le fichier a changé, la base était encore au dernier semis ;
    - `inchange` — même empreinte des deux côtés, aucune écriture ;
    - `diverge` — la base a été éditée depuis le dernier semis : **on conserve**
      l'édition, l'appelant la signale ;
    - `sans_empreinte` — ligne posée avant que le semis n'empreinte : on ne devine
      pas laquelle des deux fait foi, c'est le geste de maintenance
      (`scripts/aligner_guides_plateforme.py`) qui tranche, jamais le démarrage.

    ⚠️ La base reste la source ÉDITABLE (ADR 0042) : ce n'est pas le fichier qui
    gagne, c'est le fichier qui atteint enfin une base que personne n'a touchée.
    L'empreinte posée dans `props->>'seed_sha256'` est ce qui distingue les deux —
    sans elle, le semis en insertion seule laissait une mise à jour du dépôt sans
    jamais atteindre un environnement existant (otomata-tech/oto#236).

    Décision et écriture dans la MÊME transaction, ligne verrouillée (`FOR UPDATE`) :
    une édition admin concurrente ne peut pas se glisser entre le constat et l'écrasement.
    """
    with _connect() as conn:
        courant = conn.execute(
            f"SELECT id, props FROM nodes WHERE public_id = {_PID} FOR UPDATE",
            (scope, str(owner_id), slug),
        ).fetchone()
        if courant is None:
            row = conn.execute(
                f"INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
                f"VALUES ({_PID}, '{_KIND}', %s, %s, "
                "        jsonb_build_object('slug', %s::text, 'delivery', 'on-demand', "
                "                           'title', %s::text, 'description', %s::text, "
                "                           'body_md', %s::text, 'embed_dirty', TRUE, "
                "                           'seed_sha256', %s::text)) "
                "ON CONFLICT ON CONSTRAINT nodes_public_id_key DO NOTHING RETURNING id",
                (scope, str(owner_id), slug, scope, str(owner_id),
                 slug, title, description, body_md, seed_sha256),
            ).fetchone()
            if row is None:                   # course perdue : quelqu'un vient de poser
                return "inchange"
            _maintain_projections(conn, row, body_md)
            return "seme"

        props = courant["props"] or {}
        if props.get("delivery") != "on-demand":
            raise SemisConflitDeLivraison(
                f"`{slug}` (scope {scope}) porte une couche `{props.get('delivery')}` : "
                "le semis n'écrit pas par-dessus un readme injecté.")
        posee = (props.get("seed_sha256") or "").strip()
        if not posee:
            return "sans_empreinte"
        if posee != empreinte_de_couche(props.get("title") or "",
                                        props.get("description") or "",
                                        props.get("body_md") or ""):
            return "diverge"
        if posee == seed_sha256:
            return "inchange"
        _ecrire_le_semis(conn, courant["id"], title, description, body_md, seed_sha256)
        return "mis_a_jour"


def _ecrire_le_semis(conn, node_id: int, title: str, description: str,
                     body_md: str, seed_sha256: str) -> None:
    """Écrit la prose du fichier ET son empreinte sur un nœud existant, projections
    comprises. Une seule écriture pour les deux appelants (le semis de démarrage et
    le geste de maintenance) : une empreinte posée sans la prose qu'elle décrit
    ferait croire au démarrage suivant que la base est à jour."""
    row = conn.execute(
        "UPDATE nodes SET props = props || jsonb_build_object("
        "      'title', %s::text, 'description', %s::text, 'body_md', %s::text, "
        "      'seed_sha256', %s::text, 'embed_dirty', TRUE), "
        "  updated_at = NOW() "
        f"WHERE id = %s RETURNING {_COLS}",
        (title, description, body_md, seed_sha256, node_id),
    ).fetchone()
    _maintain_projections(conn, row, body_md)


def aligner_guide_db(scope: str, owner_id: str, slug: str, body_md: str,
                     title: str = "", description: str = "", *,
                     seed_sha256: str) -> bool:
    """Aligne INCONDITIONNELLEMENT une couche sur le fichier, empreinte comprise.

    ⚠️ Écrase ce que la base porte — c'est le geste de maintenance
    (`scripts/aligner_guides_plateforme.py`), joué à la main, après avoir MONTRÉ
    l'écart. Le démarrage, lui, ne fait jamais ça : il conserve et signale.
    `False` = aucune ligne à ce slug."""
    with _connect() as conn:
        courant = conn.execute(
            f"SELECT id FROM nodes WHERE public_id = {_PID} "
            "AND props->>'delivery' = 'on-demand' FOR UPDATE",
            (scope, str(owner_id), slug),
        ).fetchone()
        if courant is None:
            return False
        _ecrire_le_semis(conn, courant["id"], title, description, body_md, seed_sha256)
        return True


def delete_guide_db(scope: str, owner_id: str, slug: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM nodes WHERE public_id = {_PID} "
            "AND props->>'delivery' = 'on-demand'",
            (scope, str(owner_id), slug),
        )
        return (cur.rowcount or 0) > 0


# --- Init (readme injecté au handshake) : delivery='init' UNIQUEMENT ----------

def get_init_guide_db(scope: str, owner_id: str, slug: str) -> Optional[dict]:
    """Le readme INIT d'un (scope, owner, slug), ou None. `{body_md, updated_at, …}`."""
    return _get_one(scope, owner_id, slug, "init")


def set_init_guide_db(scope: str, owner_id: str, slug: str,
                      body_md: str) -> Optional[dict]:
    """Upsert d'un readme INIT (édition admin/org/user). Corps vide = readme effacé,
    la ligne reste (comme les ex-tables).

    **Renvoie `None` quand la clé est déjà occupée par une couche `on-demand`** — rien
    n'a alors été écrit. Symétrique de `set_guide_db` : la même clé dérivée sert les
    deux livraisons, donc le sens qui n'a pas été signalé le 09/09 est tout aussi
    ouvert. Il est plus étroit (les slugs d'init sont canoniques : `readme` pour
    org/group/user, la clé du bloc pour la plateforme), mais un guide on-demand nommé
    `readme` rendrait sinon le readme injecté de ce périmètre silencieusement
    remplaçable par son écriture — et réciproquement."""
    with _connect() as conn:
        previous_body = _body_before_write(conn, scope, owner_id, slug)
        row = conn.execute(
            f"INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            f"VALUES ({_PID}, '{_KIND}', %s, %s, "
            "        jsonb_build_object('slug', %s::text, 'delivery', 'init', "
            "                           'body_md', %s::text)) "
            "ON CONFLICT ON CONSTRAINT nodes_public_id_key DO UPDATE SET "
            "  props = nodes.props || jsonb_build_object('body_md', %s::text), "
            "  updated_at = NOW() "
            "  WHERE nodes.props->>'delivery' = 'init' "
            f"RETURNING {_COLS}",
            (scope, str(owner_id), slug, scope, str(owner_id),
             slug, body_md or "", body_md or ""),
        ).fetchone()
        if row is None:                       # une couche `on-demand` occupe la clé
            return None
        _maintain_projections(conn, row, (body_md or "") if body_md != previous_body else None)
        return dict(row)


def seed_init_guide_db(scope: str, owner_id: str, slug: str, body_md: str) -> None:
    """Pose le défaut d'un readme INIT s'il n'existe pas (boot, idempotent). Ne touche
    JAMAIS une ligne déjà éditée."""
    with _connect() as conn:
        row = conn.execute(
            f"INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            f"VALUES ({_PID}, '{_KIND}', %s, %s, "
            "        jsonb_build_object('slug', %s::text, 'delivery', 'init', "
            "                           'body_md', %s::text)) "
            "ON CONFLICT ON CONSTRAINT nodes_public_id_key DO NOTHING RETURNING id",
            (scope, str(owner_id), slug, scope, str(owner_id), slug, body_md or ""),
        ).fetchone()
        if row is not None:
            _maintain_projections(conn, row, body_md or "")
