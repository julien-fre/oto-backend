"""DDL du domaine « nodes » — fragment du schéma assemblé par `db/_schema.py`.

Ce module ne porte QUE du DDL, en chaînes SQL, et n'est jamais exécuté seul :
`_schema._SCHEMA` concatène tous les domaines dans un ordre FIGÉ (les FK en
dépendent — une table référencée doit être créée avant celle qui la référence).
Changer l'ordre, c'est éditer `_schema.ASSEMBLAGE`, pas ce fichier.

Les évolutions de colonnes sur tables EXISTANTES ne vivent pas ici mais dans
`_init.init_db` (ALTER idempotents) — cf. `docs/live-migrations.md`, en
particulier le piège du `CREATE INDEX` sur une colonne ajoutée par migration.
"""
from __future__ import annotations

# nœuds de contenu et blocs
NODES = """
-- NŒUDS (blueprint ADR 0054 + 0063, lot M1) — la table de contenu UNIQUE vers
-- laquelle convergent pages, projets, tableaux, lignes et couches de contexte.
-- Neuve, et pas une extension de `docs` (0063-D1) : la table des pages porte une
-- contrainte contraire au modèle (`project_id NOT NULL`) et un ownership qui vit
-- sur le projet — les traîner dans chaque requête pendant des mois coûterait plus
-- que la conversion.
--
-- ⚠️ CE QUE CE LOT FAIT DISPARAÎTRE, et qu'un lecteur futur ne devinera pas en
-- lisant le code : **le concept de « guide »**. L'ADR 0055-D4 pose qu'une couche
-- de contexte EST une page. Un readme d'org, un how-to plateforme, la note perso
-- d'un utilisateur : ce sont des pages (`kind='page'`), possédées par un scope.
-- Il n'existe donc AUCUN genre « guide » — et l'axe de livraison « injecté /
-- à la demande », qui était la NATURE d'une ligne de `guides` (sa colonne
-- `delivery`, ses deux jeux de fonctions, ses deux surfaces), n'est plus qu'une
-- PROPRIÉTÉ parmi d'autres : `props->>'delivery'`. La table `guides` n'a pas
-- déménagé, elle s'est dissoute. C'est le premier endroit où le modèle unique
-- devient réel — les surfaces (`oto_guide`, `/api/me/guides/*`), elles, ne
-- changent pas d'un octet : elles lisent ici à travers la même façade
-- (`db/guides.py`).
--
-- FORME MESURÉE, pas supposée : c'est la « forme B » du banc d'écriture en masse
-- (`oto-blueprint:docs/banc-ecriture-noeuds.md`), éprouvée jusqu'à un million de
-- lignes. Ne pas l'élargir sans mesure — chaque colonne se paie cent mille fois
-- sur un vivier (0063-D3, garde-fou 1) ; les champs métier vont dans `props`.
CREATE TABLE IF NOT EXISTS nodes (
    id BIGSERIAL PRIMARY KEY,
    -- 0059-D3 : la désignation OPAQUE et immuable, clé des machines (le BIGSERIAL
    -- reste interne). Une table neuve naît avec — d'où la dette du backfill à
    -- double résolution évitée avant d'être contractée.
    -- ⚠️ C'est un IDENTIFIANT, jamais une capacité : le connaître n'ouvre aucun
    -- droit (0055-D9 — les droits viennent des grants, jamais du contenu).
    -- L'unicité est une contrainte d'IDENTITÉ (un identifiant qui collisionne ne
    -- résout plus), pas un index de requête : les index de requête sont les DEUX
    -- ci-dessous, et seulement eux.
    public_id TEXT NOT NULL,
    -- L'arbre. PAS de clé étrangère : « contrainte, ou intégrité portée par le
    -- code ? » est l'arbitrage M-e du chantier, ouvert (le CASCADE hérité de
    -- `docs` rend la suppression d'un tableau ×118 plus chère sans index sur
    -- parent_id). Il se tranche avant M4, pas ici.
    parent_id BIGINT,
    -- Ordre de la fratrie : entiers espacés (0063-D2), mais **l'insertion se fait
    -- dans l'INTERVALLE entre deux voisins**, et la réindexation n'est plus qu'un
    -- rattrapage — c'est l'arbitrage M-g du chantier, tranché au lot M3 sur les
    -- chiffres du banc M0 (renuméroter 45 000 frères : 20 s ; insérer dans
    -- l'intervalle : 1,4 ms). D'où un BIGINT et un écart de 2^16 : cf.
    -- `db/nodes.midpoint`, qui porte la règle et son motif.
    position BIGINT,
    -- Ce que l'objet EST : `page` (couches de contexte, projets, pages — lots M1/M2)
    -- ou `tableau` (lot M3) ; `ligne` au lot M4. ⚠️ Le tableau a bien son genre, alors
    -- que l'ÉPINGLE n'en a pas : 0054-D4 fait d'un nœud un tableau parce qu'il déclare
    -- un schéma d'enfants, mais 29 des 83 tableaux de production n'en déclarent aucun
    -- (table libre) — la dimension ne peut donc pas discriminer. Le genre dit ce que
    -- l'objet est, la dimension ce que ses enfants portent.
    kind TEXT NOT NULL,
    owner_type TEXT NOT NULL,  -- platform | tenant | org | group | user (0049/0053)
    -- ⚠️ ÉCART ASSUMÉ avec la forme du banc, qui portait `owner_id BIGINT` : un
    -- propriétaire de scope `user` est un `sub` Logto (`users.sub` est la clé
    -- primaire, il n'existe aucun id numérique d'utilisateur), et le scope
    -- `platform` n'a pas d'id du tout. Un BIGINT obligerait donc à inventer un
    -- surrogate par utilisateur — une migration d'identité, sans rapport avec le
    -- modèle de contenu. TEXT est aussi ce que portent déjà `projects.owner_id`,
    -- `user_datastores.owner_id`, `org_instructions.owner_id` et `guides.owner_id` :
    -- le couple (owner_type, owner_id) se lit de la même façon partout.
    owner_id TEXT NOT NULL,
    -- Ce que le nœud EST pour la plateforme : titre, épingle, livraison, schéma
    -- d'enfants. Des clés qu'oto CONNAÎT et interprète.
    props JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Ce que l'utilisateur y a MIS : les valeurs des colonnes d'une ligne de
    -- tableau. Des clés dont oto ne sait rien et qu'il ne doit pas interpréter.
    --
    -- ⚠️ **Deux natures, deux colonnes, et ce n'est pas du rangement.** Mêlées dans
    -- `props`, une donnée utilisateur nommée `title` ou `position` écrase le sens du
    -- nœud, et toute lecture doit connaître la liste des clés réservées pour faire
    -- le tri. La frontière est celle du datastore — oto gère les types standards,
    -- jamais l'interprétation métier d'une valeur : elle mérite une colonne, pas
    -- une convention de nommage.
    --
    -- **MESURÉ, comme le garde-fou 1 l'exige** (banc du 2026-09-01, 200 000
    -- lignes-tableau de six champs métier, deux passes en ordre inversé) : la forme
    -- élargie — `data` plus les trois colonnes de bail — pèse **46,5 Mo contre
    -- 44,4**, soit **+4,7 %**, environ onze octets par ligne. Et elle s'écrit **14 %
    -- plus VITE** (886–956 ms contre 1 033–1 119), parce que séparer évite la
    -- concaténation jsonb que fondre le métier dans `props` imposait à chaque
    -- insertion. L'élargissement ne se paie donc pas en écriture ; il se paie en
    -- volume, à un taux mesuré et connu.
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Le bail de la file de travail : les CINQ colonnes, comme sur
    -- `datastore_rows`. Elles étaient deux ici — un verrou qui ignore sous quel run
    -- une ligne est réservée, combien de fois elle a été reprise et pourquoi elle a
    -- été abandonnée n'est pas le même verrou, c'est celui d'avant les deux
    -- corrections qui l'ont rendu sûr.
    claimed_by TEXT,
    claimed_until TIMESTAMPTZ,
    claimed_run TEXT,
    claims INTEGER NOT NULL DEFAULT 0,
    abandon_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Contrainte NOMMÉE (docs/live-migrations.md) : un futur
    -- `DROP CONSTRAINT IF EXISTS nodes_public_id_key` ne peut pas viser autre chose.
    CONSTRAINT nodes_public_id_key UNIQUE (public_id)
);
-- DEUX index de requête, pas plus (0063-D3 garde-fou 2, confirmé par le banc : le
-- coût du volume se joue là, bien plus que dans la largeur de la ligne) — l'arbre
-- et le propriétaire.
--
-- ⚠️ **Les CINQ colonnes de bail sont là, l'index du bail ne l'est PAS**, et ce
-- n'est pas un oubli : le chemin de réservation lit encore `datastore_rows`. Un
-- index sur un prédicat que personne n'interroge est un coût d'écriture pur. Sa
-- forme utile dépend en outre d'un arbitrage de contrat — toute forme indexable en
-- partiel change l'ordre observable de la file. Il se pose le jour où la file
-- change de table, avec la requête qui le justifie.
-- Table et index naissent ensemble ⟹ leur place est ici et pas dans `_init`
-- (cf. le piège « CREATE INDEX d'une NOUVELLE colonne », docs/live-migrations.md).
--
-- ⚠️ Les index de RECHERCHE (GIN d'expression, FTS + trigramme) ne sont pas ici :
-- leur expression DOIT être la même objet que celle de la clause WHERE, donc ils
-- sont construits par `db/search.index_ddl()` (source unique index ↔ requête) et
-- posés par `_init`. Ils portent aujourd'hui sur `nodes` ENTIÈRE, sans prédicat
-- partiel : la table porte des dizaines de lignes. C'est quand les LIGNES de
-- tableau y entreront (M4) que le prédicat partiel achètera quelque chose — sur un
-- vivier, ces GIN pèsent 99 % du temps d'écriture (banc M0). Le décider avant
-- serait le calibrer sur une population qui n'existe pas.
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
-- ⚠️ L'ownership est PARTIEL depuis le lot M4 (#308), et l'exclusion des lignes est
-- tout l'intérêt : 0054-D4 dit qu'une ligne n'a pas de propriétaire propre, elle a
-- celui de son tableau. « Que possède cet acteur ? » ne se demande donc jamais d'une
-- ligne — l'index nu répondait 43 584 fois à une question posée quelques milliers de
-- fois, et se serait alourdi de chaque ligne créée depuis.
--
-- Mesuré sur la base peuplée à l'échelle de la production (12/08) : **16 kB contre
-- 312 kB**, et le planner l'utilise bel et bien pour la seule requête d'ownership de
-- `nodes` (`db/guides.py`), qui porte `kind = 'page'` — PostgreSQL sait prouver que
-- `kind = 'page'` implique `kind <> 'ligne'`.
--
-- ⚠️ **Le corollaire est une contrainte pour la suite** : une requête d'ownership
-- sans prédicat de genre NE PEUT PAS l'utiliser (vérifié : elle retombe en parcours
-- séquentiel). Toute nouvelle lecture par propriétaire sur `nodes` doit donc porter
-- son genre — ce qu'elle veut de toute façon dire, puisqu'on cherche des pages, des
-- guides ou des tableaux, jamais « tout ce que possède cet acteur, lignes comprises ».
CREATE INDEX IF NOT EXISTS idx_nodes_owner_scoped ON nodes(owner_type, owner_id)
    WHERE kind <> 'ligne';

-- BLOCS (blueprint ADR 0054-D2 + 0063-D2, lot M2) — le corps d'un nœud est une
-- SÉQUENCE DE BLOCS STOCKÉS, pas un markdown qu'on reparserait à chaque lecture.
-- Ce qu'on y gagne : l'adressage natif (un bloc a un identifiant, donc on peut le
-- citer), l'édition chirurgicale (remplacer un paragraphe sans réécrire la page) et
-- le verrouillage fin.
--
-- ⚠️ CE QUI N'ENTRE PAS ICI, et c'est une décision, pas un oubli : **les révisions**
-- (`doc_revisions`). Une révision est un INSTANTANÉ SÉRIALISÉ du document entier, et
-- le reste (0063-D2). Versionner bloc à bloc obligerait à reconstituer un état passé
-- par assemblage, alors qu'une révision doit être atomique et lisible telle quelle.
-- Les deux formes coexistent : le courant en table pour l'adressage, l'historique en
-- document pour l'intégrité.
--
-- L'ORDRE suit le pattern déjà en place sur `docs.position` : entiers espacés (×16),
-- réindexés au déplacement — rien à inventer. (⚠️ M-g du chantier a été tranché
-- depuis, au lot M3 : pour une FRATRIE DE NŒUDS, l'insertion se fait dans
-- l'intervalle et la réindexation devient un rattrapage — `db/nodes.midpoint`. Les
-- blocs d'un corps se comptent par dizaines, jamais par dizaines de milliers : ils
-- gardent le geste simple, et c'est le rapport de volume qui le justifie.)
--
-- ⚠️ AUJOURD'HUI CES BLOCS SONT UNE PROJECTION, pas la source de vérité : le corps
-- courant reste `props->>'body_md'` (nœuds) et `docs.body_md` (table legacy encore
-- lue par la prod). Le parse est rejoué au boot quand le corps change
-- (`props->>'blocks_md5'`, cf. `db/blocks.py`). Le jour où les blocs deviennent la
-- source, c'est l'ÉCRITURE qui les posera — et ce commentaire tombera.
--
-- Forme SOBRE, même discipline que `nodes` (0063-D3 garde-fou 1) : le nœud, la
-- position, le type, la charge utile. Les champs de bloc vont dans `props`.
CREATE TABLE IF NOT EXISTS blocks (
    id BIGSERIAL PRIMARY KEY,
    -- 0059-D3 : la désignation opaque, celle qu'un agent cite pour éditer CE bloc.
    -- ⚠️ **TIRÉE AU SORT, plus dérivée du rang** (#362). Ce commentaire a décrit
    -- l'inverse jusqu'au 17/08 — « dérivée de (nœud, rang) » —, et c'était vrai à
    -- l'écriture : l'identité était positionnelle, donc insérer un paragraphe en tête
    -- ré-identifiait TOUS les blocs en dessous et cassait toute référence externe.
    -- C'est corrigé dans `db/blocks.py` ; la carte, elle, était restée. Elle comptait :
    -- c'est ce commentaire qu'on lit pour savoir si un `blk_*` est ancrable.
    -- La rejouabilité du re-parse est désormais tenue par le RAPPROCHEMENT
    -- (`write_node_blocks` réattribue l'identité d'un bloc reconnaissable), pas par
    -- une formule.
    public_id TEXT NOT NULL,
    -- ⚠️ L'arbitrage M-e (« contrainte, ou intégrité portée par le code ? ») est
    -- TRANCHÉ ICI, et par les faits (2026-09-01, #800) : c'est une CONTRAINTE
    -- (`blocks_node_fk`, plus bas). L'intégrité portée par le code a échoué DEUX
    -- fois sur cette arête — la purge des conversions retirait le nœud sans son
    -- corps, puis `db/guides.py::delete_guide_db` a refait le même geste sur du
    -- contenu NATIF — et chaque échec laisse un bloc que plus aucune requête ne
    -- relie à rien (toute lecture de `blocks` part de `node_id`). Une discipline
    -- d'appelant qui a manqué deux fois ne se re-décrète pas, elle se remplace.
    -- ⚠️ L'arbitrage reste OUVERT pour `nodes.parent_id`, et ce lot ne le tranche
    -- pas : ce qui tranche ICI n'est pas un raisonnement transposable, ce sont deux
    -- fuites constatées sur CETTE arête. L'arbre n'a rien à montrer de comparable —
    -- et pour une raison qui rend l'observation SANS VALEUR : au 2026-09-01, en
    -- production, **0 nœud a un parent** (mesuré, base servie). Sa descendance est
    -- ramassée par `db/nodes.delete_page` ; le jour où on voudra l'y remplacer, ce
    -- sera sur des mesures faites sur un arbre qui existe.
    -- (Le CASCADE ne coûte rien de plus ici : `idx_blocks_node` couvre la colonne
    -- référençante, ce qu'une cascade parcourt à chaque suppression du parent.)
    node_id BIGINT NOT NULL,
    position BIGINT NOT NULL,   -- ordre dans le corps (entiers espacés ×16)
    -- 0054-D2 : texte · code · image · référence. Le lot M2 n'en produit que deux
    -- (`text`, `code`) — c'est tout ce qu'un markdown converti contient ; les deux
    -- autres naîtront des surfaces d'édition, pas d'une conversion.
    type TEXT NOT NULL,
    -- Charge utile. Invariant du parse : `props->>'md'` porte la source EXACTE du
    -- bloc, et la concaténation des blocs d'un nœud rend le corps au caractère près
    -- (tenu par `db/blocks.py` + `tests/test_nodes_m2_blocks.py`).
    props JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Contrainte NOMMÉE (docs/live-migrations.md), même raison que `nodes`.
    CONSTRAINT blocks_public_id_key UNIQUE (public_id),
    -- Le corps part avec son nœud, et c'est la BASE qui le garantit (#800).
    -- ⚠️ Sur une base qui EXISTE DÉJÀ ce `CREATE TABLE` est sauté : la même
    -- contrainte, sous le même nom, se pose par `_init.py`. Retirer l'une des deux
    -- ne rougirait nulle part ailleurs et ferait diverger la production d'une
    -- install fraîche en silence — les deux « marchent », seule l'une emporte le
    -- corps. Les deux naissances sont gardées par `tests/test_blocs_cascade.py`.
    CONSTRAINT blocks_node_fk FOREIGN KEY (node_id)
        REFERENCES nodes(id) ON DELETE CASCADE
);
-- UN index de requête : le corps d'un nœud, dans l'ordre. C'est la seule question
-- qu'on pose à cette table. Table et index naissent ensemble ⟹ leur place est ici
-- (cf. le piège « CREATE INDEX d'une NOUVELLE colonne », docs/live-migrations.md).
CREATE INDEX IF NOT EXISTS idx_blocks_node ON blocks(node_id, position);
"""
