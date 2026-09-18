"""DDL du domaine « fonctions » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ.

**Une fonction est du code pur, stocké et exécuté par Oto** (ADR 0073) : un calcul
propre à un client — entrée JSON, sortie JSON, avertissements et fichiers — qui ne lit
rien d'Oto, n'appelle aucun réseau et ne voit aucun secret.

⚠️ **Des tables à part, pas des nœuds — pour cette première version.** 0073 §D2 range
une fonction comme une page de rôle `fonction`. Les pages natives n'ont encore ni
versions ni verrou (ADR 0072, proposée) : y greffer la fonction aurait touché le
contrat servi au front (`edit_surface`) pour un objet qui n'y est pas encore montré.
Le rattachement au modèle de contenu viendra avec les notions communes de 0072.

**Deux tables.** `functions` porte l'IDENTITÉ (propriétaire, slug, titre) et la version
publiée ; `function_versions` porte chaque VERSION, proposée, publiée ou refusée, avec
son code. Le code d'une version ne change jamais : corriger, c'est proposer la suivante.
"""
from __future__ import annotations

FUNCTIONS = """
-- Une fonction (ADR 0073) : l'identité et le pointeur vers la version publiée.
CREATE TABLE IF NOT EXISTS functions (
    id BIGSERIAL PRIMARY KEY,
    -- Le propriétaire, comme toute ressource possédée (ADR 0049) : 'org' | 'group' | 'user'.
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    -- La référence lisible, unique chez son propriétaire : c'est elle que la procédure
    -- cite. Elle ne se renomme pas : un slug qui change casse les procédures qui l'appellent.
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- La version qui s'exécute. NULL = rien de publié : la fonction existe, elle ne tourne pas.
    published_version INTEGER,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_type, owner_id, slug)
);

-- Une version d'une fonction. IMMUABLE une fois écrite : seul son statut change.
CREATE TABLE IF NOT EXISTS function_versions (
    id BIGSERIAL PRIMARY KEY,
    function_id BIGINT NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    -- 'proposee' : écrite, pas encore jugée · 'publiee' : jugée bonne (tests verts)
    -- · 'refusee' : jugée mauvaise, gardée pour la trace.
    status TEXT NOT NULL DEFAULT 'proposee'
        CHECK (status IN ('proposee', 'publiee', 'refusee')),
    -- Les fichiers, `{nom: contenu}` : modules Python, tests (`test_*.py`), données.
    sources JSONB NOT NULL,
    -- `module:fonction`, appelée avec l'entrée et rendant {result, warnings, files}.
    entrypoint TEXT NOT NULL,
    -- Les dépendances demandées, prises dans la liste blanche de l'exécuteur.
    requirements TEXT[] NOT NULL DEFAULT '{}',
    -- Pourquoi cette version : ce qu'elle corrige ou ajoute, dit par qui la propose.
    note TEXT,
    proposed_by TEXT,
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Qui a publié ou refusé, quand, et le compte rendu des tests à ce moment-là.
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    test_report JSONB,
    UNIQUE (function_id, version)
);
"""
