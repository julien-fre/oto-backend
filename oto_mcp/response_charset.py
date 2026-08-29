"""Garde ASGI : ce que le serveur écrit en UTF-8, il l'ANNONCE en UTF-8.

**Ce n'est pas la réparation d'un incident, c'est une protection.** Les octets servis
par `/mcp` sont — et ont toujours été — de l'UTF-8 valide : une description d'outil
« déjà configuré » part sur le fil en `\\xc3\\xa9`, pas en mojibake. Ce qui manquait
est l'ÉTIQUETTE : la réponse SSE sortait en `text/event-stream` **nu**, sans
`charset`. Or un client qui suit la spec applique alors le défaut historique de
HTTP/1.1 pour `text/*` (ISO-8859-1, RFC 2616 §3.7.1) et lit « dÃ©jÃ  » là où le
serveur a écrit « déjà ». Le contenu est bon, l'en-tête laissait un client
consciencieux se tromper.

Mesuré le 2026-08-29 sur `requests.utils.get_encoding_from_headers` :

    'text/event-stream'                 -> 'ISO-8859-1'   ← le piège
    'text/event-stream; charset=utf-8'  -> 'utf-8'
    'application/json'                  -> 'utf-8'        ← déjà bon (RFC 8259)
    'application/json; charset=utf-8'   -> 'utf-8'

Donc : SSE est le cas qui MORD, JSON est complété par cohérence (c'est ce que
servent Logto, GitHub et l'immense majorité des APIs — pas une déviation).

**Pourquoi un middleware et pas un paramètre.** Les deux content-types sont des
CONSTANTES du SDK `mcp` (`mcp/server/streamable_http.py`, `CONTENT_TYPE_SSE` /
`CONTENT_TYPE_JSON`), posées à la main dans le dict `headers` de la réponse. Or
Starlette n'ajoute son `charset=utf-8` que quand il compose lui-même le
`content-type` depuis `media_type` (`Response.init_headers` : un `content-type`
déjà présent dans `headers` désactive `populate_content_type`). Le SDK court-circuite
donc la seule mécanique qui aurait complété l'en-tête, et FastMCP 3.4.2 n'expose
aucun réglage dessus. Compléter l'en-tête au vol est le seul point de reprise qui ne
patche ni le SDK ni la lib.

**Périmètre volontairement étroit** — la condition pour qu'une couche traversée par
100 % des réponses soit acceptable :

- **on n'ajoute jamais, on COMPLÈTE** : un `content-type` qui porte déjà un
  `charset=` (ex. `text/markdown; charset=utf-8`, servi par `api/public.py`) est
  laissé intact, à l'octet près ;
- **allowlist de deux types**, pas une règle sur `text/*` : `application/pdf`,
  `application/zip`, `image/svg+xml` — tout ce que le serveur sert en binaire — n'est
  pas touché, et ne peut pas l'être par accident ;
- **aucun filtre de chemin** : c'est le MEDIA TYPE qui décide, pas l'URL. Un filtre
  sur `/mcp` aurait raté l'app anonyme des sous-domaines de projet (ADR 0032), qui
  sert le même endpoint depuis un autre host, et `/api/*` qui sert le même JSON.

Le corps n'est jamais lu ni ré-encodé : cette couche ne touche que des en-têtes.
"""
from __future__ import annotations

# Les deux seuls types que ce serveur produit en UTF-8 par construction (Starlette
# encode en `charset = "utf-8"`, le SDK MCP sérialise en UTF-8 via pydantic).
_A_COMPLETER = frozenset({"text/event-stream", "application/json"})

_SUFFIXE = b"; charset=utf-8"


def completer_content_type(valeur: bytes) -> bytes:
    """Rend `valeur` complétée d'un `charset=utf-8`, ou `valeur` TELLE QUELLE.

    Rendre l'objet d'origine (et pas une copie égale) est le signal « rien à faire »
    lu par `_completer_entetes` : l'identité (`is`) évite de recopier la liste
    d'en-têtes sur le chemin nominal.
    """
    texte = valeur.decode("latin-1")
    if "charset=" in texte.lower():
        return valeur                       # déjà étiqueté — on n'y touche pas
    if texte.split(";", 1)[0].strip().lower() not in _A_COMPLETER:
        return valeur                       # binaire ou type hors allowlist
    return valeur + _SUFFIXE


def _completer_entetes(entetes) -> list | None:
    """Rend la liste d'en-têtes complétée, ou `None` si rien ne change.

    Seul le PREMIER `content-type` est considéré : c'est celui que lit un client, et
    une réponse qui en porterait deux serait malformée — ce n'est pas à cette couche
    de la réparer.
    """
    for i, (cle, valeur) in enumerate(entetes):
        if cle.lower() != b"content-type":
            continue
        complet = completer_content_type(valeur)
        if complet is valeur:
            return None
        sortie = list(entetes)
        sortie[i] = (cle, complet)
        return sortie
    return None


class ResponseCharset:
    """Complète le `content-type` de chaque réponse HTTP servie sous cette couche."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            # lifespan / websocket : pas d'en-tête de réponse à compléter, et pas de
            # wrapper à payer sur le chemin.
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message.get("type") == "http.response.start":
                entetes = _completer_entetes(message.get("headers") or ())
                if entetes is not None:
                    message = {**message, "headers": entetes}
            await send(message)

        await self.app(scope, receive, _send)
