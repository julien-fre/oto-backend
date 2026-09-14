"""Amorçage d'Alembic — branché sur la MÊME source que l'application.

Deux règles tiennent ce fichier.

**Le DSN vient de `DATABASE_URL`**, comme le pool applicatif (`db/_conn.py`). Il n'y
a pas de seconde vérité sur « quelle base » : un outil de migration qui lit sa
connexion ailleurs finit un jour par migrer la mauvaise.

**Toute migration prend un verrou consultatif avant d'écrire.** Alembic n'en pose
AUCUN par défaut : deux processus qui migrent en même temps se marcheraient dessus.
Ici ce n'est pas théorique — la base est partagée entre la préproduction et la
production, et le déploiement est bleu/vert (deux arbres, deux services).

Il n'y a pas de métadonnées cibles : **aucun ORM**. Les migrations s'écrivent en SQL,
à la main, comme le reste du schéma. La détection automatique est donc inopérante —
et c'est voulu : elle ne saurait de toute façon qu'ajouter des colonnes.

⚠️ **Ce fichier est le SCRIPT qu'Alembic exécute, pas une bibliothèque.** Sous la
commande `alembic`, `context` porte une configuration réelle. Importé autrement — un
outil qui balaie `oto_mcp/` pour vérifier que tout s'importe (`scripts/arbre-
importable.py`) en fait partie, puisque ce module vit DANS le paquet — `context` est
vide et `context.config` lève `AttributeError` (vécu le 14/09/2026, à la pose
d'Alembic). Un import n'a le droit d'exiger RIEN de l'environnement
(docs/commands.md) ; `_sous_alembic()` en est la garde, pour que la même contrainte
tienne ici comme partout ailleurs dans le paquet."""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, text

# Clé du verrou consultatif. Arbitraire, mais FIGÉE : la changer ouvrirait une
# seconde file qui ignorerait la première.
VERROU = 0x07050065

target_metadata = None


def _sous_alembic() -> bool:
    """Vrai seulement si CE fichier tourne sous `alembic` — jamais lors d'un import
    générique, où `context` n'a reçu aucune configuration."""
    try:
        context.config
    except AttributeError:
        return False
    return True


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set (managed PG connection string)")
    # libpq accepte `postgres://`, SQLAlchemy veut son pilote nommé.
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def run_migrations_offline() -> None:
    """Mode `--sql` : imprime ce qui serait exécuté, sans ouvrir de connexion.

    C'est l'essai à blanc : on lit le SQL avant de l'appliquer à une base qui est
    aussi celle de la production.
    """
    context.configure(
        url=_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:cle)"), {"cle": VERROU})
        # ⚠️ Ce commit n'est PAS décoratif, et il a coûté un essai pour le voir.
        # Prendre le verrou ouvre une transaction implicite ; Alembic voit alors une
        # transaction déjà en cours, sa propre `begin_transaction()` ne fait plus rien,
        # et TOUT ce qu'il écrit est annulé à la fermeture de la connexion — sans une
        # erreur, code de sortie zéro. Le succès déguisé parfait. On referme donc la
        # transaction du verrou tout de suite : le verrou, lui, vit au niveau de la
        # SESSION et survit au commit.
        connection.commit()
        try:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            # Le verrou tombe aussi à la fermeture de la connexion ; on le rend
            # explicitement pour que le cas « la connexion vit encore » soit couvert.
            connection.execute(text("SELECT pg_advisory_unlock(:cle)"), {"cle": VERROU})
            connection.commit()


if _sous_alembic():
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
