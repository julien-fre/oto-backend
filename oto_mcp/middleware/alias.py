"""`ToolAliasMiddleware` — les noms d'outils au nom du PRODUIT du tenant."""
from __future__ import annotations

import logging

from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from .. import tool_alias
from ..auth_hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)


class ToolAliasMiddleware(Middleware):
    """Traduit les noms d'outils de la plateforme au nom du PRODUIT du tenant.

    OUTERMOST, et c'est tout l'intérêt : le nom canonique (`oto_doc`) est rétabli
    AVANT que quoi que ce soit d'autre ne le lise — gates de contexte d'appel
    (`call_axes.axes_for`), politique de rédaction (`namespace_of`), visibilité de
    session, journal `tool_calls`. Rien en aval n'apprend qu'un alias existe, donc
    rien en aval ne peut diverger : les toggles, l'audit et les références
    `<tool:slug>` des procédures restent écrits en canonique, hier comme demain.

    En SORTIE, `on_list_tools` est au contraire le DERNIER à retoucher la liste (une
    chaîne extern→interne se déroule en sens inverse au retour) : le renommage
    s'applique après le filtrage de visibilité et l'enrichissement des descriptions,
    sur la liste réellement servie.

    Deux crans de prudence :

    - un alias qui écraserait un outil RÉEL est abandonné (le nom canonique reste
      servi). Ça ne devrait pas arriver — `tool_alias.normalize_prefix` refuse un
      préfixe qui est un namespace déclaré — mais la liste est ici, la vérification
      est gratuite, et la conséquence d'une collision serait qu'un outil en éclipse
      un autre sans que personne ne le voie ;
    - aucun accès DB : `tool_alias.prefix_for` lit le registre d'émetteurs en
      mémoire. Ce hook s'exécute DANS la boucle (serveur MONO-LOOP).

    Fail-open partout : pas de sub (endpoint anonyme, découverte), pas de préfixe
    déclaré, ou erreur ⟹ les noms canoniques, à l'octet près.
    """

    @staticmethod
    def _prefix() -> str:
        try:
            return tool_alias.prefix_for(current_user_sub_from_token())
        # noqa: SILENT — dette déclarée : préfixe de tenant perdu ⇒ notre identité servie (#424, verdict C)
        except Exception:  # noqa: BLE001 — un nom d'outil ne casse jamais un appel
            return ""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        prefix = self._prefix()
        if not prefix:
            return tools
        try:
            pris = {t.name for t in tools}
            out = []
            for t in tools:
                maj = {}
                nom = tool_alias.public(t.name, prefix)
                if nom != t.name and nom not in pris:
                    maj["name"] = nom
                # La DESCRIPTION aussi : c'est sur elle que le modèle choisit, et 27
                # d'entre elles renvoient à un outil voisin (« resolve it with
                # oto_kb »). Laissée en canonique, elle fait rappeler un nom que le
                # client réaffiche sous notre marque — le défaut qu'on corrige, par
                # la porte de derrière. Mesuré à ~2 ms pour les 200 Ko servis, et nul
                # pour un tenant sans préfixe (on sort avant).
                desc = tool_alias.rewrite_prose(t.description or "", prefix)
                if desc != (t.description or ""):
                    maj["description"] = desc
                out.append(t.model_copy(update=maj) if maj else t)
            return out
        except Exception:
            logger.warning("renommage des outils échoué (fail-open, noms canoniques)",
                           exc_info=True)
            return tools

    async def on_initialize(self, context, call_next):
        """`serverInfo` au nom du PRODUIT — le dernier recoin de la classe de défaut
        que ce middleware ferme : les outils disaient `tulina_…` mais le handshake
        annonçait encore `oto`. `name` suit le `tool_prefix` déclaré (l'identifiant,
        cohérent avec les noms d'outils), `title` le nom du tenant (le libellé
        humain). Rien de déclaré ⟹ l'annonce d'avant, à l'octet près (fail-open)."""
        result = await call_next(context)
        if result is None or getattr(result, "serverInfo", None) is None:
            return result
        try:
            name, title = tool_alias.server_identity_for(current_user_sub_from_token())
            if not name and not title:
                return result
            maj = {}
            if name:
                maj["name"] = name
            if title:
                maj["title"] = title
            return result.model_copy(
                update={"serverInfo": result.serverInfo.model_copy(update=maj)})
        except Exception:  # noqa: BLE001 — une identité d'affichage ne casse pas un handshake
            logger.warning("renommage du serverInfo échoué (fail-open)", exc_info=True)
            return result

    async def on_call_tool(self, context, call_next):
        prefix = self._prefix()
        if not prefix:
            return await call_next(context)
        name = getattr(context.message, "name", "") or ""
        canonical = tool_alias.canonical(name, prefix)
        if canonical != name:
            context = context.copy(
                message=context.message.model_copy(update={"name": canonical}))
        try:
            return await call_next(context)
        except McpError as e:
            raise self._erreur_traduite(e, prefix) from e

    @staticmethod
    def _erreur_traduite(erreur: McpError, prefix: str) -> McpError:
        """Le contrat d'erreur (`ErrorEnvelopeMiddleware`, plus interne) NOMME des
        outils dans son message et son `hint` — « appelle-le via oto_call(…) »,
        « installe le connecteur — oto_connector(op='select') ». C'est le PREMIER mur
        que rencontre un agent, donc le texte le plus lu après le socle : le laisser
        en canonique ferait suivre la consigne au nom près, et réafficher notre marque
        à l'écran de quelqu'un qui n'est pas notre client.

        Fail-open : toute difficulté rend l'erreur d'ORIGINE — une erreur à traduire
        ne devient jamais une erreur de traduction.
        """
        try:
            data = getattr(erreur.error, "data", None)
            message = tool_alias.rewrite_prose(erreur.error.message or "", prefix)
            # `data` porte ce que le tool y a mis : l'enveloppe `{oto: {hint}}` du
            # contrat d'erreur, ou tout autre chose (`oto_tool_schema` y met un
            # schéma). On ne touche QUE le hint, et seulement s'il est là.
            oto = data.get("oto") if isinstance(data, dict) else None
            hint = oto.get("hint") if isinstance(oto, dict) else None
            traduit = tool_alias.rewrite_prose(hint, prefix) if hint else hint
            if message == erreur.error.message and traduit == hint:
                return erreur
            if traduit != hint:
                data = {**data, "oto": {**oto, "hint": traduit}}
            return McpError(ErrorData(code=erreur.error.code, message=message, data=data))
        except Exception:  # noqa: BLE001
            logger.warning("traduction du message d'erreur échouée (fail-open)",
                           exc_info=True)
            return erreur
