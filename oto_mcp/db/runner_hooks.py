"""Les LIVRAISONS d'un déclencheur par webhook — ce qu'un tiers a envoyé, et ce
qu'on en a fait.

Une ligne par appel, accepté ou non. Trois lecteurs, un seul écrivain (la route
`/api/hooks`) :

1. le **lissage** — combien de livraisons dans l'heure écoulée, pour savoir si
   celle-ci part tout de suite ou plus tard ;
2. l'**écran** — qui a appelé, quand, et quel déroulé en est sorti. Sans lui, une
   source mal configurée est un mystère plutôt qu'un diagnostic : c'est la leçon
   de `expired_count` sur un déclencheur programmé, où quarante-et-une occurrences
   perdues n'ont été découvertes qu'en préparant autre chose ;
3. le **refus visible** — l'appelant reçoit un 404 sans oracle, le propriétaire
   lit ici « secret périmé » sur son propre écran.

⚠️ **Le corps reçu n'est pas stocké ici.** Il voyage dans la charge du travail
(`runner_jobs.payload`), déjà domicile de ce qu'un travail emporte. Le garder deux
fois doublerait le volume ET la surface de fuite d'une donnée tierce.

⚠️ **Aucune déduplication** dans ce lot (décidé le 12/09/2026) : pas de clé de
livraison, pas de contrainte d'unicité. Ce qui la remplace est l'ACQUITTEMENT
RAPIDE — un envoyeur retente surtout sur un délai d'attente, et une réponse en
quelques millisecondes ne lui en laisse pas. Ce qui reste possible est dit sans
détour : une coupure réseau après notre écriture fait un second déroulé.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

#: Ce qu'une livraison est devenue. `queued`/`delayed` ont produit un travail ; les
#: `refused_*` n'en produisent aucun et gardent leur motif — c'est ce motif qui rend
#: une source mal branchée réparable.
#: ⚠️ Un mauvais secret ne s'écrit PAS, et c'est une conséquence, pas un oubli :
#: journaliser exigerait de retrouver le déclencheur SANS son secret, donc de
#: faire de cette route un oracle sur les identifiants qui existent. Le refus est
#: muet des deux côtés — l'appelant voit un 404, le propriétaire ne voit rien.
QUEUED, DELAYED = "queued", "delayed"
REFUSE_PAUSED, REFUSE_TOO_LARGE, REFUSE_RATE = (
    "refused_paused", "refused_too_large", "refused_rate")


def verrouiller_le_declencheur(conn, trigger_id: int) -> None:
    """Prend le verrou de CE déclencheur pour le reste de la transaction.

    ⚠️ **Sans lui, le lissage ne lisse rien au moment exact où il sert.** Une
    rafale est CONCURRENTE par définition : en READ COMMITTED, un `SELECT COUNT`
    ne verrouille rien, donc deux cents livraisons simultanées lisent toutes le
    même compte (zéro), se croient toutes sous le débit, et partent toutes en même
    temps. Le compteur aurait l'air juste après coup, et la rafale serait passée
    entière — la panne que ce mécanisme existe pour empêcher.

    Le verrou est pris sur la LIGNE du déclencheur, donc deux sources différentes
    ne se gênent jamais ; seules les livraisons d'un même agent se sérialisent, ce
    qui est précisément la file qu'on veut ordonner. La transaction qui suit est
    courte (un compte, un INSERT, un INSERT).
    """
    conn.execute("SELECT id FROM runner_triggers WHERE id = %s FOR UPDATE",
                 (trigger_id,)).fetchone()


def compter_dans_la_fenetre(conn, trigger_id: int, secondes: int) -> int:
    """Combien de livraisons ENFILÉES ce déclencheur a produites dans la fenêtre.

    ⚠️ Prend la connexion de l'appelant : cette lecture et l'écriture qui la suit
    vivent dans UNE transaction — et l'appelant a pris `verrouiller_le_declencheur`
    AVANT, sans quoi deux livraisons simultanées liraient toutes deux le même
    compte périmé.

    ⚠️ Ne compte que ce qui a ENFILÉ (`queued`/`delayed`) : un refus n'a rien
    coûté, et le faire compter dans le lissage ferait retarder des travaux
    légitimes à cause d'une source qui présente un mauvais secret.
    """
    row = conn.execute(
        """
        SELECT COUNT(*)::int AS n FROM runner_hook_deliveries
         WHERE trigger_id = %s
           AND outcome IN (%s, %s)
           AND received_at > NOW() - make_interval(secs => %s)
        """,
        (trigger_id, QUEUED, DELAYED, secondes),
    ).fetchone()
    return int(row["n"]) if row else 0


def enregistrer(conn, trigger_id: int, org_id: int, outcome: str,
                job_id: Optional[int] = None, source: Optional[str] = None) -> int:
    """Écrit la livraison. Rend son id.

    Prend aussi la connexion de l'appelant : la livraison et le travail qu'elle
    produit sont posés ENSEMBLE ou pas du tout. Sans ça, un incident entre les
    deux laisserait soit un travail qu'aucune livraison n'explique, soit une
    livraison qui prétend avoir enfilé un travail qui n'existe pas.
    """
    row = conn.execute(
        """
        INSERT INTO runner_hook_deliveries (trigger_id, org_id, outcome, job_id, source)
             VALUES (%s, %s, %s, %s, %s)
          RETURNING id
        """,
        (trigger_id, org_id, outcome, job_id, (source or None)),
    ).fetchone()
    return int(row["id"])


def livraisons(trigger_id: int, org_id: int, limit: int = 50) -> list[dict]:
    """Ce que ce déclencheur a reçu, du plus récent au plus ancien — l'écran.

    Org-scopé : un déclencheur d'une autre org rend une liste vide, jamais les
    livraisons d'autrui.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trigger_id, received_at, outcome, job_id, source
              FROM runner_hook_deliveries
             WHERE trigger_id = %s AND org_id = %s
             ORDER BY id DESC
             LIMIT %s
            """,
            (trigger_id, org_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [dict(r) for r in rows]


def comptage_livraisons(trigger_id: int, org_id: int) -> dict:
    """Ce que l'écran dit AU-DESSUS de la liste : combien reçues sur 24 h, combien
    refusées, et la dernière. ⚠️ `0` est un vrai zéro (rien n'est arrivé), jamais
    une absence de mesure — même règle que `expired_count`."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS recues_24h,
                   COUNT(*) FILTER (WHERE outcome LIKE 'refused%%')::int AS refusees_24h,
                   MAX(received_at) AS derniere
              FROM runner_hook_deliveries
             WHERE trigger_id = %s AND org_id = %s
               AND received_at > NOW() - INTERVAL '24 hours'
            """,
            (trigger_id, org_id),
        ).fetchone()
    d = dict(row) if row else {}
    return {"recues_24h": d.get("recues_24h") or 0,
            "refusees_24h": d.get("refusees_24h") or 0,
            "derniere": d.get("derniere")}
