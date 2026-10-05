"""Poser, retoucher et nettoyer le FORMAT d'un tableau (ADR 0032 §6 / 0046).

Extrait du store (#325), déplacement pur — un mixin que `DatastorePg` compose. La
couture sépare ce qui décrit un tableau de ce qui le remplit : deux préoccupations qui
changent à des rythmes différents, et dont les chantiers se marchaient dessus.

Ce que ce module a appris et garde :

- **remplacer un format et le retoucher sont deux gestes**, pas un seul avec des
  options. `set_schema` REMPLACE — bon pour poser, piège pour éditer : deux appels
  indiscernables n'avaient pas le même effet selon que l'appelant avait patché en
  mémoire ou reconstruit la liste. Mesuré sur un même tableau en une journée : une
  reconstruction avait détruit une contrainte et cinquante-deux notes de champ. Un
  avertissement n'y aurait rien changé — personne ne lit un avertissement sur un appel
  qui réussit —, d'où un geste qui ne PEUT pas détruire ;
- **un format ne vaut que pour l'avenir** : le poser ne revalide pas l'existant. D'où
  les avertissements qui regardent la donnée DÉJÀ là — colonnes orphelines, valeurs
  trop longues, valeurs qu'un enum fraîchement déclaré condamne. Sans eux, un tableau
  *a l'air* conforme parce qu'il a un schéma, et 504 lignes hors options y restent
  sans que rien ne le dise.
"""
from __future__ import annotations

from typing import Optional

from . import acces_agent as aga
from . import cles_inconnues
from . import reglages
from . import formule as dsformule
from . import schema as dsv2
from . import violations_existantes as dsve
from .. import db
from .errors import (ColumnAbsent, DatastoreForbidden, RowValidationError,
                     SchemaDefinitionError)
from .columns import _META_COLS


class SchemaOpsMixin:
    """Les opérations de FORMAT du store. Composé par `DatastorePg`, qui fournit
    `_resolve`, `_schema_of` et `_ns_of` — le mixin ne les redéfinit pas."""

    def get_schema(self, datastore: str) -> Optional[dict]:
        """Le schéma SERVI. oto#83 : amputé des colonnes `agent_access: "none"` quand
        l'appel vient de la face agent — c'est le goulot de lecture du format
        (`data_get_schema`, l'index de `data_app`, l'avertissement de colonnes de
        `data_rows`, le lot d'import).

        ⚠️ Le store, lui, continue de lire le schéma ENTIER par `_schema_of` : la
        validation et les refus ont besoin de la colonne masquée, sans quoi une
        écriture d'agent y passerait comme un champ hors schéma — l'inverse du but."""
        return self.schema_servi_et_masquees(datastore)[0]

    def schema_servi_et_masquees(self, datastore: str) -> tuple[Optional[dict], int]:
        """Le schéma SERVI, et COMBIEN de colonnes il a perdues en route (oto#94).

        La face outil servait quatre colonnes d'un tableau qui en porte cinq, sous la
        même forme exacte que l'écran : rien ne disait qu'une colonne manquait. Le
        compte se dit, jamais le nom — c'est le nom que le propriétaire retient."""
        ns_id = self._resolve(datastore)
        stocke = (db.get_datastore_by_id(ns_id) or {}).get("schema")
        servi = aga.schema_servi(stocke)
        return servi, (0 if servi is stocke else len(aga.masquees(stocke)))

    def schema_stocke(self, datastore: str) -> Optional[dict]:
        """Le schéma tel qu'il est STOCKÉ, entier, sur toutes les faces (oto#94).

        Réservé au TITRE — qui possède le tableau ou le gouverne (`_peut_forcer`),
        le palier qui pose `agent_access` (oto#93) : qui décide à qui une colonne est
        servie lit ce qu'elle porte. Un accès partagé, en lecture comme en écriture,
        n'y suffit pas (`DatastoreForbidden`)."""
        ns_id = self._resolve(datastore)
        if not self._peut_forcer(ns_id):
            raise DatastoreForbidden(datastore)
        return self._schema_of(ns_id)

    def _recalculer_formules_neuves_ou_modifiees(self, ns_id: int,
                                                 avant: Optional[dict],
                                                 apres: Optional[dict]) -> int:
        """Déclenche le backfill des colonnes `type: "formula"` (oto-backend#1008) :
        si `apres` pose une formule ABSENTE de `avant`, ou en modifie le texte,
        TOUTES les lignes existantes du tableau doivent être recalculées — sinon une
        ligne écrite avant la déclaration resterait sans valeur calculée, sans que
        rien ne le dise.

        **ASYNCHRONE depuis la v2** (mesuré sur un tableau de 8910 lignes : le
        recalcul synchrone prenait 1min34-1min47, au-delà du délai du client MCP,
        qui basculait l'appel en arrière-plan de son côté — pas un contrat qu'on
        veut). Cette méthode ne fait plus qu'un `UPDATE` de masse (rapide, aucun
        verrou par ligne) qui marque `formula_dirty` sur toutes les rows du
        namespace — `formula_backfill_worker.py` (composé au lifespan, même
        discipline que `rank_backfill_worker.py` : verrou PAR LIGNE, jamais de
        verrou de table) draine hors du chemin d'appel. Renvoie le nombre de rows
        marquées (pas encore recalculées — `datastore_formula_dirty_count` donne
        le nombre RESTANT à tout instant)."""
        if not dsformule.formules_neuves_ou_modifiees(avant, apres):
            return 0
        return db.datastore_mark_formula_dirty(ns_id)

    def set_schema(self, datastore: str, schema: Optional[dict], *,
                   retraits_annonces: Optional[list] = None,
                   geste: str = "schema") -> dict:
        """Pose (ou retire si None) le schéma typé d'un datastore. Exige le droit
        d'écriture. SOFT pour les champs (schéma de rendu, pas de validation des
        rows) — SAUF `schema.key` (#109 ch.3) : la clé métier déclarée devient une
        CONTRAINTE (index UNIQUE partiel sur `bkey_index_expr`, la valeur que sert
        la lecture, oto#223) → dédup concurrent-safe et
        lookup indexé. Des doublons existants sur la clé = REFUS actionnable (on ne
        pose pas un UNIQUE sur des données sales en silence).

        `retraits_annonces` = les champs dont le retrait est DÉJÀ dit à l'appelant
        (le `remove` d'un patch) : ils sortent du relevé d'effacement, sans quoi le
        geste explicite crierait sur lui-même — et un avertissement qui crie à tort
        est celui qu'on apprend à ignorer."""
        return self._poser_schema(self._resolve(datastore, write=True), datastore, schema,
                                  retraits_annonces=retraits_annonces, geste=geste)

    def _poser_schema(self, ns_id: int, datastore: str, schema: Optional[dict], *,
                      retraits_annonces: Optional[list] = None,
                      geste: str = "schema") -> dict:
        """Le corps de `set_schema`, sur un tableau DÉJÀ résolu — validation, écriture,
        index et relevés. Deux appelants : `set_schema` (qui résout l'adresse, droit
        d'écriture compris) et la création d'un tableau qui naît avec son schéma
        (`create_datastore(schema=…)`, oto#124) : son créateur vient de l'insérer, il
        n'y a rien à résoudre, et le résoudre par son nom sous l'org active pourrait
        désigner un homonyme. Un seul chemin de pose, jamais recopié."""
        if schema is not None and not isinstance(schema, dict):
            raise SchemaDefinitionError("schema doit être un objet {fields:[...]} ou null")
        # L'ancien schéma se lit AVANT la validation : le refus des clés inconnues
        # (01/10/2026) ne porte que sur ce que CE geste pose ou modifie — une clé déjà
        # stockée, inchangée, passe, sinon le patch d'une autre colonne serait refusé
        # sur un tableau pas encore migré (`cles_inconnues`).
        # oto#82 : c'est aussi la COMPARAISON des clés métier qui décide du travail
        # d'index — le calcul regardait ce qui EXISTE, pas ce qui a CHANGÉ, donc toute
        # pose (un libellé) rescannait les lignes puis reposait l'index, et son `DROP`
        # prend un verrou exclusif sur `datastore_rows`, commune à TOUS les tableaux.
        # « Inchangée » ne suffit pas : si l'index MANQUE (borne coupée au tir
        # précédent), il se repose.
        ancien = self._schema_of(ns_id)
        def_errors = dsv2.validate_schema_def(schema, ancien)
        if def_errors:
            raise SchemaDefinitionError("schéma invalide : " + " ; ".join(def_errors))
        new_key = (schema or {}).get("key")
        new_key = new_key if isinstance(new_key, str) and new_key else None
        ancienne_cle = (ancien or {}).get("key")
        ancienne_cle = ancienne_cle if isinstance(ancienne_cle, str) and ancienne_cle else None
        poser_index = bool(new_key) and (new_key != ancienne_cle
                                         or not db.datastore_has_key_index(ns_id))
        if poser_index:
            dups = db.datastore_key_dup_groups(ns_id, new_key)
            if dups:
                sample = ", ".join(f"{d['value']!r}×{d['n']}" for d in dups[:5])
                raise ValueError(
                    f"schema.key='{new_key}' refusée : {len(dups)}+ valeurs en DOUBLON "
                    f"dans les rows existantes (ex. {sample}). Résorbe-les d'abord "
                    f"(data_write avec key='{new_key}' merge les doublons, ou supprime "
                    "les rows en trop), puis re-déclare la clé.")
        # L'ancien schéma est lu plus haut (oto#82). Ce qui compte ici reste vrai : le
        # RELEVÉ d'effacement ne se calcule qu'après les refus — un refus n'a rien
        # effacé, et l'annoncer ferait chercher un dégât imaginaire.
        # oto#83 : un agent ne repose pas un format dont il ne voit qu'une partie, et
        # l'accès agent se règle au TITRE (possède ou gouverne, oto#93 — le palier du
        # forçage de `readonly`), plus à la face. Ici plutôt que dans les surfaces :
        # `patch_schema` repasse par cette méthode, donc les deux gestes sont couverts
        # par une seule garde — et elle tombe AVANT `set_datastore_schema`, rien n'est
        # écrit quand elle refuse.
        if (refus := aga.refus_de_schema(ancien, schema, geste=geste,
                                         a_titre=lambda: self._peut_forcer(ns_id))):
            raise SchemaDefinitionError(refus)
        efface = dsv2.declarations_effacees(ancien, schema, retraits_annonces)
        db.set_datastore_schema(ns_id, schema)
        # Après l'écriture du schéma : une formule neuve ou modifiée (oto-backend#1008)
        # ne vaut que si le schéma qui la porte est bien écrit.
        formules_marquees = self._recalculer_formules_neuves_ou_modifiees(
            ns_id, ancien, schema)
        # La pose de l'index est BORNÉE (incident du 2026-09-01 : elle a tenu la boucle
        # 12 min 48 s derrière une lecture ouverte). Quand la borne coupe, le schéma est
        # déjà écrit : rendre un 500 ferait chercher un dégât qui n'existe pas, et
        # taire l'échec ferait croire à une contrainte qui n'est pas là. On le DIT, et
        # `oto-mcp maintenance key-indexes` repose l'index au tir suivant.
        index_differe = index_non_retire = None
        if poser_index:
            try:
                db.datastore_ensure_key_index(ns_id, new_key)
            except db.KeyIndexUnavailable as e:
                index_differe = str(e)
        elif not new_key and (ancienne_cle or db.datastore_has_key_index(ns_id)):
            # Le dépôt reste conditionné à l'EXISTANT, pas à l'ancienne déclaration
            # seule : un index laissé par une clé retirée imposerait son unicité à une
            # colonne que le schéma ne déclare plus. Borné lui aussi (oto#82) — et quand
            # sa borne coupe, le schéma est déjà écrit : même arbitrage que la pose, on
            # sert le schéma et on NOMME la contrainte qui survit.
            try:
                db.datastore_drop_key_index(ns_id)
            except db.KeyIndexStillEnforced as e:
                # La clé survivante se NOMME : c'est elle que la contrainte fait encore
                # respecter, et sans son nom l'appelant ne peut pas relier un refus
                # d'écriture à la colonne qui le produit. `_schema_of` la connaît, le
                # retrait en base ne la connaît pas — le nom se pose donc ici.
                index_non_retire = (f"index d'unicité de `{ancienne_cle}` : {e}"
                                    if ancienne_cle else str(e))
        # #389 : ce que CETTE version fait respecter, dit à celui qui pose. Le
        # défaut n'était pas le vocabulaire mais le DÉCALAGE de déploiement — une
        # borne écrite un jour et servie trois semaines plus tard gèle 75 lignes
        # d'un coup, sans que personne ne relie l'effet à sa cause. Annoncé même
        # quand le schéma ne déclare rien : c'est une propriété du SERVEUR, pas du
        # schéma, et c'est justement quand on s'apprête à déclarer qu'on veut la
        # connaître.
        out = {"datastore": datastore, "schema": aga.schema_servi(schema),
               "enforced": dsv2.enforced_keys()}
        if formules_marquees:
            # ⚠️ Marquées, pas encore recalculées (oto-backend#1008 v2, backfill
            # asynchrone) : `formula_backfill_worker.py` drainera en fond. La clé
            # dit le geste en cours, jamais un fait accompli — `formules_a_recalculer`
            # (relue via `get_schema`, `datastore_formula_dirty_count`) nomme ce qui
            # reste, consultable à tout instant par un nouvel appel.
            out["formules_marquees_pour_recalcul"] = formules_marquees
            out["formules_recalcul_en_cours"] = True
        # oto-backend#479 : ce que les lignes EN PLACE violent déjà du schéma posé, par
        # chemin, avec un échantillon d'identifiants et la conséquence — jugé par le
        # moteur qui refusera les écritures, borné, et la borne se dit.
        out.update(dsve.releve(ns_id, schema))
        # Un statut sans état terminal = file de travail qui ne libère rien : le dire
        # ICI, à l'auteur du schéma, au moment où il le pose (les deux faces l'ont).
        warnings = [w for w in (index_differe, index_non_retire,
                                dsv2.queue_release_warning(schema),
                                # Les clés inconnues sont REFUSÉES depuis le
                                # 01/10/2026 — sauf celles déjà stockées, que ce geste
                                # n'a pas touchées. Elles restent jusqu'à la migration
                                # (`scripts/durcir_schemas.py`), et se DISENT : le
                                # premier qui voudra les modifier sera refusé.
                                cles_inconnues.residus_warning(schema),
                                # #319 : des options déclarées mais qu'aucun régime ne
                                # fait respecter — dit AU MOMENT où on pose le schéma,
                                # pas six semaines plus tard devant des valeurs libres.
                                dsv2.options_not_enforced_warning(
                                    dsv2.options_not_enforced(schema)),
                                # Et le fait, sur les champs `json` : stockés, rendus,
                                # mais pas interrogeables en profondeur.
                                dsv2.json_depth_warning(dsv2.json_fields_depth(schema)),
                                # 08/09/2026 — deux gardes qui ont l'air de mordre et
                                # ne mordent pas là où leur nom le laisse croire.
                                # Signalées par une campagne qui les avait posées en
                                # croyant fermer une porte : un motif ne contraint que
                                # ce qui est écrit, et une couche exigée l'est en
                                # PRÉSENCE, jamais en contenu.
                                dsv2.motif_sans_obligation_warning(
                                    dsv2.motif_sans_obligation(schema)),
                                dsv2.couche_exigee_sans_forme_warning(
                                    dsv2.couche_exigee_sans_forme(schema)),
                                self._missing_required_warning(ns_id, schema),
                                self._overlong_warning(ns_id, schema),
                                self._offpattern_warning(ns_id, schema),
                                self._offending_enum_warning(ns_id, schema),
                                self._orphan_columns_warning(ns_id, schema),
                                # oto-backend#479 : la phrase qui renvoie au relevé
                                # structuré, et qui dit quand il n'est qu'un plancher.
                                dsve.releve_warning(out)) if w]
        if warnings:
            out["warning"] = "\n".join(warnings)
        # #388 : ce que CETTE pose vient de retirer, avec les valeurs perdues. Clé
        # DISTINCTE de `warning` — les autres décrivent une configuration douteuse
        # qui reste réparable ; celle-ci nomme quelque chose qui N'EST PLUS, et la
        # réponse en est la seule copie. Même parti que `valeurs_effacees` sur une
        # ligne : on n'empêche rien, on nomme.
        out.update(dsv2.declarations_effacees_report(efface))
        return out

    def patch_schema(self, datastore: str, *, fields: Optional[list] = None,
                     remove: Optional[list] = None,
                     remove_attrs: Optional[dict] = None,
                     key: Optional[str] = None,
                     unknown_columns: Optional[str] = None,
                     new_rows: Optional[str] = None) -> dict:
        """Modifie le schéma PAR CLÉ, sans réécrire la liste entière (#388).

        `data_set_schema` REMPLACE : c'est le bon geste pour poser un format, et un
        piège pour le retoucher. Deux appels indiscernables — même méthode, même
        succès, même réponse — n'ont pas le même effet selon que l'appelant a patché
        en mémoire ou reconstruit la liste : le premier a préservé 78 notes de champ,
        le second a détruit un `pattern` et un `max_length`, et 52 notes ont disparu
        entre deux sessions par le même mécanisme. Rien dans la réponse ne le disait.
        Un avertissement n'aurait pas suffi : personne ne lit un avertissement sur un
        appel qui réussit. Il faut un geste qui ne PEUT pas détruire.

        `fields` = fusion par clé (complète l'existant, ajoute l'inconnu) ; `remove`
        = le retrait EXPLICITE d'une COLONNE ; `remove_attrs` = `{colonne: [attribut,
        …]}`, le retrait d'un ATTRIBUT sur une colonne qui reste ;
        `key`/`unknown_columns`/`new_rows` = les clés de tête, inchangées si omises
        (oto#127 : les deux réglages remplacent `strict`, `unknown_fields` et
        `key_required`). Ils se posent ICI en priorité : un tableau se ferme quand il a
        FINI d'être exploré, donc quand son schéma est long — et le poser par `set`
        obligerait à réécrire quatre-vingts champs pour une clé de tête, exactement le
        geste que ce patch existe pour éviter. Le schéma résultant repasse par
        `set_schema`, donc par ses gardes (doublons de clé métier, index UNIQUE,
        `new_rows: "reject"` sans `key`, valeurs des réglages) et ses avertissements
        (file de travail, bornes, colonnes orphelines) — on ne double pas cette
        logique.

        Un réglage revenu à son défaut s'ÉCRIT (`"create"`) plutôt que de retirer la
        clé (#516) : le relevé d'effacement compte les DISPARITIONS de tête sans
        exception, et retirer la clé ferait crier un geste explicite sur lui-même."""
        ns_id = self._resolve(datastore, write=True)
        current = self._schema_of(ns_id) or {}
        if not isinstance(current, dict):
            raise SchemaDefinitionError("le schéma courant n'est pas un objet — repose-le avec "
                             "data_set_schema avant de le patcher")
        if (fields is None and remove is None and remove_attrs is None
                and key is None and unknown_columns is None and new_rows is None):
            raise SchemaDefinitionError(
                "rien à patcher : passe `fields` (fusion par clé), `remove` (retrait "
                "d'une colonne), `remove_attrs` (retrait d'un attribut sur une "
                "colonne qui reste), `key`, `unknown_columns` ou `new_rows`")
        merged = [f for f in (current.get("fields") or []) if isinstance(f, dict)]
        merged, added, updated = dsv2.merge_fields(merged, fields or [])
        merged, inconnus_attrs = dsv2.remove_field_attrs(merged, remove_attrs or {})
        if inconnus_attrs:
            raise SchemaDefinitionError(
                "`remove_attrs` nomme ce que le schéma ne porte pas : "
                + ", ".join(f"`{k}`" for k in inconnus_attrs)
                + ". Rien n'a été touché — un retrait silencieux sur une faute de "
                "frappe ferait croire au nettoyage. Relis le format avec "
                "`data_get_schema`.")
        merged, unknown = dsv2.remove_fields(merged, remove or [])
        if unknown:
            raise SchemaDefinitionError(
                "`remove` nomme des champs que le schéma ne déclare pas : "
                + ", ".join(f"`{k}`" for k in unknown)
                + ". Rien n'a été touché — vérifie l'orthographe (data_get_schema). "
                "Pour effacer la COLONNE des données, c'est data_drop_column.")
        out_schema = {**current, "fields": merged}
        if key is not None:
            out_schema["key"] = key
        # Écrits TELS QUELS, jamais normalisés : une valeur hors des crans doit se
        # faire refuser par `validate_schema_def` en les nommant. La replier sur le
        # défaut ici rendrait un succès à qui croit avoir fermé son tableau.
        if unknown_columns is not None:
            out_schema[reglages.UNKNOWN_COLUMNS] = unknown_columns
        if new_rows is not None:
            out_schema[reglages.NEW_ROWS] = new_rows
        # Le patch NOMME ce qu'il retire (`removed`) : le relevé d'effacement n'a
        # donc rien à en redire. Il reste tendu pour tout le reste — c'est le seul
        # moyen de voir une fusion qui laisserait échapper quelque chose.
        result = self.set_schema(datastore, out_schema, geste="patch",
                                 retraits_annonces=(
                                     [str(k) for k in (remove or [])]
                                     + [f"{c}.{a}"
                                        for c, aa in (remove_attrs or {}).items()
                                        for a in (aa or [])]))
        return {**result, "added": added, "updated": updated,
                "removed": [str(k) for k in (remove or [])]}

    @staticmethod
    def _orphan_columns_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des colonnes vivent dans les DONNÉES sans être déclarées au schéma qu'on
        vient de poser : le dire ici, à l'auteur du renommage (#296).

        C'est le moment utile — renommer `actualite_sociale` en `analyse1` sort
        l'ancien nom de la vue, mais la clé reste dans chaque ligne. Elle continue
        de se rendre à la lecture, et son nom décrit souvent le contenu mieux que le
        nouveau : trois agents successifs ont écrit dedans en la prenant pour la
        bonne cible. Le silence à la pose du schéma est ce qui laisse le piège armé.

        Seulement quand le format fait contrat (`unknown_columns` autre que `create`) :
        sur un schéma souple, un champ libre est un droit du contrat (0016) — la table qu'on explore avant de la typer en est pleine, et
        signaler y serait du bruit sur un usage normal."""
        if not isinstance(schema, dict) or not reglages.format_contraignant(schema):
            return None
        declared = {f.get("key") for f in dsv2._fields(schema)}
        orphans = [k for k in db.datastore_row_keys(ns_id) if k not in declared]
        if not orphans:
            return None
        noms = ", ".join(f"`{k}`" for k in orphans)
        return (f"colonnes présentes dans les données mais PAS dans ce schéma : {noms}. "
                "Elles se rendent encore à la lecture — après un renommage, leur nom "
                "décrit souvent le contenu mieux que le nouveau, et un agent qui relit "
                "une ligne écrit dedans en croyant viser juste. Purge-les "
                "(`data_drop_column(datastore, key, confirm=True)`) ou déclare-les.")

    @staticmethod
    def _overlong_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes dépassent déjà une borne `max_length` fraîchement
        posée : le dire à celui qui la pose (#383). Pas un refus — la borne vaut
        pour ce qu'on ÉCRIT, l'historique n'est refusé qu'au geste qui le réécrit —
        mais un silence ferait croire la table conforme."""
        bounds = dsv2.top_level_bounds(schema)
        if not bounds:
            return None
        over = db.datastore_overlong_fields(ns_id, bounds)
        if not over:
            return None
        detail = ", ".join(
            f"`{o['field']}` : {o['rows']} ligne(s) jusqu'à {o['longest']} car. "
            f"(max {o['max_length']})" for o in over)
        return (f"borne posée sur des données déjà hors borne — {detail}. Les "
                "écritures futures sont refusées, ces lignes-là restent en place "
                "jusqu'à ce qu'on réécrive le champ (un patch d'un AUTRE champ "
                "passe).")

    @staticmethod
    def _missing_required_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes ne portent pas un champ fraîchement déclaré `required`
        (oto-backend#284) : le dire à celui qui le déclare, AU MOMENT où il le fait.

        ⚠️ **Celui-ci n'annonce pas une non-conformité, il annonce un BLOCAGE** — et
        c'est ce qui le sépare de ses deux voisins. Une borne ou un motif posés après
        coup ne jugent que les clés qu'un geste ÉCRIT : un patch d'un autre champ
        passe. Un champ requis est vérifié sur la ligne entière ; tant qu'il manque,
        **plus rien ne s'écrit sur cette ligne**, et le refus nomme un champ que
        l'appelant n'essayait pas d'écrire.

        Mesuré le 05/09/2026 : ajouter une colonne obligatoire à un tableau qui a déjà
        des lignes est un geste ordinaire, et il rend ces lignes inécrivables sans que
        rien ne le dise. On le découvrait plus tard, par des écritures qui échouent
        ailleurs — au pire endroit et au pire moment.

        AVERTIT, ne refuse pas : déclarer un champ obligatoire reste légitime, et
        l'ordre normal des choses est d'écrire d'abord et de formaliser ensuite. Ce
        qui manquait, c'est de connaître le prix avant de le payer."""
        champs = [f.get("key") for f in dsv2._fields(schema)
                  if isinstance(f, dict) and f.get("required") and f.get("key")]
        if not champs:
            return None
        manquants = db.datastore_rows_missing_required(ns_id, champs)
        if not manquants:
            return None
        detail = ", ".join(f"`{m['field']}` : {m['rows']} ligne(s)" for m in manquants)
        return (f"champ obligatoire déclaré sur des lignes qui ne le portent pas — "
                f"{detail}. ⚠️ Ces lignes-là n'accepteront PLUS AUCUNE écriture, sur "
                f"aucune colonne, tant que ce champ n'est pas rempli — et le refus "
                f"nommera ce champ, pas celui qu'on essayait d'écrire. Remplis-le sur "
                f"ces lignes, ou retire `required` (`data_patch_schema` avec "
                f"`required: null`).")

    @staticmethod
    def _offpattern_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes ne suivent DÉJÀ PAS un motif fraîchement posé (#387).

        Pendant exact de `_overlong_warning`, pour la même raison : un schéma ne vaut
        que pour l'avenir, mais l'ordre normal des choses est d'écrire d'abord et de
        formaliser ensuite — au moment où le motif arrive, la table est pleine. Sans
        ce relevé elle *paraît* conforme (elle a un format) tout en portant des
        valeurs que le format condamne.

        AVERTIT, ne refuse pas — et le motif ne gèle rien : comme la borne, il ne
        juge que les clés qu'un geste ÉCRIT, donc un patch d'un autre champ passe.

        Le verdict se calcule ICI, en Python, sur les valeurs distinctes rendues par
        la base : c'est le MÊME moteur d'expressions que celui qui refusera les
        écritures. Le poser en SQL (`~` de PostgreSQL) ferait compter par un moteur
        et refuser par un autre, dont les dialectes divergent — un avertissement qui
        annonce un nombre que le refus ne confirme pas est pire que pas
        d'avertissement du tout."""
        motifs = dsv2.top_level_patterns(schema)
        if not motifs:
            return None
        releve = db.datastore_field_values(ns_id, list(motifs))
        detail, tronque = [], False
        for champ, motif in sorted(motifs.items()):
            vu = releve.get(champ) or {}
            hors = [v for v in vu.get("values") or []
                    if not dsv2._pattern_re(motif).search(str(v["value"]))]
            if not hors:
                continue
            tronque = tronque or bool(vu.get("truncated"))
            lignes = sum(int(v["rows"]) for v in hors)
            echantillon = ", ".join(f"« {v['value']} » ({v['rows']})"
                                    for v in hors[:3])
            detail.append(
                f"  `{champ}` : {lignes} ligne(s) hors motif `{motif}` — "
                + echantillon
                + (f", et {len(hors) - 3} autre(s) valeur(s)" if len(hors) > 3
                   else ""))
        if not detail:
            return None
        msg = ("motif posé sur des données qui n'y répondent pas :\n"
               + "\n".join(detail)
               + "\nCes lignes restent en place ; elles seront refusées au geste "
                 "qui RÉÉCRIT ce champ (un patch d'un autre champ passe). Corrige-"
                 "les, ou élargis le motif.")
        if tronque:
            # Un relevé partiel qui se présenterait comme un total rassurerait
            # exactement là où il ne faut pas.
            msg += (" ⚠️ Trop de valeurs distinctes pour toutes les examiner : ce "
                    "compte est un PLANCHER, pas un total.")
        return msg

    @staticmethod
    def _offending_enum_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes portent une valeur qu'une liste d'options fraîchement
        déclarée condamne (tout type scalaire, #98) : le dire à celui qui la déclare.

        Un schéma ne vaut que pour l'AVENIR — le poser ne revalide pas l'existant.
        Or l'ordre normal des choses est d'écrire d'abord et de formaliser ensuite :
        au moment où le format arrive, la table est déjà pleine. Sans cet
        avertissement elle *paraît* conforme (elle a un schéma) tout en contenant
        des valeurs que le format refuse. Vécu : 504 lignes en « Oui »/« Non » sur
        un enum `oui`/`non`/`inconnu`.

        ⚠️ Le texte dit ce que le code FAIT de ces lignes, pas ce qu'on en craint
        (oto#218). Il les a dites « invisibles au filtrage et aux facettes » : faux.
        Les lectures ne consultent pas les `options` — `eq`, `in`, le regroupement
        et les comptes les rendent comme toute valeur (`db/query.py`) ; seul le tri
        typé les range après les conformes (`typed_order_sql`). Et une écriture
        future n'est pas « refusée » en bloc : la valeur hors options est écartée,
        le reste de la ligne s'écrit (`controles._ecarter`, #667). Un agent qui
        croit ses lignes invisibles les réécrit ou les exclut à tort ; un banc
        témoin (`test_datastore_enum_hors_options_lu_pg`) fait tomber le texte s'il
        redevient faux.

        AVERTIT, ne refuse pas : refuser rendrait impossible de déclarer un format
        sur un tableau existant, c'est-à-dire le cas normal. Et rend les valeurs
        fautives avec leur compte — c'est ce qui permet de choisir entre corriger la
        donnée et élargir les options, là où un total nu laisse chercher."""
        # Gate = la validation sera-t-elle ACTIVE ? On avertit exactement quand les
        # écritures futures seront refusées. Sur un schéma souple, la liste ne
        # condamne rien (validation opt-in, 0016) : signaler l'existant y annoncerait
        # un refus qui n'aura pas lieu — un faux avertissement coûte la confiance
        # qu'on met dans les vrais.
        if not dsv2.validation_active(schema):
            return None
        options = dsv2.top_level_options(schema)
        if not options:
            return None
        bad = db.datastore_offending_enum_values(ns_id, options)
        if not bad:
            return None
        detail = "\n".join(
            f"  `{b['field']}` : {b['rows']} ligne(s) hors options — "
            + ", ".join(f"« {v['value']} » ({v['rows']})" for v in b["values"])
            + (f", et {b['distinct'] - len(b['values'])} autre(s) valeur(s)"
               if b["distinct"] > len(b["values"]) else "")
            + f"  [options : {', '.join(options[b['field']])}]"
            for b in bad)
        return ("liste de valeurs déclarée sur des données qui en sortent déjà :\n" + detail +
                "\nCes lignes restent en place, et les lectures les voient telles "
                "quelles : les filtres, le regroupement et les comptes les rendent "
                "comme toute valeur. Le tri sur cette colonne les range après les "
                "valeurs conformes. Une écriture future qui pose une valeur hors "
                "options la voit écartée, et le reste de la ligne s'écrit ; seule "
                "dans le geste ou avec un autre refus, elle est refusée. Un patch "
                "d'un autre champ passe. Corrige-les (réécris le champ) ou élargis "
                "les options.")

    def drop_column(self, datastore: str, key: str, *, confirm: bool) -> dict:
        """Retire une colonne des DONNÉES de toutes les rows (#296). Destructif et
        irréversible : `confirm=True` exigé — la garde vit ICI, pas dans la surface,
        pour qu'aucune face ne puisse l'oublier. Exige le droit d'écriture (même
        palier que `set_schema` : c'est le même geste, la forme de la table).

        Retirer un champ du schéma le sort de la vue mais laisse la clé dans chaque
        ligne, où elle continue de se rendre — et d'attirer les écritures. Mettre la
        valeur à `null` ne l'efface pas non plus (une clé nulle reste une clé). D'où
        ce geste, le seul qui fasse disparaître la colonne.

        REFUSE une clé encore DÉCLARÉE au schéma : un `confirm` ne protège pas d'une
        faute de nom, et l'échappatoire est le geste naturel du renommage — retirer
        d'abord le champ du schéma. Ainsi la purge ne peut viser qu'une colonne dont
        le format a déjà acté la sortie.

        REFUSE aussi une purge qui n'a touché AUCUNE ligne (#680). `rows: 0` valait
        pour deux vérités opposées — « la colonne existait, aucune ligne ne la
        portait » et « ce nom n'est pas une colonne, rien n'a été fait » — et un
        opérateur qui enchaîne 190 retraits coche les seconds comme des retraits.
        Un geste destructif confirmé qui ne fait rien doit le DIRE : le succès porte
        donc toujours `rows >= 1`, et l'ambiguïté disparaît au lieu de se signaler."""
        key = (key or "").strip()
        if not key:
            raise ValueError("key requise (le nom de la colonne à purger)")
        if key in _META_COLS:
            raise ValueError(
                f"`{key}` est une colonne gérée par la plateforme, pas une donnée")
        if not confirm:
            raise ValueError(
                f"purge de la colonne `{key}` non confirmée — c'est irréversible sur "
                "toutes les lignes : rappelle l'appel avec confirm=True")
        ns_id = self._resolve(datastore, write=True)
        schema = self._schema_of(ns_id)
        if key in {f.get("key") for f in dsv2._fields(schema)}:
            raise ValueError(
                f"`{key}` est encore DÉCLARÉE au schéma de `{datastore}` : purger une "
                "colonne vivante est presque toujours une faute de nom. Si la sortie "
                "est voulue, retire d'abord le champ du schéma (data_set_schema), puis "
                "purge.")
        # Le diagnostic se fait APRÈS la purge, jamais avant : une clé pointée
        # LITTÉRALE au premier niveau du blob (`"site_web.comment"` posé par un
        # chemin qui a contourné la garde d'écriture, #647) EST une colonne, elle se
        # retire, et le geste a un effet. Refuser sur la seule forme du nom la
        # rendrait inatteignable — la seule chose qui distingue les trois cas est ce
        # que la purge a touché.
        rows = db.datastore_drop_column(ns_id, key)
        if not rows:
            raise ColumnAbsent(self._rien_purge(ns_id, schema, datastore, key))
        return {"datastore": datastore, "key": key, "rows": rows}

    def _rien_purge(self, ns_id: int, schema, datastore: str, key: str) -> str:
        """POURQUOI la purge n'a touché aucune ligne — la phrase qui remplace le zéro.

        Deux branches, et la seconde est le garde-fou de la première : un nom qui a
        la FORME d'une adresse de couche n'en est une que si sa colonne porteuse
        existe pour de bon (en base ou au schéma). Sinon c'est une faute de frappe,
        et la nommer « couche de `site_web` » fabriquerait une colonne `site_web` que
        personne n'a jamais écrite. **Une destination inventée est pire qu'une
        destination absente** : on dit alors ce qu'on constate, et rien de plus."""
        adresse = dsv2.layer_address(key)
        if adresse is not None:
            base, couche = adresse
            declarees = {f.get("key") for f in dsv2._fields(schema)}
            if base in declarees or db.datastore_has_column(ns_id, base):
                return (
                    f"`{key}` n'est pas une colonne : c'est l'annotation `{couche}` "
                    f"de la colonne `{base}`. La lecture la sert à plat, à côté de sa "
                    f"colonne, mais elle est STOCKÉE sous `{base}` — une purge de "
                    f"colonne ne descend pas dedans, et rien n'a été touché. Pour la "
                    f"retirer, écris-la nulle en forme imbriquée (data_write "
                    f'`{{"{base}": {{"{couche}": null}}}}`) ; pour purger la colonne '
                    f"entière et ses annotations avec elle, vise `{base}`.")
        return (
            f"aucune colonne `{key}` dans les données de `{datastore}` : aucune ligne "
            f"ne la portait, rien n'a été touché — ce n'est donc pas un retrait, et "
            f"ça ne se compte pas comme tel. Vérifie le nom avant de le rayer de ta "
            f"liste.")

    def set_semantic(self, datastore: str, enabled: bool) -> dict:
        """Active/désactive la recherche SÉMANTIQUE des lignes du datastore (#67 V2.2,
        opt-in — coût d'embedding). Exige le droit d'écriture. À l'activation, les rows
        sont mises en file d'indexation (worker) ; à la désactivation, leurs embeddings
        sont purgés."""
        ns_id = self._resolve(datastore, write=True)
        queued = db.set_datastore_semantic(ns_id, bool(enabled))
        return {"datastore": datastore, "semantic_search": bool(enabled), "rows_queued": queued}
