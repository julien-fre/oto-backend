"""Capacités « les lignes » : page, fiche, écriture, suppression, file, agrégat (#302).

Huit chemins qui vivaient en routes écrites à la main (`api/datastore.py`).
C'est le cœur de l'écran datastore, et c'était la plus grosse zone d'ombre du contrat
d'API : ni entrée ni sortie déclarées, donc rien à générer chez un intégrateur.

`mcp=None` partout : les tools `data_*` tiennent déjà la face agent, ce lot ne migre
que la face REST. Autz `SUB_ONLY` au seuil ; le vrai gate est le droit de lecture ou
d'ÉCRITURE sur le tableau, résolu par le store (org active + ownership ADR 0030) —
jamais par le nom passé en path. Un tableau hors périmètre répond 404, comme partout
ailleurs dans le datastore.

**Deux corps LIBRES** (`RestBinding.body_field`) : ajouter et modifier une ligne
envoient les colonnes du tableau au premier niveau du corps. Ce sont des DONNÉES, pas
des champs d'API — la garde de champ inconnu les aurait toutes refusées. Elle continue
de couvrir la query string et les params de chemin, ce que le test vérifie.

**Les paramètres JSON restent des chaînes** (`filters`, `metrics`, `filter`) : ils
arrivent par la query string, où ils sont du JSON encodé en texte. Les typer `list`/
`dict` ferait rendre à pydantic un `invalid_input` générique là où ces routes rendent
`invalid_filters`/`invalid_metrics`/`invalid_filter` — trois refus distincts que le
cockpit distingue. Le décodage vit donc dans le handler, comme avant.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ... import access
from ...auth import token_scopes
from ...datastore.identite import Adresse
from ...datastore import journal as datastore_journal
from ...datastore import couches, identite, jetons
from ...datastore import forcage as fcg
from ...datastore import layers as dsl
from ...datastore import schema as dsv2
from ...datastore.core import (
    BusinessKeyRequired,
    DatastoreNotFound,
    DatastoreReadOnly,
    RowLocked,
    RowNotFound,
    RowValidationError,
    make_store,
)
from ...datastore.errors import RevisionConflict
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .common import EntreeDatastore, HORODATAGE, ns_not_found
from .lot import refuser_un_lot
from ..registry import CAPABILITIES
from ._forme import (_EMPTIES, _LAYERS, _REFUS_DE_FORME, _VERSIONS, _layers,
                     _relais_empties, _versions)
from ._refus import (_JETON_MAL_PLACE, _LIGNE_ABSENTE, _PRECONDITION_REFUSEE,
                     _REFUS_D_ADRESSE, _REFUS_D_ECRITURE, _WORKER_REQUIS)


def _tolerant_int(v):
    """`?limit=beaucoup` retombe sur le défaut, il ne casse pas la requête.

    C'est le comportement des routes d'avant (`_int(name, default)` avalait
    `TypeError`/`ValueError`). Le typer `int` sec ferait 400 là où le serveur rendait
    une page — un écart invisible en test et visible en prod.
    """
    if v is None or isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _precondition(refus: str):
    """`?expected_revision=` — le MÊME champ sur les trois gestes qui décident d'après
    une lecture : écrire, supprimer, libérer (oto#217).

    En QUERY, jamais dans le corps là où le corps EST la donnée (une clé de plus y
    serait écrite comme une colonne). Un serveur qui ne le connaît pas rend
    `400 unknown_fields` — l'échec est fermé, rien ne part sans la protection demandée.
    Un seul texte : trois copies auraient divergé, et le client aurait lu la plus
    ancienne."""
    return Field(
        default=None,
        description=("The `_revision` of the row as you read it, when what you do was "
                     "decided from that read. If the row changed since (any column, or "
                     f"its reservation), {refus}: `409 revision_conflict`, "
                     "`details.current_revision`. Omit it otherwise."))


class ListRowsInput(EntreeDatastore):
    datastore: Adresse
    # `None` = le défaut du serveur (0 / 50) ; borné à [1, 1000] pour `limit`.
    offset: Optional[int] = None
    limit: Optional[int] = None
    order_by: Optional[str] = None
    order_dir: str = "desc"
    q: Optional[str] = None
    # JSON encodé : filtre d'égalité exacte `{colonne: valeur}` — le MÊME paramètre
    # que la face MCP `data_rows`, et ce que la CLI envoie sur `--filter`. Il était
    # absent : la route l'ignorait en silence et rendait TOUTES les lignes en les
    # présentant comme filtrées (#303). Les deux faces d'un verbe doivent offrir
    # les mêmes paramètres — c'est « une capacité, deux faces » appliquée à elle-même.
    filter: Optional[str] = None
    # JSON encodé : liste de clauses `{field, op, value}`, combinées en ET.
    filters: Optional[str] = None
    layers: str = _LAYERS
    versions: Optional[list[str] | str] = _VERSIONS
    empties: str = _EMPTIES
    # Même sémantique que la face MCP `data_rows` (oto-backend#980, lot 2) : colonnes
    # gardées + `_id` toujours présent, `["*"]` ou absent = ligne complète (contrat
    # inchangé). Comma-separated pour la MÊME raison que `versions` juste au-dessus.
    fields: Optional[list[str] | str] = None

    _coerce = field_validator("offset", "limit", mode="before")(_tolerant_int)

    @field_validator("versions", mode="after")
    @classmethod
    def _versions_en_liste(cls, v):
        """⚠️ **Une URL ne transporte que des chaînes.** Déclarer `list[str]` seul rend
        le champ INATTEIGNABLE par la surface qui le porte : `?versions=a,b` sort en
        `400 invalid_input`, et le paramètre a l'air servi sans l'être — c'est ce qui a
        laissé `?include=` mort quinze jours (#367). Même patron que `node_rows.filter`
        et `projects.include`, pour la même raison.

        La virgule sépare une valeur unique ; la forme répétée (`?versions=a&versions=b`) arrive
        déjà en liste depuis l'adaptateur. Les deux se combinent."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]

    @field_validator("fields", mode="after")
    @classmethod
    def _fields_en_liste(cls, v):
        """Même patron que `_versions_en_liste` ci-dessus, même raison (#367)."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]



class AggregateInput(EntreeDatastore):
    datastore: Adresse
    group_by: Optional[str] = None
    # JSON encodé : `[{op: count|sum|avg|min|max, field?}]`.
    metrics: Optional[str] = None
    # JSON encodé : filtre d'égalité exacte `{colonne: valeur}` (chemin MCP).
    filter: Optional[str] = None
    q: Optional[str] = None
    # JSON encodé : mêmes clauses riches que `/rows` — les tuiles du cockpit agrègent
    # le jeu filtré affiché.
    filters: Optional[str] = None


class DatastoreRefInput(EntreeDatastore):
    datastore: Adresse


class RowRefInput(EntreeDatastore):
    datastore: Adresse
    row_id: str


class DeleteRowInput(RowRefInput):
    """La suppression porte la précondition ; la lecture par id n'en a que faire —
    d'où deux entrées là où `RowRefInput` servait les deux (oto#217)."""
    expected_revision: Optional[str] = _precondition("nothing is deleted")


class GetRowInput(RowRefInput):
    """La lecture d'une ligne seule porte `layers` ; `RowRefInput` reste partagé avec
    la suppression, qui n'a pas de forme à choisir."""
    layers: str = _LAYERS
    versions: Optional[list[str] | str] = _VERSIONS
    empties: str = _EMPTIES

    @field_validator("versions", mode="after")
    @classmethod
    def _versions_en_liste(cls, v):
        """⚠️ **Une URL ne transporte que des chaînes.** Déclarer `list[str]` seul rend
        le champ INATTEIGNABLE par la surface qui le porte : `?versions=a,b` sort en
        `400 invalid_input`, et le paramètre a l'air servi sans l'être — ce qui a laissé
        `?include=` mort quinze jours (#367). Même patron que `node_rows.filter`."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]


# #658 : le corps EST la ligne (`RestBinding.body_field`) — le forçage ne peut donc
# pas y vivre sans se confondre avec une colonne du tableau. Il passe en QUERY, ce que
# l'adaptateur REST fait déjà de tout champ d'`Input` hors du corps libre, et ce que
# l'OpenAPI publie tel quel. Additif : un client qui l'ignore garde le refus.
_FORCAGE = Field(default=False, description=(
    "Remplacer les colonnes verrouillées (`readonly`) que cet appel écrit, au lieu "
    "d'être refusé. Réservé au propriétaire du tableau ou à qui le gouverne ; ne vaut "
    "que pour cet appel ; journalisé (ligne, colonne, valeur remplacée)."))

# oto#140 : le forçage par CIBLES. Il coexiste avec le booléen ci-dessus pendant le
# préavis — nommer ses cibles est un geste plus précis, pas un droit différent.
_FORCE = Field(default=None, description=fcg.description_parametre_cibles())

# oto#70 lot 2 : la MÊME mécanique de passage que `_FORCAGE` (query, additif, publié
# tel quel par l'OpenAPI) — mais pas le même objet. Le forçage demande un PALIER (qui
# possède ou gouverne le tableau) ; celui-ci n'en demande aucun : ce n'est pas un droit
# à obtenir, c'est une déclaration à faire. Ouvert à tout écrivain, et le texte le dit,
# sans quoi on irait chercher à qui la demander.
_ORIGINE = Field(default=False,
                 description=dsv2.description_parametre_origine())

# oto#140 : la déclaration qui distingue un IMPORT d'une écriture ordinaire. Même
# mécanique de passage que les deux au-dessus, et aucun palier non plus — ce n'est pas
# un droit à obtenir, c'est un fait à déclarer sur ce que l'appel apporte.
_DONNEES_D_ORIGINE = Field(default=False,
                           description=dsv2.description_donnees_d_origine())


class AppendRowInput(EntreeDatastore):
    datastore: Adresse
    # Le corps ENTIER (cf. `RestBinding.body_field`) : les colonnes du tableau.
    row: dict = Field(default_factory=dict)
    readonly_override: bool = _FORCAGE
    force: Optional[list[str] | str] = _FORCE
    origine_override: bool = _ORIGINE
    donnees_d_origine: bool = _DONNEES_D_ORIGINE

    @field_validator("force", mode="after")
    @classmethod
    def _force_en_liste(cls, v):
        """⚠️ **Une URL ne transporte que des chaînes.** Déclarer `list[str]` seul rend
        le champ INATTEIGNABLE par la surface qui le porte : `?force=a,b` sort en
        `400 invalid_input`, et le paramètre a l'air servi sans l'être — c'est ce qui a
        laissé `?include=` mort quinze jours (#367). Même patron que `node_rows.filter`
        et `projects.include`, pour la même raison.

        La virgule sépare une valeur unique ; la forme répétée (`?force=a&force=b`) arrive
        déjà en liste depuis l'adaptateur. Les deux se combinent."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]



class WriteRowsInput(EntreeDatastore):
    """Le LOT sur la face REST (oto#151) : le même geste que `data_write(rows=…)`.

    Le corps n'est PAS libre ici, à la différence de l'ajout d'une ligne : il porte
    `rows` et les paramètres du geste, que la garde de champ inconnu couvre donc aussi.
    Les paramètres voyagent dans le corps ou en query, au choix de l'appelant."""
    datastore: Adresse
    # `list` sans type d'élément : une ligne qui n'est pas un objet est refusée par le
    # moteur, qui NOMME son rang dans le lot — pydantic rendrait un `invalid_input` nu.
    rows: list = Field(description=(
        "The rows, one object per row, one key per column — the same objects "
        "`POST …/rows` takes one at a time."))
    key: Optional[str] = Field(default=None, description=(
        "Business-key column used to dedup: a row whose key value already exists "
        "is MERGED into that row, otherwise it is created. Defaults to the table's "
        "declared key (`schema.key`)."))
    readonly_override: bool = _FORCAGE
    force: Optional[list[str] | str] = _FORCE
    origine_override: bool = _ORIGINE
    donnees_d_origine: bool = _DONNEES_D_ORIGINE

    @field_validator("force", mode="after")
    @classmethod
    def _force_en_liste(cls, v):
        """Même patron que l'ajout d'une ligne : `?force=a,b` arrive en chaîne."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]


class UpdateRowInput(EntreeDatastore):
    datastore: Adresse
    row_id: str
    # Le corps ENTIER : les colonnes à écrire (patch partiel, jamais un remplacement).
    patch: dict = Field(default_factory=dict)
    readonly_override: bool = _FORCAGE
    force: Optional[list[str] | str] = _FORCE
    origine_override: bool = _ORIGINE
    donnees_d_origine: bool = _DONNEES_D_ORIGINE
    expected_revision: Optional[str] = _precondition("nothing is written")

    @field_validator("force", mode="after")
    @classmethod
    def _force_en_liste(cls, v):
        """⚠️ **Une URL ne transporte que des chaînes.** Déclarer `list[str]` seul rend
        le champ INATTEIGNABLE par la surface qui le porte : `?force=a,b` sort en
        `400 invalid_input`, et le paramètre a l'air servi sans l'être — c'est ce qui a
        laissé `?include=` mort quinze jours (#367). Même patron que `node_rows.filter`
        et `projects.include`, pour la même raison.

        La virgule sépare une valeur unique ; la forme répétée (`?force=a&force=b`) arrive
        déjà en liste depuis l'adaptateur. Les deux se combinent."""
        if v is None:
            return None
        brut = v if isinstance(v, list) else str(v).split(",")
        return [m for m in (str(x).strip() for x in brut) if m]



class ReleaseInput(EntreeDatastore):
    datastore: Adresse
    row_id: str
    # Vide = libération FORCÉE (supervision humaine) ; renseigné = libération GARDÉE.
    worker: str = ""
    # oto#217 : la précondition vaut pour les DEUX régimes. La cantonner au régime
    # gardé l'aurait rendue inerte là où elle protège le plus — le forcé, qui n'a
    # aucune garde et peut retirer un bail repris entre-temps par un autre worker.
    expected_revision: Optional[str] = _precondition("nothing is released")


class Row(BaseModel):
    """Une ligne : trois colonnes de plateforme, plus les colonnes de l'utilisateur.

    `additionalProperties` est VRAI et c'est le fond du modèle — un tableau du
    datastore n'a pas de schéma imposé (le schéma typé d'ADR 0046 est optionnel et se
    lit sur le datastore, pas ici).
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    row_id: str = Field(alias="_id", serialization_alias="_id")
    created_at: Optional[str] = Field(default=None, alias="_created_at",
                                      serialization_alias="_created_at",
                                      description=HORODATAGE)
    updated_at: Optional[str] = Field(default=None, alias="_updated_at",
                                      serialization_alias="_updated_at",
                                      description=HORODATAGE)
    revision: Optional[str] = Field(
        default=None, alias="_revision", serialization_alias="_revision",
        description=("The row's revision, as a string. It changes whenever the row's data "
                     "or its reservation changes. Pass it back as `expected_revision` "
                     "when what you write was computed from this read."))
    # Bail de la file de travail (ADR 0046 D) : ABSENTS quand la ligne n'est pas
    # réservée (les lectures ordinaires ne les sélectionnent pas) — pas nuls, absents.
    claimed_by: Optional[str] = Field(default=None, alias="_claimed_by",
                                      serialization_alias="_claimed_by")
    claimed_until: Optional[str] = Field(default=None, alias="_claimed_until",
                                         serialization_alias="_claimed_until",
                                         description=HORODATAGE)
    claimed_run: Optional[str] = Field(
        default=None, alias="_claimed_run", serialization_alias="_claimed_run",
        description=(
            "The run holding this lease — what links a piece of work to the ROW it "
            "is working on. Present whenever `_claimed_by` is (the three travel "
            "together); null when the lease was taken WITHOUT a run (a person on "
            "the dashboard queue, an agent that passed no `_run_id`). Cleared when "
            "the run gives its rows back (`run_finish`, or the runner concluding "
            "its job), so it answers \"which row is this run on NOW\", not \"which "
            "row did that run work\"."))


class WrittenRow(Row):
    """La ligne écrite, plus le relevé « hors schéma » du geste (#294).

    Les deux clés n'apparaissent QUE si l'écriture a posé des champs absents du schéma
    déclaré — leur présence est le signal, leur absence est le cas normal.
    """
    hors_schema: Optional[list[str]] = None
    hors_schema_hint: Optional[str] = None
    # #319 — valeurs écrites hors des `options` déclarées, sur un tableau qui n'est
    # PAS en format strict : elles passent (le régime est souple par déclaration),
    # mais la réponse le dit désormais. `{champ: valeur}`. Absent quand tout est dans
    # les listes — le cas normal, pas de clé parasite dans la réponse.
    hors_options: Optional[dict] = None
    hors_options_hint: Optional[str] = None
    # #733 — colonnes dont la valeur DÉJÀ EN BASE ne respecte plus le type déclaré,
    # rencontrées en écrivant ailleurs sur la même ligne. `{champ: le refus qu'on
    # aurait rendu}`. L'écriture a réussi : ce n'est ni un refus ni le geste de
    # l'appelant, c'est un fait sur la ligne dit à celui qui passe par là. Déclaré
    # au contrat plutôt que toléré, parce qu'une intégration qui lit l'OpenAPI doit
    # savoir que cette clé peut arriver. Absent quand la ligne est conforme.
    hors_type: Optional[dict] = None
    hors_type_hint: Optional[str] = None
    # ⚠️ **La face REST mangeait ce relevé** (#667, trouvé le 08/09/2026 par la
    # campagne). Le store le PRODUIT — `{champ, motif, valeur_rejetee}` par valeur
    # qu'un schéma armé a refusée — et la face MCP le sert ; ici il n'était pas
    # déclaré, donc Pydantic le retirait de la réponse. Un intégrateur voyait donc
    # une création réussie, sans le moindre signal, avec une colonne vide.
    #
    # Ce que ça a coûté, mesuré : une ligne d'essai née avec sa colonne d'étape à
    # `null`, six passes qui l'ont trouvée hors de leur file, « zéro travail, zéro
    # erreur » — et vingt minutes à chercher une panne de chaîne dont la cause était
    # une valeur avalée deux minutes plus tôt.
    #
    # ⚠️ Distinct de `hors_options` juste au-dessus, et la nuance est tout le sujet :
    # là une valeur ÉCRITE quand même (régime souple), ici une valeur PAS écrite. Les
    # confondre ferait croire à une donnée en base qui n'y est pas.
    valeurs_ecartees: Optional[list[dict]] = None
    valeurs_ecartees_hint: Optional[str] = None
    # Le NUMÉRO du tableau écrit — la forme d'adresse à employer (le nom part en
    # retrait). Déclaré et non seulement toléré : une intégration qui lit l'OpenAPI
    # doit le voir. ⚠️ Le NOM, lui, ne s'ajoute pas ici : le corps de cette remise EST
    # la ligne, et `datastore` y entrerait en collision avec une colonne parfaitement
    # plausible. Le numéro n'a pas ce défaut. Cf. `datastore/identite.py`.
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    # #317 : ce qui a CHANGÉ dans le comportement de la plateforme, dit à l'instant
    # où ça joue — aujourd'hui le retrait de la libération automatique sur état final.
    # Déclaré (et pas seulement toléré par `extra="allow"`) parce qu'un message de
    # migration dont tout l'objet est d'être LU doit exister au contrat publié : une
    # intégration qui lit l'OpenAPI ne saurait pas qu'il peut arriver. Absent quand
    # il n'y a rien à dire — le cas normal.
    notices: Optional[list[str]] = None


class WrittenBatch(BaseModel):
    """Le récapitulatif d'un lot — la MÊME enveloppe que `data_write(rows=…)`.

    Pas de ligne rendue : un lot en porte des milliers. Les relevés du geste
    (`notices`, `hors_schema`, `valeurs_ecartees`…) sont cumulés sur le lot, une
    phrase par fait et non une par ligne ; les plus lus sont déclarés, les autres
    passent (`extra="allow"`), comme sur l'écriture d'une ligne."""
    model_config = ConfigDict(extra="allow")

    datastore: Optional[str] = None
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    inserted: int
    updated: int
    count: int
    key: Optional[str] = None
    ids: list[str]
    hors_schema: Optional[list[str]] = None
    hors_schema_hint: Optional[str] = None
    valeurs_ecartees: Optional[list[dict]] = None
    valeurs_ecartees_hint: Optional[str] = None
    notices: Optional[list[str]] = None


class RowPage(BaseModel):
    rows: list[Row]
    # Le NUMÉRO du tableau lu — la forme d'adresse à employer.
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    # Total du jeu FILTRÉ (pas du tableau) — c'est ce qui pagine le cockpit.
    total: int
    offset: int
    limit: int


class RowQueue(BaseModel):
    rows: list[Row]


class AggregateResult(BaseModel):
    # Un groupe = `{<group_by>: valeur, count: n, sum_<champ>: n, …}` — les clés
    # dépendent des métriques demandées, elles ne sont donc pas déclarables.
    groups: list[dict]


class DeletedRow(BaseModel):
    ok: bool
    id: str


class ReleasedRow(BaseModel):
    ok: bool
    # ⚠️ `False` couvrait DEUX situations opposées, et rien ne les distinguait (#517,
    # 29/08) : « aucun bail » (bénin) et « bail d'un autre travail » (échec réel).
    # `reason` les sépare en vocabulaire fermé, `hint` dit laquelle en toutes lettres.
    released: bool
    # ⚠️ `id` désigne la LIGNE, pas le tableau — c'est pourquoi le tableau se nomme
    # `ns_id` ici et partout ailleurs : réutiliser `id` pour lui créerait un homonyme
    # à l'endroit précis où l'appelant adresse.
    id: str
    # Le NUMÉRO du tableau — la forme d'adresse à employer.
    ns_id: Optional[int] = Field(default=None, description=identite.DESCRIPTION)
    reason: Optional[str] = None
    hint: Optional[str] = None


def _adresse(datastore: str, row_id=None):
    """La MÊME couture que la face agent (`oto_mcp/datastore/jetons.py`).

    ⚠️ Elle est ici parce que les deux faces avaient divergé, et en silence : les
    opérations de SCHÉMA de cette couche résolvaient `slot:<nom>` depuis toujours,
    celles de LIGNES le passaient brut au stockage, qui répondait « datastore inconnu »
    sur un jeton parfaitement valide. *Une divergence qui refuse est visible ; une
    divergence qui répond une cause fausse s'instruit pendant des jours.*

    ⚠️ **Ne prend plus ni contexte, ni `ligne`** (07/09/2026) : ils n'existaient que pour
    ouvrir un store et y lire le bail du run, ce que faisait `@claimed`. Le pronom
    retiré, l'identifiant de ligne n'est plus que vérifié — et c'est cette vérification
    qui rend `400 jeton_mal_place` avec la conduite qui aboutit, là où le stockage aurait
    répondu « tableau inconnu » sur une chaîne correctement orthographiée."""
    try:
        return jetons.resoudre(datastore, row_id,
                               resoudre_slot=access.resolve_datastore_ref)
    except jetons.JetonMalPlace as e:
        raise AuthzDenied(400, "jeton_mal_place", str(e))


def _indice_de_liberation(issue: dict) -> str:
    """La MÊME phrase que la face agent — écrite une fois, servie deux.

    Les deux faces avaient chacune sa formule, et toutes deux mêlaient les deux
    situations dans un seul texte (#517, 29/08)."""
    from ...datastore.core import indice_de_liberation
    return indice_de_liberation(issue)


def _verifier_contenu(contenu) -> None:
    """Ce qui n'a aucun sens comme donnée est refusé AVANT d'atteindre le stockage."""
    try:
        jetons.verifier_contenu(contenu)
    except jetons.JetonMalPlace as e:
        raise AuthzDenied(400, "jeton_mal_place", str(e))


def _json_param(raw: Optional[str], code: str, *, expect=None):
    """Décode un paramètre JSON de query string, avec le refus NOMMÉ de la route."""
    if not raw:
        return None
    try:
        out = json.loads(raw)
    except ValueError:
        raise AuthzDenied(400, code)
    if expect is not None and not isinstance(out, expect):
        raise AuthzDenied(400, code)
    return out


def _list_rows(ctx: ResolvedCtx, inp: ListRowsInput) -> dict:
    ns, _ = _adresse(inp.datastore)
    offset = max(0, inp.offset if inp.offset is not None else 0)
    limit = min(1000, max(1, inp.limit if inp.limit is not None else 50))
    filter_eq = _json_param(inp.filter, "invalid_filter", expect=dict)
    filters = _json_param(inp.filters, "invalid_filters", expect=list)
    layers = _layers(inp.layers)
    # Validé AVANT le `try` : son refus est `invalid_empties`, pas le `invalid_filters`
    # que la clause ci-dessous rend à toute `ValueError` du store.
    empties = _relais_empties(inp.empties)
    store = make_store(ctx.sub)
    try:
        page = store.page_rows(
            ns, offset=offset, limit=limit,
            order_by=inp.order_by or None, order_dir=inp.order_dir,
            q=inp.q or None, filter=filter_eq, filters=filters, layers=layers,
            versions=_versions(inp.versions), fields=inp.fields, **empties)
        return {**page, **identite.numero(store.dernier_tableau)}
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except ValueError as e:
        # Le message du store arrive JUSQU'À l'appelant. Sans lui, un refus
        # SÉMANTIQUE (opérateur inconnu, `null` sur `eq`, date invalide) rend le
        # même `invalid_filters` nu qu'un JSON malformé — l'appelant relit sa
        # syntaxe alors que c'est le SENS de son filtre qui est refusé. Trouvé au
        # smoke prod de v1.87.0 : la garde `null` nommait `empty` et personne ne
        # le voyait.
        raise AuthzDenied(400, "invalid_filters", str(e))


def _aggregate(ctx: ResolvedCtx, inp: AggregateInput) -> dict:
    """Agrégat serveur (ADR 0046 b1 — compteurs du cockpit) : COUNT/SUM/AVG/…
    groupés par un champ JSONB, sans rapatrier les lignes."""
    ns, _ = _adresse(inp.datastore)
    metrics = _json_param(inp.metrics, "invalid_metrics")
    filter_eq = _json_param(inp.filter, "invalid_filter", expect=dict)
    filters = _json_param(inp.filters, "invalid_filters", expect=list)
    try:
        groups = make_store(ctx.sub).aggregate(
            ns, group_by=inp.group_by or None, metrics=metrics,
            filter=filter_eq, q=inp.q or None, filters=filters)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_aggregate", str(e))
    return {"groups": groups}


def _queue(ctx: ResolvedCtx, inp: DatastoreRefInput) -> dict:
    """File de travail (ADR 0046 D) — vue de supervision : les lignes sous bail
    (`_claimed_by`/`_claimed_until`/`_claimed_run`), actif ou expiré. Lecture
    seule. `_claimed_run` est ce qui rend la vue ACTIONNABLE : sans lui elle dit
    qu'un travail tient une ligne, jamais lequel tient laquelle."""
    ns, _ = _adresse(inp.datastore)
    try:
        return {"rows": make_store(ctx.sub).queue(ns)}
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)


def _get_row(ctx: ResolvedCtx, inp: GetRowInput) -> dict:
    ns, rid = _adresse(inp.datastore, inp.row_id)
    layers = _layers(inp.layers)
    empties = _relais_empties(inp.empties)
    try:
        return make_store(ctx.sub).get_row(ns, rid, layers=layers,
                                           versions=_versions(inp.versions), **empties)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except RowNotFound:
        raise AuthzDenied(404, "row_not_found")


def _write_refusal(e: Exception) -> AuthzDenied:
    """Un refus d'écriture est ACTIONNABLE, jamais un 500 opaque.

    `RowValidationError` (schéma strict, transition de cycle de vie non déclarée) et
    `ValueError` (`_id` posé dans le corps #390, clé métier déjà portée) portent le
    message qui dit quoi corriger — le front l'affiche tel quel. Sans cette
    traduction, la face MCP répondait proprement pendant que REST rendait « Internal
    Server Error », et Sentry comptait une faute d'appel comme un bug backend.
    ⚠️ `RowValidationError` DÉRIVE de `ValueError` : l'ordre des branches est le
    contrat, pas un détail de style.

    ⚠️ `RowLocked` (#317) est le cas qui a coûté le plus longtemps : elle n'était
    attrapée NULLE PART côté REST, donc un refus juste sortait en 500 muet pendant que
    la face MCP le traduisait proprement. Elle dérive de `ValueError` depuis le
    05/09/2026 ; sa branche est ici, et son code est un **409** — la requête est bien
    formée, c'est l'état de la ligne qui s'y oppose, et un 400 enverrait corriger ce
    qui n'a rien à corriger.

    ⚠️ `BusinessKeyRequired` (#516) est un troisième cas, et il mérite son propre code :
    un front qui reçoit `invalid_row_input` ne peut que réafficher une phrase, alors
    que `business_key_required` lui dit QUOI proposer — viser une ligne existante.
    Elle dérive de `ValueError` elle aussi : l'ordre des branches reste le contrat.
    Ses `details` (oto#151) disent si l'écriture PORTAIT la clé, et la charge à renvoyer.

    `details` (#545) passe avec, quand le refus en porte : `expected_column` dit au
    front QUEL champ pointer, sans reparser la phrase française du message — la
    reconstituer serait un contrat déguisé. Le CODE, lui, ne bouge pas : un refus de
    schéma reste `row_invalid`, et un code neuf ferait traiter comme nouveau ce que
    les clients gèrent déjà.
    """
    if isinstance(e, RowLocked):
        # 409 et pas 400 : la requête est bien formée, c'est l'ÉTAT de la ligne qui
        # s'y oppose. Un 400 enverrait corriger une requête qui n'a rien à corriger —
        # le geste est d'attendre la fin du bail, ou de libérer la ligne.
        return AuthzDenied(409, "row_locked", str(e))
    if isinstance(e, BusinessKeyRequired):
        return AuthzDenied(400, "business_key_required", str(e), e.details)
    if isinstance(e, RowValidationError):
        return AuthzDenied(400, "row_invalid", str(e), e.details)
    return AuthzDenied(400, "invalid_row_input", str(e))


def _conflit_de_revision(e: RevisionConflict) -> AuthzDenied:
    """409 comme `row_locked`, et AVANT le refus générique : `RevisionConflict` dérive
    de `ValueError`, en 400 elle enverrait corriger une requête juste. La révision en
    place part en `details` : le client relit sans reparser la phrase. Écrite une fois
    pour les trois gestes qui portent la précondition (oto#217)."""
    return AuthzDenied(409, "revision_conflict", str(e),
                       {"current_revision": e.current_revision})


_ECRITURE_DETRUIT = (
    " ⚠️ Une écriture DÉTRUIT ce qui est dans la colonne : sur une colonne "
    "ouverte il n'y a ni annulation ni historique, la valeur précédente "
    "disparaît au moment où la vôtre arrive. ⚠️ **Et il n'y a AUCUN filet "
    "automatique** : le format `origine: \"system\"` a été SUPPRIMÉ le "
    "08/09/2026 — il capturait la valeur précédente à la première écriture "
    "qui la changeait, ce qui exigeait d'avoir été déclaré AVANT que la "
    "ligne existe ; déclaré après coup il ne gardait rien. Ce qui le "
    "remplace est un geste DÉCLARÉ, porté par l'appel qui apporte la "
    "donnée : `donnees_d_origine=true` écrit les DEUX versions — la valeur "
    "courante et l'origine — au moment où la valeur entre. Sans lui, un "
    "écrasement est définitif et rien ne vous le dira après. La face "
    "d'appel n'y change rien : une ligne créée ici et une ligne créée par "
    "l'outil agent se comportent à l'identique.")


def _append_row(ctx: ResolvedCtx, inp: AppendRowInput) -> dict:
    ns, _ = _adresse(inp.datastore)
    _verifier_contenu(inp.row)
    trace: dict = {}
    store = make_store(ctx.sub)
    try:
        refuser_un_lot(store, ns, inp.row)  # oto#48 : un lot enveloppé n'est pas une ligne
        created = store.append_row(ns, inp.row, trace=trace,
                                   readonly_override=inp.readonly_override,
                                   origine_override=inp.origine_override,
                                   donnees_d_origine=inp.donnees_d_origine,
                                   force=fcg.chemins_forces(inp.force))
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except ValueError as e:
        raise _write_refusal(e)
    nsctx = datastore_journal.from_trace(trace, ns)
    datastore_journal.record(
        datastore_journal.TOOL_WRITE, sub=ctx.sub, ctx=nsctx, row_id=created.get("_id"),
        fields=list(inp.row.keys()), forced=store.off_forced,
        to_status=datastore_journal.status_of(created, nsctx))
    # Même relevé « hors schéma » que la face MCP (#294) : les deux faces ne doivent
    # pas diverger sur ce qu'elles signalent d'une écriture — le numéro du tableau
    # non plus.
    return {**created, **store.off_schema_report(),
            **identite.numero(store.dernier_tableau)}


def _write_rows(ctx: ResolvedCtx, inp: WriteRowsInput) -> dict:
    """Le lot REST (oto#151) : `write_rows`, le moteur de `data_write(rows=…)`.

    Un client REST pur n'avait AUCUN chemin de lot : 8 907 `PATCH` ligne à ligne,
    douze minutes sur la production. Le moteur, lui, est indifférent à la face qui
    l'appelle — ce n'était qu'un trou de surface. Mêmes refus nommant la ligne
    fautive, mêmes notices, mêmes bascules datées (`@keep`/`@clear` jugés sur le lot
    ENTIER avant la première ligne)."""
    ns, _ = _adresse(inp.datastore)
    _verifier_contenu(inp.rows)
    store = make_store(ctx.sub)
    try:
        recap = store.write_rows(ns, inp.rows, key=inp.key,
                                 readonly_override=inp.readonly_override,
                                 origine_override=inp.origine_override,
                                 donnees_d_origine=inp.donnees_d_origine,
                                 force=fcg.chemins_forces(inp.force))
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except ValueError as e:
        raise _write_refusal(e)
    tableau = store.dernier_tableau or {}
    # UNE ligne de journal pour le geste, comme la face MCP verse UN relevé d'appel :
    # les colonnes écrites, en union sur le lot.
    datastore_journal.record(
        datastore_journal.TOOL_WRITE, sub=ctx.sub,
        ctx=datastore_journal.NsContext(ns_id=tableau.get("ns_id"),
                                        name=tableau.get("datastore") or str(ns)),
        fields=sorted({k for r in inp.rows if isinstance(r, dict) for k in r}),
        forced=store.off_forced)
    return {**identite.de_releve(tableau, ns), **recap, **store.off_schema_report(),
            **identite.numero(tableau)}


def _update_row(ctx: ResolvedCtx, inp: UpdateRowInput) -> dict:
    # L'état AVANT vient du RELEVÉ de la mutation (`trace`) : c'est celui sur lequel la
    # transition a été validée. Le relire ici courrait avec un write concurrent → le
    # cockpit proposerait d'annuler vers un état que la ligne n'a jamais eu.
    ns, rid = _adresse(inp.datastore, inp.row_id)
    _verifier_contenu(inp.patch)
    trace: dict = {}
    store = make_store(ctx.sub)
    try:
        updated = store.update_row(ns, rid, inp.patch, trace=trace,
                                   readonly_override=inp.readonly_override,
                                   origine_override=inp.origine_override,
                                   donnees_d_origine=inp.donnees_d_origine,
                                   force=fcg.chemins_forces(inp.force),
                                   expected_revision=inp.expected_revision)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except RowNotFound:
        raise AuthzDenied(404, "row_not_found")
    except RevisionConflict as e:
        raise _conflit_de_revision(e)
    except ValueError as e:
        raise _write_refusal(e)
    nsctx = datastore_journal.from_trace(trace, ns)
    datastore_journal.record(
        datastore_journal.TOOL_WRITE, sub=ctx.sub, ctx=nsctx, row_id=rid,
        fields=list(inp.patch.keys()), forced=store.off_forced,
        from_status=trace.get("prev_status"),
        to_status=datastore_journal.status_of(updated, nsctx))
    return {**updated, **store.off_schema_report(),
            **identite.numero(store.dernier_tableau)}


def _delete_row(ctx: ResolvedCtx, inp: DeleteRowInput) -> dict:
    ns, rid = _adresse(inp.datastore, inp.row_id)
    trace: dict = {}
    try:
        make_store(ctx.sub).delete_row(ns, rid, trace=trace,
                                       expected_revision=inp.expected_revision)
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except RowNotFound:
        raise AuthzDenied(404, "row_not_found")
    except RevisionConflict as e:
        raise _conflit_de_revision(e)
    except ValueError as e:
        # Supprimer EST une écriture : même table de traduction que le patch. Elle
        # porte le refus de BAIL (`409 row_locked`) — il n'était attrapé nulle part
        # ici, donc la suppression d'une ligne réservée traversait l'adaptateur et
        # sortait en 500 muet, sans dire qui tenait la ligne ni jusqu'à quand.
        raise _write_refusal(e)
    nsctx = datastore_journal.from_trace(trace, ns)
    datastore_journal.record(datastore_journal.TOOL_DELETE, sub=ctx.sub, ctx=nsctx,
                             row_id=rid, from_status=trace.get("prev_status"))
    return {"ok": True, "id": rid}


def _release_claim(ctx: ResolvedCtx, inp: ReleaseInput) -> dict:
    """Libère le bail d'une ligne. Deux régimes, selon ce que l'appelant SAIT :

    - `worker` renseigné → libération GARDÉE (on ne libère pas le bail d'un autre) ;
    - `worker` absent → libération FORCÉE, supervision humaine (dashboard).

    Le forcé est refusé à un jeton PORTÉ : c'est le vecteur des intégrations
    multi-utilisateurs (un front d'équipe), où « n'importe qui libère la réservation
    de n'importe qui » est le défaut, pas la supervision. Une session interactive
    garde le geste, elle en a la légitimité. Exige l'écriture dans les deux cas.
    """
    ns, rid = _adresse(inp.datastore, inp.row_id)
    worker = (inp.worker or "").strip()
    if not worker and token_scopes.current() is not None:
        raise AuthzDenied(400, "worker_required",
                          "un jeton porté libère SON bail : passer le worker "
                          "utilisé au claim (la libération forcée est réservée "
                          "à la supervision, session interactive)")
    trace: dict = {}
    store = make_store(ctx.sub)
    attendue = inp.expected_revision
    try:
        # La libération FORCÉE reste un booléen : la supervision humaine agit sans
        # garde, il n'y a pas de « bail d'un autre » qui la concerne. La PRÉCONDITION,
        # elle, vaut pour les deux régimes (oto#217).
        issue = (store.release_claim(ns, rid, worker=worker, trace=trace,
                                     expected_revision=attendue) if worker
                 else {"released": store.force_release(ns, rid, trace=trace,
                                                       expected_revision=attendue),
                       "reason": None, "lease": None})
        released = issue["released"]
    except DatastoreNotFound:
        raise ns_not_found(ctx.sub, ns)
    except DatastoreReadOnly:
        raise AuthzDenied(403, "datastore_read_only")
    except RowNotFound:
        # Seulement sous précondition : sans elle, une ligne absente n'a pas de bail et
        # reste le `no_lease` bénin que la flotte lit (#517). Avec elle, l'appelant a
        # demandé un verdict sur la ligne qu'il a lue — elle n'existe plus, on le dit.
        raise AuthzDenied(404, "row_not_found")
    except RevisionConflict as e:
        raise _conflit_de_revision(e)
    except ValueError as e:
        raise _write_refusal(e)
    if released:  # rien libéré = rien changé, donc rien à journaliser
        datastore_journal.record(
            datastore_journal.TOOL_RELEASE, sub=ctx.sub,
            ctx=datastore_journal.from_trace(trace, ns), row_id=rid)
    return {
        "ok": True, "released": released, "id": rid,
        **identite.numero(store.dernier_tableau), "reason": issue["reason"],
        **({} if released else {"hint": _indice_de_liberation(issue)}),
    }


_NS = "/api/datastores/{datastore}"

CAPABILITIES += [
    Capability(
        key="me.datastore.list_rows",
        handler=_list_rows,
        Input=ListRowsInput,
        Output=RowPage,
        authz=SUB_ONLY,
        mcp=None,  # `data_rows` tient déjà la face agent
        rest=RestBinding(verb="GET", path=_NS + "/rows"),
        errors=_REFUS_DE_FORME,
        description=("Page de lignes d’un tableau (tri, recherche, filtres serveur). "
                     "Pagination par `offset` + `limit` avec `total` du jeu filtré, "
                     "pas de curseur — la fin se calcule. Toute colonne déclarée est "
                     "servie, `null` sans valeur. Couches à plat par défaut, "
                     "`layers=nested` pour la forme d’écriture ; guide "
                     "`datastore-semantics`."),
    ),
    Capability(
        key="me.datastore.append_row",
        handler=_append_row,
        Input=AppendRowInput,
        Output=WrittenRow,
        authz=SUB_ONLY,
        mcp=None,
        # Corps LIBRE (les colonnes) + 201 : les deux contrats d'avant la migration.
        rest=RestBinding(verb="POST", path=_NS + "/rows", status=201, body_field="row"),
        description=("Ajoute UNE ligne à un tableau — le corps EST la ligne : un objet, "
                     "une clé par colonne. Pas de lot ici : un corps dont l'unique clé "
                     "— ou la clé `rows`, quelles que soient les autres — porte une "
                     "liste d'objets est refusé (`400 batch_body`) ; le lot passe par "
                     "`POST …/rows/batch` (ou `data_write(rows=[…])` côté agent), ou "
                     "par un upload signé NDJSON/CSV (`oto_upload_url` → "
                     "`PUT /api/upload/{token}`) pour les volumes. "
                     "`readonly_override=true` remplace les colonnes verrouillées "
                     "de cet appel — propriétaire ou gouvernant du tableau seulement, "
                     "et journalisé. " + dsv2.description_parametre_origine()
                     + " " + couches.DESCRIPTION_ECRITURE
                     + " `readonly`, clé métier, ce qu'une écriture détruit : guide "
                     "`datastore-semantics`." + _ECRITURE_DETRUIT),
    ),
    Capability(
        key="me.datastore.write_rows",
        handler=_write_rows,
        Input=WriteRowsInput,
        Output=WrittenBatch,
        authz=SUB_ONLY,
        mcp=None,  # `data_write(rows=…)` tient déjà la face agent
        rest=RestBinding(verb="POST", path=_NS + "/rows/batch"),
        errors=_REFUS_D_ADRESSE + _REFUS_D_ECRITURE + (
            _JETON_MAL_PLACE,
            DeclaredError(400, "row_invalid",
                          "une ligne du lot est refusée par le schéma ou le cycle de "
                          "vie : le message nomme son rang (et sa clé) et dit combien "
                          "de lignes étaient déjà écrites avant l'arrêt"),
            DeclaredError(400, "business_key_required",
                          "le tableau exige sa clé métier à la création et une ligne "
                          "du lot ne la porte pas, ou ne désigne aucune ligne "
                          "existante : le message nomme la ligne ; "
                          "`details.cle_portee` dit si elle portait la clé, "
                          "`details.a_renvoyer` le fragment à renvoyer"),
        ),
        description=("Écrit un LOT de lignes en un appel — le même geste que "
                     "`data_write(rows=[…])` côté agent : même moteur, mêmes refus, "
                     "mêmes notices. Corps `{\"rows\": [{…}, …], \"key\": "
                     "\"<colonne>\"}` ; `key` facultatif, défaut = la clé métier "
                     "déclarée. Une ligne dont la clé existe déjà est FUSIONNÉE dans "
                     "celle-ci, sinon elle est créée. Réponse : `inserted`, "
                     "`updated`, `count`, `ids`, et les relevés du geste cumulés sur "
                     "le lot. Une écriture qui porte un mot refusé (`@keep`, "
                     "`@clear`, après leur date) est refusée ENTIÈRE, rien n'est "
                     "écrit. ⚠️ Sinon le lot n'est PAS atomique : une ligne refusée "
                     "arrête le lot, les lignes d'avant restent écrites, et le refus "
                     "dit à quelle ligne reprendre. Pour un volume au-delà de "
                     "quelques milliers de lignes, l'upload signé NDJSON/CSV. "
                     + dsv2.description_donnees_d_origine()
                     + " " + couches.DESCRIPTION_ECRITURE + _ECRITURE_DETRUIT),
    ),
    Capability(
        key="me.datastore.get_row",
        handler=_get_row,
        Input=GetRowInput,
        Output=Row,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="GET", path=_NS + "/rows/{row_id}"),
        errors=_REFUS_DE_FORME,
        description="Lit une ligne par son `_id` (deep-link de fiche).",
    ),
    Capability(
        key="me.datastore.update_row",
        handler=_update_row,
        Input=UpdateRowInput,
        Output=WrittenRow,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="PATCH", path=_NS + "/rows/{row_id}", body_field="patch"),
        errors=_REFUS_D_ADRESSE + _REFUS_D_ECRITURE + (
            _JETON_MAL_PLACE, _PRECONDITION_REFUSEE, _LIGNE_ABSENTE,
            DeclaredError(400, "row_invalid",
                          "la ligne fusionnée est refusée par le schéma strict ou par "
                          "le cycle de vie (transition non déclarée) : le message nomme "
                          "les champs fautifs, `details.expected_column` la colonne "
                          "quand il y en a une, `details.a_renvoyer` le fragment de "
                          "ligne à renvoyer (gabarits `<…>` à remplacer)"),
        ),
        description=("Modifie une ligne (patch partiel ; le corps EST le patch). "
                     "`?expected_revision=` (query, jamais le corps) = la `_revision` "
                     "lue, quand ce qu'on écrit a été calculé d'après elle : si la ligne "
                     "a changé depuis, rien n'est écrit (`409 revision_conflict`). "
                     "Deux écritures sur des colonnes différentes ne s'écrasent jamais. "
                     "`readonly_override=true` remplace les colonnes verrouillées "
                     "de cet appel — propriétaire ou gouvernant du tableau seulement, "
                     "et journalisé. " + dsv2.description_parametre_origine()
                     + " " + couches.DESCRIPTION_ECRITURE + _ECRITURE_DETRUIT),
    ),
    Capability(
        key="me.datastore.delete_row",
        handler=_delete_row,
        Input=DeleteRowInput,
        Output=DeletedRow,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="DELETE", path=_NS + "/rows/{row_id}"),
        errors=_REFUS_D_ADRESSE + _REFUS_D_ECRITURE + (
            _JETON_MAL_PLACE, _PRECONDITION_REFUSEE, _LIGNE_ABSENTE),
        description=("Supprime une ligne — définitivement, la ligne est retirée de la "
                     "table. `?expected_revision=` (query) = la `_revision` lue, quand "
                     "c'est sur cette lecture qu'on a décidé de supprimer : si la ligne "
                     "a changé depuis, rien n'est supprimé (`409 revision_conflict`). "
                     "Jugée sous le verrou de la ligne, après le bail — une ligne "
                     "réservée par un autre travail ne se supprime pas."),
    ),
    Capability(
        key="me.datastore.release_claim",
        handler=_release_claim,
        Input=ReleaseInput,
        Output=ReleasedRow,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="POST", path=_NS + "/rows/{row_id}/release"),
        errors=_REFUS_D_ADRESSE + (
            _JETON_MAL_PLACE, _PRECONDITION_REFUSEE, _LIGNE_ABSENTE, _WORKER_REQUIS,
            DeclaredError(400, "invalid_row_input",
                          "`expected_revision` est illisible (la `_revision` est une "
                          "chaîne de chiffres) : rien n'est libéré"),
        ),
        description=("Libère le bail d'une ligne (gardée avec `worker`, forcée sans). "
                     "`expected_revision` = la `_revision` de la ligne telle qu'elle "
                     "vous a été présentée : une réservation reprise depuis par un "
                     "autre travail rend `409 revision_conflict` plutôt que de lui "
                     "retirer sa ligne. « Rien à rendre » reste un succès "
                     "(`released: false`, `reason: no_lease`)."),
    ),
    Capability(
        key="me.datastore.queue",
        handler=_queue,
        Input=DatastoreRefInput,
        Output=RowQueue,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="GET", path=_NS + "/queue"),
        description="File de travail : les lignes sous bail (supervision, lecture seule).",
    ),
    Capability(
        key="me.datastore.aggregate",
        handler=_aggregate,
        Input=AggregateInput,
        Output=AggregateResult,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="GET", path=_NS + "/aggregate"),
        description="Agrégat serveur d'un tableau (COUNT/SUM/AVG/MIN/MAX, groupé).",
    ),
]
