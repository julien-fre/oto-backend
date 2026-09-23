"""DDL du domaine « prose de plateforme » — fragment assemblé par `db/_schema.py`.

Il ne reste ici que `platform_instructions` (blocs A/B, #50) : la table `guides`
a été retirée du code le 23/09/2026 (oto#239), ses lignes vivent dans `nodes`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# instructions plateforme (#50). Les guides, eux, sont des nœuds (lot M1).
GUIDES = """
-- Instructions injectées AU NIVEAU PLATEFORME (#50, bloc A « secret sauce » +
-- bloc B « onboarding »). Singleton par `key` ('secret_sauce' | 'onboarding').
-- Éditable seulement par l'admin plateforme (inviolable par l'org — frontière
-- plateforme/org nette). Seedé au boot depuis les constantes de `instructions.py`
-- (INSERT ON CONFLICT DO NOTHING) → le code reste le défaut/fallback, la DB porte
-- l'override éditable. En CLAIR (prose, pas un credential).
CREATE TABLE IF NOT EXISTS platform_instructions (
    key TEXT PRIMARY KEY,                       -- 'secret_sauce' (bloc A) | 'onboarding' (bloc B)
    body_md TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

-- ⚠️ La table `guides` a été RETIRÉE DU CODE le 23/09/2026 (otomata-tech/oto#239).
-- Ses lignes vivent dans `nodes` depuis le lot M1 (blueprint ADR 0054/0063) : une
-- couche de contexte EST une page, `delivery` n'est qu'une propriété. Plus rien ici
-- ne la crée, ne l'altère, ne l'indexe ni ne la recopie — un `CREATE TABLE IF NOT
-- EXISTS` laissé en place la faisait RENAÎTRE au démarrage suivant un `DROP`, et le
-- boot continuait d'écrire dans une table que plus rien ne lit pour servir.
-- Le `DROP TABLE guides` lui-même n'est PAS ici : DDL non additive sur une base
-- partagée, jamais au démarrage — décision d'Alexis, exécutée par l'opérationnel
-- une fois ce code livré en production (docs/live-migrations.md, « la danse en N lots »).
"""
