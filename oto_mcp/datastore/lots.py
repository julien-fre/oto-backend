"""Écrire un LOT de lignes : le chemin des imports, et ses refus qui NOMMENT la ligne.

Extrait de `core.py` (déplacement pur, 07/09/2026) — un mixin que `DatastorePg`
compose, sur le modèle de `SchemaOpsMixin`. Séparé de `ecriture.py` parce que le lot
a ses propres règles — il n'est pas atomique, il dédouble sur une clé, et chacun de
ses refus doit dire OÙ il s'est arrêté (#412).

⚠️ Ce module LIT des attributs de colonne et de tableau (`key`, `schema`…) : il est
listé dans `vocabulaire._read_keys`.
"""
from __future__ import annotations

from typing import Any, Optional

from psycopg.errors import UniqueViolation

from .. import db, geste
from . import acces_agent as aga
from . import colonnes_non_declarees as cnd
from . import schema as dsv2
from .columns import (
    _META_COLS,
    mots_resolus_a_la_creation,
    refuser_cles_internes,
    refuser_les_mots_mal_places,
    sans_les_nulls_sans_effet,
    sans_les_objets_vides,
)
from .cle_metier import ligne_de_la_course_perdue, refuser_cle_metier_vide
from .controles import _refuser_origine_non_declaree
from .errors import (BusinessKeyExists, BusinessKeyRequired, RowLocked,
                     RowValidationError)
from .outils import _new_id, _refus_de_creation
from .points import _refuse_dotted_names, ranger_les_couches
from . import rangs as rg
from . import mots_deprecies as mdp
from . import upsert_implicite as upi
from . import donnees_d_origine as ddo
from .reserves import refuser_champs_reserves


class LotsMixin:
    """L'écriture par lot du store. Composé par `DatastorePg`."""

    @staticmethod
    def _designation_de_lot(rang: int, total: int, key: Optional[str],
                            data: Any, faites: int) -> str:
        """COMMENT retrouver la ligne fautive d'un lot, et OÙ le lot s'est arrêté (#412).

        Le refus nommait le champ et la valeur, jamais la ligne : sur un import de
        8 910 lignes par lots de 200, retrouver la fautive coûtait plus cher que les
        199 lignes perdues avec elle. Le store valide ligne par ligne — il SAIT
        laquelle échoue, l'information existait et ne sortait pas.

        ⚠️ On y ajoute ce que le signal croyait acquis et qui est FAUX : **le lot
        n'est pas atomique**. Les lignes qui précèdent la fautive sont écrites et le
        restent. C'est ce qui décide de la reprise — rejouer le lot entier
        re-fusionnerait les premières (ou les dupliquerait, sans clé métier)."""
        ref = ""
        if key and isinstance(data, dict) and data.get(key) is not None:
            ref = f" ({key}={data[key]})"
        etat = (f"{faites} ligne{'s' if faites > 1 else ''} déjà "
                f"écrite{'s' if faites > 1 else ''} avant l'arrêt, aucune après "
                f"— reprends le lot à la ligne {rang}" if faites
                else "aucune ligne écrite avant l'arrêt")
        return f"ligne {rang}/{total} du lot{ref} · {etat}"

    @geste.import_si_donnees_d_origine
    def _write_rows_to_ns(self, ns_id: int, rows: list, *, key: Optional[str],
                          readonly_override: bool = False,
                          origine_override: bool = False,
                          donnees_d_origine: bool = False,
                          force: Optional[frozenset] = None,
                          upsert: bool = False,
                          cle_passee: bool = False,
                          fusions: Optional[upi.Fusions] = None) -> dict:
        """Cœur du batch, keyé par `ns_id` déjà résolu (réutilisable hors contexte
        d'org — matérialisation d'un upload signé, où l'org de session est absente).
        Le schéma v2 (validation/lifecycle, ADR 0046) s'applique à CHAQUE row du
        lot, sur son résultat mergé — une row fautive fait échouer le lot en NOMMANT
        la ligne autant que le champ (#412), et en disant ce qui est déjà écrit.

        `cle_passee` (oto#141) = `key` a été NOMMÉ par l'appel : le lot DÉSIGNE par la
        clé, une valeur en place modifie sa ligne. Sinon il AJOUTE, et une valeur en
        place n'y fusionne qu'avec `upsert` — sans lui avertie, puis refusée à sa date.
        Deux lignes du lot à la même clé : refusées sans `upsert`, dans les deux cas. Le
        lot est jugé ENTIER avant sa première ligne.
        `fusions` = le relevé partagé d'un geste découpé en tranches (`import_rows`),
        pour que les rangs et `dans_rang` soient ceux du fichier ; `None` = ce lot seul."""
        ns = self._ns_of(ns_id)
        schema = ns.get("schema")
        nom_ns = ns.get("datastore") or f"#{ns_id}"
        # #658 : UN palier pour le lot entier, lu une seule fois — pas une lecture
        # d'ownership par ligne sur un import de huit mille.
        # `force` implique la demande : nommer une cible EST le geste.
        forcage = self._forcage_readonly(
            ns_id, schema, readonly_override or bool(force), force)
        # oto#140 : `@keep` et `@clear` sur le chemin des imports aussi — jugés sur le
        # lot ENTIER avant la première ligne : le refus sans moitié de lot déjà écrite.
        mdp.controler(*(r for r in rows if isinstance(r, dict)))
        # oto#124 : une colonne non déclarée, à partir de sa date — le lot ENTIER est
        # jugé ici, avant sa première ligne. Avant la date, rien : chaque ligne relève
        # ses colonnes au passage (`_check_row`), dites en une phrase.
        cnd.juger_le_lot(schema, rows)
        # oto#141 : AJOUTER sur une clé existante ne fusionne plus en silence ; la
        # DÉSIGNER (`key=` nommé, tableau fermé) la modifie. `upsert` sans clé ne
        # fusionnerait rien ; sans `upsert`, à partir de la date, le lot est jugé ENTIER
        # ici — doublons internes, et clés en base s'il ajoute —, avant toute écriture.
        upi.refuser_upsert_sans_cle(upsert, key, nom_ns, lot=True)
        designation = upi.designe(schema, cle_passee)
        suivi = fusions if fusions is not None else upi.Fusions()
        if key and not upsert and not suivi.juge and upi.refus_arme():
            upi.juger_le_lot(
                rows, key=key, datastore=nom_ns, designation=designation,
                decalage=suivi.decalage,
                nommer=suivi.nommer, textes=db.datastore_textes_de_cle,
                chercher=lambda kv: db.datastore_find_row_id_by_key(ns_id, key, kv))
        inserted, updated, ids = 0, 0, []
        fusionnees: list[dict] = []

        def _fusion(rang: int, row_id: str, colonne: str, valeur: Any) -> None:
            entree = suivi.fusion(rang, row_id, colonne, valeur)
            upi.controler_fusion(self.off_notices, designation=designation,
                                 upsert=upsert, datastore=nom_ns, key=colonne,
                                 kv=valeur, row_id=row_id,
                                 dans_rang=entree["dans_rang"], rang=entree["rang"],
                                 lot=True, nommer=suivi.nommer)
            fusionnees.append(entree)
        total = len(rows)
        for rang, data in enumerate(rows, 1):
            self._lot_rang = rang  # read by a sliced import to name the absolute row
            try:
                if not isinstance(data, dict):
                    raise ValueError("chaque row doit être un objet")
                self._reject_misplaced_id(data, None, batch=True)
                user_data = {k: v for k, v in data.items() if k not in _META_COLS}
                # oto#22 : l'écriture PAR RANG, sortie avant toute garde (cf.
                # `append_row`). Les mots refusés de ses valeurs ont été jugés sur le
                # lot ENTIER, plus haut, clés de rang comprises.
                user_data, rangs = rg.sortir_les_rangs(schema, user_data)
                if rangs is not None:
                    rangs.preparer(schema, self._normaliser_les_dates)
                # ⚠️ #329 volet 2, appliqué au QUATRIÈME chemin — il y manquait.
                # `append_row` et la fusion refusent une clé littérale pointée ; le
                # LOT, non. Or c'est LUI qui porte les imports : la garde était posée
                # sur les chemins où l'on écrit une ligne, et absente
                # de celui où l'on en écrit huit mille.
                #
                # Ce que ça a produit, mesuré le 31/08 sur un fichier de production :
                # une fiche porte `contact2_nom.comment` et `contact2_email.comment`
                # comme COLONNES littérales de premier niveau, à côté d'une base
                # `contact2_nom` qui, elle, a été retirée depuis. Elles ont donc survécu
                # au retrait — *une couche imbriquée part avec sa colonne, une colonne
                # littérale du même nom ne part pas* — et se relisent ensuite comme des
                # « couches orphelines », un objet qui n'existe pas dans le modèle.
                # Deux sessions ont cherché le geste pendant une demi-journée.
                # MÊME ordre que les quatre autres portes : on range, puis on refuse.
                # C'est par ici que passent les imports — donc par ici que passe un
                # export du tableau de bord réimporté, qui porte `champ.comment` par
                # construction (#687).
                user_data = ranger_les_couches(
                    schema, user_data,
                    colonnes_en_place=lambda: self._colonnes_de_la_ligne_visee(
                        ns_id, schema, user_data, key))
                # oto#182 : un `null` qui n'efface rien ne s'écrit pas (cf. `append_row`).
                user_data = sans_les_nulls_sans_effet(
                    user_data, lambda: self._donnees_de_la_ligne_visee(
                        ns_id, schema, user_data, key), schema)
                # oto#165 : un `{}` sur une case vide est écarté, et dit.
                user_data, objets_vides = sans_les_objets_vides(
                    user_data, lambda: self._donnees_de_la_ligne_visee(
                        ns_id, schema, user_data, key))
                self.off_rejected.extend(objets_vides)
                _refuse_dotted_names(user_data)
                refuser_cles_internes(user_data)
                refuser_les_mots_mal_places(schema, user_data)
                # #859 : les dates en une forme, AVANT la recherche par clé.
                user_data = self._normaliser_les_dates(schema, user_data)
                # Signal feedback 994 : la clé DÉCLARÉE (celle de l'index), pas le
                # `key=` de l'appel — cf. `cle_metier`.
                refuser_cle_metier_vide(schema, user_data)
                # ⚠️ DÉBALLÉ : une clé métier ANNOTÉE désigne la même ligne qu'une clé nue.
                # `{"code": {"valeur": "A", "comment": "fichier source"}}` et
                # `{"code": "A"}` sont la MÊME identité — enrichir la provenance ne
                # change pas ce qu'une donnée EST. Sans ce déballage, le lookup
                # cherchait l'objet entier : ligne « introuvable », puis insertion,
                # puis `UniqueViolation` sur l'index de clé. Mesuré le 08/09/2026.
                kv = dsv2.unwrap(user_data.get(key)) if key else None
                existing_id = None
                par = (key, kv)  # la colonne et la valeur qui ont trouvé la ligne
                if key and kv is not None and str(kv) != "":
                    existing_id = db.datastore_find_row_id_by_key(ns_id, key, kv)
                # #516 : le LOT est le second chemin de création, et le plus
                # volumineux — c'est par lui que passent les imports. La garde s'y
                # juge sur la clé DÉCLARÉE, celle qui porte l'index UNIQUE, même
                # quand le lot dédouble sur une AUTRE (`key=` explicite) : sinon un
                # tableau fermé refuserait une ligne qu'il porte déjà.
                if existing_id is None and dsv2.creation_refusee(schema):
                    dk = self._declared_key_of(schema)
                    dkv = dsv2.unwrap(user_data.get(dk))
                    if dk != key and dkv is not None and str(dkv) != "":
                        existing_id = db.datastore_find_row_id_by_key(ns_id, dk, dkv)
                        par = (dk, dkv)
                    if existing_id is None:
                        raise _refus_de_creation(nom_ns, dk, dkv, schema=schema,
                                                 ligne=user_data, cle_du_lot=key)
                if existing_id is not None:
                    _fusion(rang, existing_id, *par)
                    self._merge_into_row(ns_id, existing_id, user_data, schema=schema,
                                         forcage=forcage,
                                         origine_override=origine_override,
                                         donnees_d_origine=donnees_d_origine,
                                         lot=True, rangs=rangs)
                    updated += 1
                    ids.append(existing_id)
                    continue
                # #586 : la création dans le LOT (même chemin que l'upload signé) —
                # la couche d'origine d'un champ système ne s'écrit pas.
                # oto#22 : la ligne naît — un rang n'y vise rien, `contacts[+]` y
                # ajoute (cf. `append_row`). La course perdue, plus bas, fusionne le
                # geste d'origine, rangs compris.
                cree = (user_data if rangs is None else
                        {**user_data, **rangs.appliquer({}, schema, creation=True)})
                refuser_champs_reserves(schema, cree,
                                        agent=aga.appel_d_agent())
                _refuser_origine_non_declaree(cree, declare=origine_override)
                # CRÉATION : pas de ligne en base, donc rien à préserver — mais la
                # règle « une origine déjà posée ne se réécrit pas » vaut quand même,
                # car l'appelant peut avoir écrit `origine` lui-même (chemin déclaré).
                # oto#204 : la ligne NEUVE d'un lot ne passe pas par la fusion — ses mots
                # réservés se résolvent ici, sur une copie (cf. `append_row`) : la course
                # perdue ci-dessous fusionne le geste d'origine.
                a_creer = mots_resolus_a_la_creation(schema, cree)
                releve = (ddo.poser_les_deux_versions(a_creer)
                          if donnees_d_origine else None)
                # `lot=True` : le refus de l'`id` nu doit nommer un geste qui
                # ABOUTIT en mode lot — cf. #72, 22 cas sur 29 suivaient le conseil
                # du refus précédent, lequel échouait ici.
                self._check_row(schema, a_creer, lot=True, creation=True)
                try:
                    row = db.datastore_insert_row(ns_id, _new_id(), a_creer)
                except UniqueViolation as e:
                    # Course perdue sous l'index UNIQUE de clé métier (#109 ch.3) : un
                    # write concurrent vient d'insérer la même clé entre le lookup et
                    # l'insert — c'est PRÉCISÉMENT le doublon que la contrainte empêche.
                    # On converge en update (même merge que le chemin nominal). La clé
                    # violée est la clé DÉCLARÉE du datastore (l'index ne porte qu'elle),
                    # qui peut différer d'un `key` explicite passé à l'appel.
                    dk = ((db.get_datastore_by_id(ns_id) or {}).get("schema")
                          or {}).get("key")
                    dkv = dsv2.unwrap(user_data.get(dk)) if dk else None
                    existing_id = ligne_de_la_course_perdue(ns_id, dk, dkv, e)
                    _fusion(rang, existing_id, dk, dkv)
                    # ⚠️ `donnees_d_origine` voyage ICI aussi (oto#72) : ce chemin est
                    # la COURSE PERDUE sous l'index de clé métier, qui converge en
                    # update — « même merge que le chemin nominal », disait le
                    # commentaire, mais il laissait tomber ce paramètre. Une ligne
                    # d'import qui perdait sa course perdait sa version d'origine.
                    self._merge_into_row(ns_id, existing_id, user_data, schema=schema,
                                         forcage=forcage,
                                         origine_override=origine_override,
                                         donnees_d_origine=donnees_d_origine,
                                         lot=True, rangs=rangs)
                    updated += 1
                    ids.append(existing_id)
                    continue
                # oto#164 : relevé APRÈS l'insert (cf. `append_row`).
                if releve is not None:
                    ddo.relever(self, releve)
            except RowLocked as e:
                # ⚠️ MÊME parti que les deux clauses suivantes, et pour la même
                # raison : le refus garde sa CLASSE, seule sa désignation change.
                # `RowLocked` dérive de `ValueError` depuis le 05/09/2026 ; sans
                # cette clause, elle tomberait dans le `except ValueError` du bas et
                # ressortirait en refus d'entrée invalide — perdant son code 409 et
                # le message du bail, exactement le défaut qu'on vient de fermer.
                raise RowLocked(
                    e.row_id, e.claimed_by, e.claimed_until, e.claimed_run,
                    row=self._designation_de_lot(rang, total, key, data,
                                                 inserted + updated)) from None
            except BusinessKeyExists as e:
                # oto#141 : une COURSE perdue sans `upsert`, après la date (le lot, lui,
                # a été jugé entier avant sa première ligne). Même parti : la classe
                # reste, la désignation s'ajoute. AVANT `ValueError`, dont elle dérive.
                raise BusinessKeyExists(
                    e.motif, key=e.key, details=e.details,
                    row=self._designation_de_lot(rang, total, key, data,
                                                 inserted + updated)) from None
            except BusinessKeyRequired as e:
                # MÊME parti que ci-dessous : le refus garde sa classe (la face REST
                # en dérive son code `business_key_required`), seule sa désignation
                # change. Cette clause DOIT précéder `except ValueError` — dont
                # `BusinessKeyRequired` dérive, pour être actionnable côté MCP.
                raise BusinessKeyRequired(
                    e.motif, key=e.key, datastore=e.datastore, value=e.value,
                    details=e.details, row=self._designation_de_lot(rang, total, key, data,
                                                 inserted + updated)) from None
            except RowValidationError as e:
                # Le refus GARDE sa classe : les surfaces s'en servent pour choisir
                # leur code (`capabilities/datastore/rows`), et un refus de schéma
                # dans un lot reste un refus de schéma. Seule sa désignation change —
                # `details` suit, sinon le refus structuré (#545) se perdrait
                # exactement là où le lot rend la reprise la plus coûteuse.
                # `type(e)` : une sous-classe (`ColonneNonDeclaree`, oto#124) garde
                # elle aussi sa classe, donc son code.
                raise type(e)(
                    e.errors, details=e.details,
                    row=self._designation_de_lot(rang, total, key, data,
                                                 inserted + updated)) from None
            except ValueError as e:
                # Les autres refus de row (`id` égaré, `_id` dans un lot, row qui
                # n'est pas un objet) nomment déjà LEUR faute, jamais la ligne.
                raise ValueError(
                    f"{self._designation_de_lot(rang, total, key, data, inserted + updated)}"
                    f" : {e}") from None
            inserted += 1
            ids.append(row["row_id"])
            suivi.creee(rang, row["row_id"])
        recap = {"inserted": inserted, "updated": updated, "count": inserted + updated,
                 "key": key, "ids": ids}
        # oto#141 : `ids` reste aligné rang pour rang ; ce qui dit que deux entrées
        # désignent la même ligne, c'est `fusions`.
        if fusionnees:
            recap["fusions"] = fusionnees
        return recap
