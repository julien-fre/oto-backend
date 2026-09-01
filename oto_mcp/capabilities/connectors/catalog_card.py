"""La CARTE d'un connecteur, déclarée — la forme servie en `verbose=true` (#667).

`GET /api/me/connectors?verbose=true` sert la ligne entière du catalogue
(`providers.public_catalog()`), soit treize clés de premier niveau de plus que le
mode compact. Aucune n'était déclarée : `MyConnectorRow` est en `extra="allow"`, donc
elles traversaient le modèle sans laisser de trace dans le schéma, qui n'annonçait
qu'`additionalProperties: true`. Un front tiers qui dérive son formulaire de
credential du contrat ne pouvait donc rien en tirer, alors que la donnée arrivait —
c'est le bloquant qu'a remonté le consommateur REST.

**Ces modèles DÉCRIVENT, ils ne valident pas** (même régime que `Capability.Output`,
cf. `capabilities/_types.py`) : le handler continue de rendre des `dict` construits
par `providers.public_catalog()`. Déclarer ne peut donc pas déplacer un octet du
payload — et c'est la seule raison pour laquelle ce lot est sûr à poser sur une
surface déjà consommée.

**Domicile.** Ici et pas dans `selection.py` : la forme décrite est celle que produit
`providers/__init__.py::public_catalog`, pas celle que compose la capacité. Les deux
faces l'utilisent — la projection authentifiée (`connectors.me`) l'enrichit de
`connect.callback_url` / `connect.app_ready`, absents du catalogue public servi sans
auth (`connectors/flow.py`).

⚠️ Ce qui est déclaré ici doit rester le REFLET de ce que le producteur rend. Le
producteur reste `providers/__init__.py::public_catalog` et `_model.Connector.auth` :
un champ ajouté là-bas et oublié ici redevient un champ servi et non déclaré, ce qui
est exactement le défaut qu'on répare. `tests/test_carte_connecteur_declaree.py` tient
le cliquet : il compare les clés SERVIES aux clés DÉCLARÉES, connecteur par
connecteur.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DocSection(BaseModel):
    """Une section de la doc « how-to » d'un connecteur, en markdown.

    Curée par connecteur (`connectors/docs/<nom>.md`), lue par
    `connectors/docs_reader.py`. `kind` observé : `prerequisite` | `setup` | `usage` |
    `note` — jeu ouvert par construction (il vient des titres du markdown), donc
    déclaré `str` et non un énuméré : figer un jeu que la prose peut élargir ferait
    mentir le contrat au premier fichier de doc ajouté."""
    kind: str
    title: str
    body_md: str


class CredentialField(BaseModel):
    """Un champ de saisie du credential — la FORME, jamais une valeur.

    C'est ce sur quoi le dashboard boucle pour rendre son formulaire. Homonyme
    volontaire de `providers._model.CredentialField`, qui est la dataclass qui le
    PRODUIT : celui-ci est sa projection servie, et il ne porte donc pas les champs
    internes (`whitespace_significant` n'est pas sur le fil).

    `secret=True` ⟹ la valeur n'est **jamais** rendue en lecture (ni tronquée, ni
    vidée) : sa clé est absente du corps. Ce modèle ne décrit que la saisie."""
    name: str
    label: str
    secret: bool
    required: bool
    help: str
    when: list[str] = Field(default=[], description=(
        "Valeurs du champ discriminant (`AuthDescriptor.field_discriminator`) pour "
        "lesquelles ce champ-ci est pertinent. ⚠️ Liste VIDE = pertinent QUEL QUE "
        "SOIT le discriminant — « vide » veut dire « toujours », jamais « jamais » "
        "(c'est le cas des ~90 connecteurs qui n'en déclarent pas). Renseignée, elle "
        "dit deux choses d'un coup : le champ ne s'affiche que pour ces valeurs, et "
        "son `required` ne s'applique que là."))
    choices: list[str] = Field(default=[], description=(
        "Jeu FERMÉ de valeurs acceptées — un select, pas un champ libre. Vide = "
        "libre. Une valeur hors liste est refusée à l'ÉCRITURE, avec le jeu attendu "
        "dans le message."))


class AuthDescriptor(BaseModel):
    """Descripteur d'auth unifié (ADR 0024) — la source unique du rendu de la face
    credential, quel que soit le mécanisme.

    ⚠️ `method` est un jeu FERMÉ, et il est consommé par un `switch` dans un autre
    dépôt (oto-dashboard) : y ajouter une valeur casse en silence (branche `default` →
    panneau de connexion vide). Il est néanmoins déclaré `str` et non `Literal` ici, et
    c'est délibéré : ce modèle DÉCRIT une réponse servie, et un énuméré au contrat
    ferait échouer la génération de client d'un tiers le jour où le serveur en rend une
    sixième valeur — le contrat doit vieillir moins mal que la liste. Les valeurs
    servies au 2026-09-01 : `hosted` | `remote` | `oauth` | `cookie` | `none` |
    `secret` (cf. `providers/_model.py::auth_method`, qui en est le domicile)."""
    method: str
    # `multi_account` | `single`. DÉRIVÉ, pas déclaré par le connecteur (sauf
    # exception motivée) : cf. `Connector.auth_multi_account`.
    cardinality: str
    # Le MOT que l'utilisateur emploie pour un compte de CE connecteur, quand
    # « compte » est faux chez lui (un compte Slack du coffre EST un workspace). Le
    # front l'affiche tel quel. Toujours renseigné — défaut « compte ».
    account_noun: str
    field_discriminator: str = Field(default="", description=(
        "Le champ dont la valeur sélectionne les autres, quand il y en a un "
        "(`auth_mode` chez `http`). **Chaîne VIDE**, jamais `null`, quand il n'y en "
        "a pas. ⚠️ Tant qu'aucune valeur n'est choisie, TOUS les champs sont "
        "pertinents : le serveur ne masque rien avant que la saisie ait tranché, "
        "parce que masquer serait deviner. Un formulaire qui filtrerait dès "
        "l'ouverture cacherait des champs que la pose exige."))
    # Le canal hébergé de ce connecteur, quand il en porte un (les six canaux
    # unipile). `null` = ce connecteur n'est pas un canal hébergé — sans lui, une
    # carte « connecter un compte » ne peut pas savoir lequel des six elle représente.
    hosted_channel: Optional[str] = None
    # Le connecteur qui DÉTIENT le credential, quand ce n'est pas celui-ci. `null` =
    # il détient le sien. Un connecteur qui délègue n'a aucun champ à saisir : c'est
    # le porteur nommé ici que l'écran doit envoyer poser la clé.
    credential_of: Optional[str] = None
    # Schéma de saisie. Vide hors `method=secret` (les autres mécanismes ont leur flux
    # dédié, cf. `connect`).
    fields: list[CredentialField] = []


class ConnectParamOption(BaseModel):
    """Une valeur d'un choix fermé d'un paramètre de flux (le front rend un select)."""
    value: str
    label: str


class ConnectParam(BaseModel):
    """Une valeur que l'utilisateur doit fournir pour démarrer le flux de connexion."""
    name: str
    label: str
    required: bool
    default: str = ""
    help: str = ""
    # Non vide ⟹ liste FERMÉE. Vide ⟹ saisie libre. C'est le domicile unique de ces
    # valeurs (`connectors/flow.py::FlowParam`), jamais recopié dans un front.
    options: list[ConnectParamOption] = []


class ConnectFlow(BaseModel):
    """La FORME du geste « connecter » — jamais une URL d'autorisation ni un nom de
    capacité (`/api/connectors` est servie SANS auth, et le chemin d'appel est fixe
    côté client). `null` sur les ~85 connecteurs qui n'ont pas de flux : le front rend
    alors son formulaire de champs habituel.

    Les deux derniers champs n'existent QUE sur la projection authentifiée
    (`GET /api/me/connectors?verbose=true`) et jamais dans le catalogue public : ils
    répondent à qui demande, ce qu'un catalogue anonyme ne peut pas faire."""
    label: str
    params: list[ConnectParam] = []
    # L'URL de retour de consentement à enregistrer chez le fournisseur, DÉRIVÉE de
    # l'environnement (jamais écrite en dur : une URL de prose ment dès qu'on la lit
    # depuis la preprod, et le consentement échoue sur un `redirect_uri_mismatch`
    # incompréhensible). `null` = flux sans retour déclaré.
    callback_url: Optional[str] = None
    # Cet utilisateur a-t-il déjà une app OAuth à disposition (la sienne, celle de son
    # org, ou celle de l'éditeur) ? `null` = question non déclarée par le connecteur —
    # le front doit alors rester MUET plutôt qu'affirmer qu'il reste une app à poser.
    app_ready: Optional[bool] = None


class FreeTier(BaseModel):
    """Free-tier (ADR 0031) : la clé plateforme est ouverte sans grant, avec un quota
    gratuit par utilisateur et par jour. `null` sur la carte = pas de free-tier."""
    daily_quota: int
