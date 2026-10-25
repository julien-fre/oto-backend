"""DDL du domaine « apps » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul.

**Une app est un écran bâti par un utilisateur sur Tulina** (`oto_apps`, Tulina doc
5429) : un manifeste (les tableaux, champs, agents qu'elle touche), du HTML, une
vignette. Elle appartient à un PROJET et en hérite l'accès, comme une page — mais ce
n'est PAS une page de mémoire : elle n'entre ni dans l'arbre des pages, ni dans la
recherche, ni dans la base de connaissance. Ses révisions sont à part
(`app_revisions`), comme celles des pages.
"""
from __future__ import annotations

APPS = """
-- Une app : un écran rangé dans un projet, dont il hérite l'accès.
CREATE TABLE IF NOT EXISTS apps (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    -- Ce que l'app peut toucher : tableaux et champs lus, écritures permises, agents
    -- lus ou lancés, dépôt de fichiers. Validé au serveur à chaque écriture.
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    html TEXT NOT NULL DEFAULT '',
    -- Image publiée sur le stockage média, ou couleur + emoji, et les réglages du
    -- dessin qui l'a produite. NULL = la tuile par défaut.
    thumbnail JSONB,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Le dernier auteur : celui dont les droits bornent l'app (auteur ∩ lecteur).
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS apps_project_idx ON apps (project_id) WHERE archived_at IS NULL;

-- Chaque état ANTÉRIEUR d'une app, écrit avant chaque mise à jour : de quoi restaurer.
CREATE TABLE IF NOT EXISTS app_revisions (
    id BIGSERIAL PRIMARY KEY,
    app_id BIGINT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    manifest JSONB NOT NULL,
    html TEXT NOT NULL,
    thumbnail JSONB,
    edited_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS app_revisions_app_idx ON app_revisions (app_id, id DESC);
"""
