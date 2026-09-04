"""DDL du domaine « portée » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ. Les évolutions de
colonnes sur tables existantes vivent dans `_init.init_db`, pas ici
(`docs/live-migrations.md`).

**Une table, et elle n'envoie rien.**

`portee_elargissements` enregistre les moments où un AGENT fait sortir un contenu du
périmètre de son propriétaire (ADR 0068 §4). Elle est née en **période d'observation**,
décision d'Alexis du 04/09/2026 : on veut d'abord voir *combien* d'alertes partiraient
et *à qui*, avant d'en envoyer une seule. Chaque ligne porte donc les destinataires
qu'elle AURAIT prévenus (`destinataires`) et l'urgence qu'elle aurait eue
(`immediat`) — sans qu'aucun message ne parte.

⚠️ **Ce n'est pas un journal d'audit et ça ne doit pas le devenir.** `tool_calls` trace
déjà tous les appels, avec sa rétention et ses trois lentilles. Cette table-ci ne
retient que les gestes qui CHANGENT QUI VOIT, et son objet est de nourrir une
notification. Y verser autre chose la rendrait aussi illisible que le journal qu'elle
existe pour ne pas remplacer — « journaliser n'est pas avertir » vaut dans les deux
sens.

⚠️ **`corps` ne contient jamais le contenu élargi.** On enregistre CE QUI a bougé (le
type, l'id, le nom), pas ce qu'il y a dedans : cette table sera lue par des humains qui
n'ont pas forcément accès à la ressource, et une trace de sécurité qui recopie le
secret qu'elle surveille est un second exemplaire à protéger.
"""
from __future__ import annotations

PORTEE = """
-- Un agent a élargi la portée d'un contenu (ADR 0068). Période d'OBSERVATION :
-- ces lignes décrivent des notifications qui ne sont pas envoyées.
CREATE TABLE IF NOT EXISTS portee_elargissements (
    id BIGSERIAL PRIMARY KEY,
    -- Qui a fait le geste, et sous quelle org de travail.
    acteur_sub TEXT NOT NULL,
    org_id BIGINT,
    -- Ce qui a bougé : la famille ('project', 'datastore_namespace', 'doc', 'node'),
    -- son identifiant tel que la surface le manipule, et son nom lisible.
    ressource_type TEXT NOT NULL,
    ressource_id TEXT NOT NULL,
    ressource_nom TEXT,
    -- Le propriétaire AVANT le geste : c'est lui qui subit l'élargissement.
    proprietaire_sub TEXT,
    -- Vers quoi ça s'est ouvert : 'org' | 'group' | 'person' | 'public' | 'secret'.
    -- 'public'/'secret' = lisible sans login, la seule catégorie urgente.
    vers TEXT NOT NULL,
    -- La cible nommée quand il y en a une (id d'org, d'équipe, adresse d'une personne).
    cible TEXT,
    -- Le geste, tel qu'un humain le lit : « oto_resource op=share », « op=copy ».
    geste TEXT NOT NULL,
    -- Les subs qui AURAIENT été prévenus, en JSON. Le propriétaire ET l'auteur du
    -- geste (choix d'Alexis) — ils se confondent souvent, et c'est le cas nominal.
    destinataires JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- true = serait parti tout de suite (ouverture sans login) ; false = aurait été
    -- groupé dans un récap de vague.
    immediat BOOLEAN NOT NULL DEFAULT false,
    -- Posé le jour où l'on commencera à envoyer pour de vrai. NULL pendant
    -- l'observation, et c'est ce NULL qui dit qu'aucun message n'est parti.
    notifie_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- La lecture d'observation : « qu'est-ce qui serait parti, et à qui, cette semaine ».
CREATE INDEX IF NOT EXISTS idx_portee_created ON portee_elargissements (created_at DESC);
-- « Que se serait-il passé pour CETTE personne » — la question qu'on posera d'abord.
CREATE INDEX IF NOT EXISTS idx_portee_proprietaire
    ON portee_elargissements (proprietaire_sub, created_at DESC);
"""
