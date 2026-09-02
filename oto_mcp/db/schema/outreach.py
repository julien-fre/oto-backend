"""DDL du domaine « outreach » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`.

**Deux tables, deux garde-fous qui ne se rappellent à personne.**

`outreach_sends` porte un **index unique partiel** `(campaign, sub)` sur les envois
réels : « ne pas relancer deux fois la même personne » n'est pas une consigne qu'un
opérateur doit se souvenir d'appliquer, c'est une contrainte que la base refuse de
violer. Le corollaire est que l'insertion précède l'envoi (cf. `db/outreach.py`) :
une ligne perdue vaut mieux qu'un doublon dans une boîte mail.

`outreach_optouts` est **par compte, pas par campagne** : un refus vise le canal,
pas un message. Une table à part plutôt qu'une colonne sur `users` parce qu'elle
porte sa propre date et sa provenance, et qu'un `DELETE` la lève sans toucher à
l'identité.
"""
from __future__ import annotations

OUTREACH = """
-- Journal des relances de plateforme adressées à un COMPTE (jamais à une org : on
-- écrit à une personne, pas à un espace de travail — cf. `db/outreach.py`).
CREATE TABLE IF NOT EXISTS outreach_sends (
    id BIGSERIAL PRIMARY KEY,
    campaign TEXT NOT NULL,                 -- slug de campagne, ex 'onboarding-2026-09'
    sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    to_email TEXT NOT NULL,                 -- l'adresse SERVIE (figée : elle peut changer après)
    locale TEXT NOT NULL,                   -- 'fr' | 'en' : la langue réellement servie
    -- 'test' = l'essai que l'opérateur s'envoie à LUI-MÊME ; 'send' = l'envoi réel.
    -- Le premier est ce qui autorise le second (`db.outreach.essai_valide`) : sans
    -- essai portant la MÊME empreinte de contenu, l'envoi en masse est refusé.
    kind TEXT NOT NULL DEFAULT 'send',
    -- sha256 du contenu servi (sujet + corps + CTA, les deux langues). Ce qui lie
    -- l'essai à l'envoi : retoucher une virgule après l'essai invalide l'essai.
    fingerprint TEXT NOT NULL,
    sent_by TEXT,                           -- l'opérateur qui a déclenché
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- L'unicité ne porte QUE sur les envois réels : on peut se refaire dix essais.
CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_once
    ON outreach_sends(campaign, sub) WHERE kind = 'send';
CREATE INDEX IF NOT EXISTS idx_outreach_campaign ON outreach_sends(campaign, sent_at DESC);

-- Refus de recevoir nos relances. Par COMPTE et pour TOUTE campagne : un refus vise
-- le canal, jamais un message. Posé par le lien de désinscription du mail (anonyme,
-- jeton signé) ou par un opérateur.
CREATE TABLE IF NOT EXISTS outreach_optouts (
    sub TEXT PRIMARY KEY REFERENCES users(sub) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'link',    -- 'link' (le destinataire) | 'operator'
    opted_out_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
