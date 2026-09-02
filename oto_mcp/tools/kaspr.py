"""Kaspr — enrichissement contacts B2B depuis URL LinkedIn (emails + téléphones).

Provider user-only : pas de quota plateforme, chaque user pose sa clé sur
`/account`. Kaspr facture en crédits à l'enrichissement.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# La normalisation du slug LinkedIn (URL → slug nu, sinon Kaspr 500) vit dans le
# client oto-core (`oto.tools.kaspr.client.linkedin_slug`), pas ici — logique
# canonique partagée par tous les consommateurs. Ce wrapper ne fait que traduire
# une erreur Kaspr en McpError actionnable.


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde, non utilisé ici)
    """Sonde « tester la connexion » : la clé authentifie-t-elle vraiment ?

    `verify_key()` (oto-core) fait un POST sentinel sans effet de bord ni crédit
    consommé (Kaspr n'a pas de `/me`) — 401 sur clé invalide. Lève — le message
    remonte tel quel à l'UI.
    """
    from oto.tools.kaspr.client import KasprClient
    KasprClient(api_key=fields["key"]).verify_key()


def register(mcp: FastMCP) -> None:
    from oto.tools.kaspr.client import KasprClient

    connector_verify.register("kaspr", _verify)

    def _client() -> tuple[KasprClient, bool]:
        key, is_platform = access.resolve_api_key("kaspr")
        return KasprClient(api_key=key), is_platform

    @mcp.tool()
    def kaspr_enrich_linkedin(
        linkedin_id: str,
        name: Optional[str] = None,
        with_phone: bool = False,
        data_to_get: Optional[list[str]] = None,
    ) -> dict:
        """Enrich a LinkedIn profile with emails and (optionally) phone numbers.

        Cost: 1 credit per email, +1 per phone if `with_phone=True`.

        Args:
            linkedin_id: the person's LinkedIn handle. Either the bare slug
                ("alexis-laporte") OR the full profile URL
                ("https://www.linkedin.com/in/alexis-laporte/") — both work, the
                slug is extracted automatically. NOT a name or a search query.
            name: Optional fallback name if the slug alone is ambiguous.
            with_phone: Request mobile/work phones (extra credits cost).
            data_to_get: Kaspr field names, from its own enum — "workEmail",
                "directEmail", "phone". Kaspr answers 500 on a name it does not
                know. Omitted, the call asks for ["workEmail", "phone"] — NOT
                every field.
        """
        client, is_platform = _client()
        # with_phone=True → include "phone" in data_to_get (costs extra credits)
        effective_data = data_to_get
        if effective_data is None and with_phone:
            effective_data = ["workEmail", "phone"]
        try:
            # Le client oto-core normalise linkedin_id (URL → slug) avant l'appel.
            result = client.enrich_linkedin(
                linkedin_id=linkedin_id,
                name=name,
                is_phone_required=with_phone,
                data_to_get=effective_data,
            )
        except ValueError as e:
            # Refus LOCAL du client oto-core — un nom de `dataToGet` hors des
            # trois que Kaspr accepte. Son message NOMME déjà les valeurs
            # acceptées : on le rend tel quel plutôt que de le noyer sous une
            # hypothèse de profil introuvable (même forme que `tools/cognism.py`).
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        except Exception as e:
            # ⚠️ Un 5xx de Kaspr NE PROUVE PAS une panne : Kaspr rend 500 sur au
            # moins deux fautes d'entrée connues — une URL complète au lieu du
            # slug nu (relevé le 15/06) et un `dataToGet` inconnu (reproduit le
            # 01/09 : `["emails","phones","company"]` → 500 `TypeError: Cannot
            # read properties of undefined (reading 'push')`). Le message disait
            # « ce n'est pas ton entrée » : une affirmation qu'on ne peut pas
            # faire, et qui fermait la seule piste correcte dans le cas le plus
            # atteignable. Il NOMME donc les deux fautes, et borne la reprise.
            #
            # La reprise bornée à UNE tentative différée est cohérente avec le
            # drapeau machine : cette McpError(INVALID_PARAMS) est classée
            # `invalid_input` / `retryable: false` par `error_taxonomy`, où
            # `retryable` veut dire « rejouable TEL QUEL ». Rejouer tel quel n'est
            # justement pas le premier geste ici — c'est corriger l'entrée.
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if status and status >= 500:
                msg = (f"Kaspr a rendu une erreur serveur ({status}) — ce qu'il rend "
                       "AUSSI sur une requête malformée. Vérifie `linkedin_id` (slug "
                       "nu ou URL de profil, jamais un nom ni une recherche) et "
                       "`data_to_get` (seuls workEmail, directEmail et phone "
                       "existent). Si l'entrée est correcte : une seule nouvelle "
                       "tentative, différée.")
            elif status == 402:
                # 402 = crédits insuffisants sur le COMPTE Kaspr. Le message
                # générique en-dessous renvoyait l'agent vérifier le profil
                # LinkedIn — la seule piste qui ne peut RIEN donner ici, et qui
                # se solde par une relecture du slug puis une nouvelle tentative
                # identique. Relevé le 2026-09-02 (appel 1345911) : un 402 rendu
                # comme « Vérifie le profil LinkedIn (slug ou URL valide) ».
                # Le client oto-core retire déjà `phone` et rejoue UNE fois quand
                # il était demandé : un 402 qui remonte jusqu'ici est donc un
                # refus du compte, pas un choix de champs à revoir.
                msg = ("Kaspr a refusé l'appel faute de crédits (402) — c'est le "
                       "compte Kaspr qui est à sec, pas ton entrée. Le profil et "
                       "`data_to_get` n'y sont pour rien : ni les corriger ni "
                       "réessayer n'y changera quoi que ce soit tant que le compte "
                       "n'est pas rechargé.")
            else:
                msg = (f"Kaspr n'a pas pu enrichir `{linkedin_id}` ({e}). Vérifie le "
                       f"profil LinkedIn (slug ou URL valide).")
            raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))
        if is_platform:
            access.record_platform_usage("kaspr")
        return result
