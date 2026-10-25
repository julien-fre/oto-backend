"""Le store de l'agent intégré (`docs/copilot.md`) : conversations et annulations.

Trois règles tenues ici plutôt qu'en prose :

1. **Une conversation appartient à son `sub`.** Toute lecture qui sert une requête passe
   par `lire(thread_id, sub)` : un autre compte reçoit `None`, exactement comme une
   conversation qui n'existe pas — pas d'oracle d'existence.
2. **Une étape à la fois** (`prendre` / `rendre`) : le verrou est une ligne de la base,
   pas un drapeau en mémoire — bleu, vert et préprod partagent la même base, deux
   réponses à la même carte peuvent arriver sur deux processus. Il a une échéance :
   un processus mort en pleine étape ne bloque pas la conversation à jamais.
3. **Une annulation se consomme UNE fois** (`consommer`), atomiquement, et pas après
   son échéance.
"""
from __future__ import annotations

import json
from typing import Optional

from ._conn import _connect

#: Les colonnes JSON d'une conversation que la boucle réécrit à chaque étape.
CHAMPS_JSON = ("messages", "queue", "results", "pending", "remembered", "client_tools")
_CHAMPS_SIMPLES = ("project_id", "run_id", "model_calls", "mode")


def creer(thread_id: str, sub: str, org_id: Optional[int], project_id: Optional[int],
          system: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO copilot_threads (id, sub, org_id, project_id, system) "
            "VALUES (%s, %s, %s, %s, %s)",
            (thread_id, sub, org_id, project_id, system))


def lire(thread_id: str, sub: str) -> Optional[dict]:
    """La conversation, si elle est à `sub` ; sinon `None` (même réponse qu'absente)."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM copilot_threads WHERE id = %s AND sub = %s",
            (thread_id, sub)).fetchone()


def enregistrer(thread_id: str, champs: dict) -> None:
    """Réécrit l'état de la conversation (les seuls champs nommés) et la date de vie."""
    poses, valeurs = [], []
    for k, v in champs.items():
        if k in CHAMPS_JSON:
            poses.append(f"{k} = %s::jsonb")
            valeurs.append(json.dumps(v, ensure_ascii=False))
        elif k in _CHAMPS_SIMPLES:
            poses.append(f"{k} = %s")
            valeurs.append(v)
        else:
            raise ValueError(f"champ de conversation inconnu : {k}")
    poses.append("last_seen = NOW()")
    with _connect() as conn:
        conn.execute(f"UPDATE copilot_threads SET {', '.join(poses)} WHERE id = %s",
                     (*valeurs, thread_id))


def prendre(thread_id: str, jeton: str, secondes: int) -> bool:
    """Prend le verrou d'étape s'il est libre (ou échu). Vrai = pris."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE copilot_threads SET busy_token = %s, "
            "busy_until = NOW() + make_interval(secs => %s) "
            "WHERE id = %s AND (busy_until IS NULL OR busy_until < NOW()) RETURNING id",
            (jeton, secondes, thread_id)).fetchone()
    return row is not None


def rendre(thread_id: str, jeton: str) -> None:
    """Rend le verrou — seulement s'il est encore à ce jeton."""
    with _connect() as conn:
        conn.execute(
            "UPDATE copilot_threads SET busy_token = NULL, busy_until = NULL "
            "WHERE id = %s AND busy_token = %s", (thread_id, jeton))


def ouvertes_recentes(sub: str, minutes: int) -> int:
    """Combien de conversations de `sub` ont vécu ces `minutes` dernières."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM copilot_threads WHERE sub = %s "
            "AND last_seen > NOW() - make_interval(mins => %s)", (sub, minutes)).fetchone()
    return int(row["n"])


def inactives_avec_run(sub: str, minutes: int, limite: int = 20) -> list[dict]:
    """Les conversations de `sub` sans vie depuis `minutes` qui tiennent encore un run."""
    with _connect() as conn:
        return conn.execute(
            "SELECT id, run_id, org_id FROM copilot_threads WHERE sub = %s "
            "AND run_id IS NOT NULL AND last_seen < NOW() - make_interval(mins => %s) "
            "AND (busy_until IS NULL OR busy_until < NOW()) LIMIT %s",
            (sub, minutes, limite)).fetchall()


def oublier_run(thread_id: str, run_id: str) -> None:
    """La conversation ne tient plus ce run (il a été clos)."""
    with _connect() as conn:
        conn.execute("UPDATE copilot_threads SET run_id = NULL WHERE id = %s AND run_id = %s",
                     (thread_id, run_id))


def offrir_annulation(undo_id: str, thread_id: str, sub: str, label: str, kind: str,
                      payload: dict, secondes: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO copilot_undos (id, thread_id, sub, label, kind, payload, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW() + make_interval(secs => %s))",
            (undo_id, thread_id, sub, label, kind, json.dumps(payload, ensure_ascii=False),
             secondes))


def consommer(undo_id: str, sub: str) -> Optional[dict]:
    """L'annulation, marquée utilisée dans le même ordre — `None` si elle n'est pas à
    `sub`, déjà utilisée ou échue. Une fois, même si défaire échoue ensuite à moitié."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE copilot_undos u SET used_at = NOW() FROM copilot_threads t "
            "WHERE u.id = %s AND u.sub = %s AND u.used_at IS NULL "
            "AND u.expires_at > NOW() AND t.id = u.thread_id "
            "RETURNING u.id, u.thread_id, u.label, u.kind, u.payload, t.org_id, "
            "t.project_id, t.run_id", (undo_id, sub)).fetchone()


def prolonger(thread_id: str, jeton: str, secondes: int) -> bool:
    """Repousse l'échéance du verrou tenu par ce jeton (une étape longue)."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE copilot_threads SET busy_until = NOW() + make_interval(secs => %s) "
            "WHERE id = %s AND busy_token = %s RETURNING id",
            (secondes, thread_id, jeton)).fetchone()
    return row is not None


def schema_stocke(ns_id: int) -> Optional[dict]:
    """Le schéma d'un tableau tel qu'il est STOCKÉ (sans projection de face), pour
    comparer avant de défaire une modification de champs."""
    with _connect() as conn:
        row = conn.execute("SELECT schema FROM user_datastores WHERE id = %s",
                           (int(ns_id),)).fetchone()
    return row["schema"] if row else None


def tableau_vide(ns_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT EXISTS (SELECT 1 FROM datastore_rows WHERE ns_id = %s) "
                           "AS plein", (int(ns_id),)).fetchone()
    return not row["plein"]
