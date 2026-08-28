"""`FieldRedactionMiddleware` — la politique de rédaction appliquée au résultat."""
from __future__ import annotations

from fastmcp.server.middleware import Middleware

from .. import redaction
from ..tool_visibility import namespace_of


class FieldRedactionMiddleware(Middleware):
    """Redacte les champs sensibles du RÉSULTAT de tout tool, selon la politique de
    rédaction de l'org active (ADR 0009/0015, « la policy gouverne l'exposition »).

    Point d'application unique de la rédaction : remplace le filtrage qui vivait au
    niveau des clients (folk/silae/pennylane) et couvre désormais **tous** les
    connecteurs (unipile, ATS…) sans câblage par tool. La cascade (org → défaut
    serveur → vide) est résolue par `access.resolve_field_filter(<namespace>)` ;
    `FieldFilter` matche par nom de clé feuille, récursivement.

    Doit être enregistré **en dernier** (`add_middleware`) : l'exécution étant en
    ordre inverse, il enveloppe les autres et retouche le **résultat final**.

    Deux canaux à garder cohérents : un tool renvoie son dict en `structured_content`
    ET/OU en `content` (TextContent JSON). On redacte la donnée puis on réémet les
    deux depuis la version redactée — sinon un canal brut fuirait (Claude lit surtout
    `content`).

    **Fail-closed** : si l'application de la rédaction lève alors qu'une politique
    existe (ex. Faker absent pour `pseudonym`), on RETIENT la sortie plutôt que de
    laisser fuiter le brut. Une simple absence de policy (`is_empty`) = passe-through.
    """

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        name = getattr(context.message, "name", "") or ""
        service = namespace_of(name)
        payload = redaction.extract_payload(result)   # dict | list | None

        # Capture passive du schéma observé (squelette clés+types, JAMAIS de valeurs) :
        # source de vérité du schéma de rédaction. Hors spine/méta. Best-effort.
        if payload is not None and service not in _SPINE_SERVICES:
            _observe_schema(service, payload)

        # Rédaction déléguée à la logique PARTAGÉE (`redaction.py`) — même chemin que
        # `oto_call` (ADR 0036), pour qu'un outil dispatché soit redacté à l'identique.
        try:
            red = redaction.redact_payload(service, payload)
        except redaction.RedactionWithheld:
            return redaction.withheld_result(name)
        if red is redaction.PASSTHROUGH:
            return result
        return redaction.rebuild_result(result, red)


# Spine / méta : pas de capture de schéma (pas des connecteurs ; `data` =
# données arbitraires de l'user → bruit). La rédaction, elle, reste possible partout.
_SPINE_SERVICES = {"oto", "run", "feedback", "data"}


def _observe_schema(service: str, payload) -> None:
    from ..connectors import schema_store as connector_schema_store
    connector_schema_store.observe(service, payload)
