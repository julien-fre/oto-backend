"""La garde ASGI de déconnexion client (#352) : compléter la réponse, sans rien avaler d'autre.

Un POST `/mcp` dont le client est parti laissait une réponse ASGI **incomplète** —
uvicorn fermait alors le transport, et Caddy, qui tenait cette connexion pour
réutilisable, rendait des 502 sur elle ET sur les requêtes voisines de son pool
keep-alive. La garde complète la réponse à la place du client parti.

Ce fichier tient les DEUX moitiés du contrat, et la seconde est la condition de la
première : une garde qui avale des exceptions n'est acceptable que si l'on prouve
qu'elle n'avale QUE celle-là.

- ce qui doit être RATTRAPÉ : la classe « déconnexion client » (BrokenResourceError /
  ClosedResourceError, nue ou dans un groupe d'exceptions anyio), qu'elle survienne
  avant les en-têtes, entre les en-têtes et la fin du corps, ou après une réponse déjà
  complète ; et le retour normal qui laisse le corps en plan (branche SSE annulée) ;
- ce qui doit TRAVERSER : toute autre exception, intacte ; et tout ce qui n'est pas un
  POST sur `/mcp`.

Client ASGI de test : on appelle la garde directement avec un scope/receive/send et on
lit la SÉQUENCE de messages ASGI produite — c'est précisément ce que uvicorn observe
pour décider s'il garde la connexion ou la ferme.
"""
from __future__ import annotations

import anyio
import pytest

from oto_mcp.client_disconnect_guard import ClientDisconnectGuard


# --- client ASGI de test -----------------------------------------------------

def _scope(method: str = "POST", path: str = "/mcp") -> dict:
    return {"type": "http", "method": method, "path": path, "headers": []}


async def _receive():                                   # pragma: no cover - jamais lu ici
    return {"type": "http.request", "body": b"", "more_body": False}


async def _call(app, scope=None) -> list[dict]:
    """Joue une requête à travers la garde et rend les messages ASGI émis."""
    envoyes: list[dict] = []

    async def send(message):
        envoyes.append(message)

    await ClientDisconnectGuard(app)(scope or _scope(), _receive, send)
    return envoyes


def _statuts(msgs) -> list[int]:
    return [m["status"] for m in msgs if m["type"] == "http.response.start"]


def _complete(msgs) -> bool:
    """Vrai si la séquence constitue une réponse complète aux yeux d'uvicorn :
    des en-têtes, puis un dernier morceau de corps sans `more_body`."""
    return (any(m["type"] == "http.response.start" for m in msgs)
            and any(m["type"] == "http.response.body" and not m.get("more_body")
                    for m in msgs))


async def _start(send, status=200):
    await send({"type": "http.response.start", "status": status, "headers": []})


# --- ce qui doit être rattrapé ----------------------------------------------

@pytest.mark.asyncio
async def test_rien_envoye_puis_deconnexion_la_reponse_est_un_202_vide():
    async def app(scope, receive, send):
        raise anyio.BrokenResourceError()

    msgs = await _call(app)
    assert _statuts(msgs) == [202]
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_entetes_parties_puis_deconnexion_le_corps_est_termine():
    async def app(scope, receive, send):
        await _start(send)
        await send({"type": "http.response.body", "body": b"pa", "more_body": True})
        raise anyio.ClosedResourceError()

    msgs = await _call(app)
    # Pas de second `start` (les en-têtes sont partis, le statut est figé) : juste la
    # fin de corps qui manquait.
    assert _statuts(msgs) == [200]
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_reponse_deja_complete_puis_deconnexion_rien_de_plus_nest_emis():
    """Le cas mesuré du SDK MCP : le 202 part, PUIS `writer.send` lève. La réponse est
    déjà complète — la garde n'a plus qu'à ne pas laisser l'exception fermer le
    transport, et surtout à ne RIEN ajouter derrière."""
    async def app(scope, receive, send):
        await _start(send, 202)
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        raise anyio.BrokenResourceError()

    msgs = await _call(app)
    assert [m["type"] for m in msgs] == ["http.response.start", "http.response.body"]
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_la_sequence_reelle_du_sdk_mcp_de_bout_en_bout():
    """Le chemin EXACT de `mcp/server/streamable_http.py`, rejoué (vérifié sur upstream
    `main`, `v1.x` et notre 1.27.2 — le code est identique partout, et aucune version
    publiée ne le garde) :

    1. branche « message qui n'est pas une JSONRPCRequest » : le 202 part en entier ;
    2. `await writer.send(session_message)` lève `BrokenResourceError` (session terminée) ;
    3. le `except Exception as err` global tente une 2ᵉ réponse, un 500 — uvicorn refuse
       des en-têtes sur une réponse déjà complète et lève le `RuntimeError` « after
       response already completed », qui s'échappe de l'app.

    Sans la garde, uvicorn journalise « Exception in ASGI application » et FERME le
    transport : Caddy tenait cette connexion pour réutilisable → 502 sur elle et sur ses
    voisines. Avec la garde : le 202 reste la réponse, elle est complète, rien ne fuit."""
    async def app(scope, receive, send):
        await _start(send, 202)                                          # (1)
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        try:
            raise anyio.BrokenResourceError()                            # (2)
        except anyio.BrokenResourceError:
            # (3) le SDK renvoie une réponse par-dessus une réponse complète ; c'est
            # uvicorn qui refuse — on rejoue son refus mot pour mot.
            raise RuntimeError("Unexpected ASGI message 'http.response.start' sent, "
                               "after response already completed.")

    msgs = await _call(app)
    assert _statuts(msgs) == [202]              # le 500 du SDK n'a jamais atteint le fil
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_groupe_dexceptions_anyio_est_reconnu():
    """anyio 4 remonte les erreurs d'un task group dans un groupe — et le SDK MCP lance
    sa réponse SSE dans un task group."""
    async def app(scope, receive, send):
        await _start(send)
        raise BaseExceptionGroup("tg", [anyio.BrokenResourceError()])

    msgs = await _call(app)
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_retour_normal_avec_corps_en_plan_la_reponse_est_terminee():
    """Branche SSE : le task group annule la tâche de réponse, le SDK avale, l'app
    RETOURNE. Aucune exception à attraper, et pourtant la réponse est incomplète —
    uvicorn fermerait le transport (« returned without completing response »)."""
    async def app(scope, receive, send):
        await _start(send)
        await send({"type": "http.response.body", "body": b"event: x\n", "more_body": True})

    msgs = await _call(app)
    assert _complete(msgs)


@pytest.mark.asyncio
async def test_reponse_deja_complete_nest_pas_retouchee():
    async def app(scope, receive, send):
        await _start(send)
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    msgs = await _call(app)
    assert [m["type"] for m in msgs] == ["http.response.start", "http.response.body"]
    assert msgs[1]["body"] == b"ok"


# --- ce qui doit TRAVERSER (la condition d'acceptabilité de la garde) --------

@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [
    KeyError("orgs"),
    ValueError("vrai bug"),
    RuntimeError("le coffre a rendu None"),
])
async def test_une_vraie_erreur_traverse_intacte(boom):
    """La garde ne doit JAMAIS transformer un bug backend en 202 silencieux : uvicorn
    doit continuer de voir l'exception (500 + event Sentry)."""
    async def app(scope, receive, send):
        raise boom

    with pytest.raises(type(boom)) as pris:
        await _call(app)
    assert pris.value is boom


@pytest.mark.asyncio
async def test_une_vraie_erreur_ne_declenche_aucune_cloture():
    """Et elle ne laisse pas non plus la garde émettre une réponse derrière : c'est
    uvicorn qui décide (500), pas nous."""
    envoyes: list[dict] = []

    async def send(message):
        envoyes.append(message)

    async def app(scope, receive, send_):
        await _start(send_)
        raise KeyError("boum")

    with pytest.raises(KeyError):
        await ClientDisconnectGuard(app)(_scope(), _receive, send)
    assert not _complete(envoyes)          # aucune fin de corps ajoutée par la garde


@pytest.mark.asyncio
async def test_un_groupe_mixte_traverse():
    """Un groupe qui contient AUSSI un vrai bug n'est pas une déconnexion client."""
    async def app(scope, receive, send):
        raise BaseExceptionGroup("tg", [anyio.BrokenResourceError(), KeyError("orgs")])

    with pytest.raises(BaseExceptionGroup):
        await _call(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [
    _scope(method="GET"),
    _scope(method="DELETE"),
    _scope(path="/api/me"),
    _scope(method="GET", path="/api/me/projects"),
    # Se termine par `/mcp` sans être le endpoint MCP — le prédicat compare le chemin
    # ENTIER, il ne suffixe pas.
    _scope(path="/.well-known/oauth-protected-resource/mcp"),
])
async def test_hors_perimetre_pass_through_integral(scope):
    """Périmètre = POST sur le endpoint MCP, et rien d'autre. La face REST et les
    autres méthodes ne voient même pas la garde."""
    async def app(scope_, receive, send):
        raise anyio.BrokenResourceError()

    with pytest.raises(anyio.BrokenResourceError):
        await _call(app, scope)


@pytest.mark.asyncio
async def test_le_endpoint_mcp_dun_sous_domaine_de_projet_est_couvert():
    """`<slug>.share.oto.cx/mcp` (ADR 0032) sert le MÊME transport — même race."""
    async def app(scope, receive, send):
        raise anyio.BrokenResourceError()

    msgs = await _call(app, _scope(path="/mcp/"))
    assert _statuts(msgs) == [202]


@pytest.mark.asyncio
async def test_websocket_et_lifespan_passent_sans_toucher_a_rien():
    vus = []

    async def app(scope, receive, send):
        vus.append(scope["type"])

    async def send(message):                            # pragma: no cover - jamais appelé
        raise AssertionError("la garde ne doit rien émettre hors HTTP")

    for t in ("lifespan", "websocket"):
        await ClientDisconnectGuard(app)({"type": t}, _receive, send)
    assert vus == ["lifespan", "websocket"]


# --- l'assemblage : la garde est bien POSÉE, et à la bonne place -------------

def test_lapp_racine_servie_par_uvicorn_est_gardee():
    """Une garde qu'on peut retirer sans faire rougir la suite n'est pas une garde.
    `build_root_app` est le point d'assemblage exact ; on vérifie que la garde y est la
    couche la plus EXTERNE (elle doit voir ce que voit uvicorn, dispatch par Host
    compris) — et que l'étiquetage de version (oto#33) puis celui du charset (#472)
    s'intercalent SOUS elle, au-dessus du dispatch, donc sur les deux instances à la
    fois."""
    from oto_mcp.response_charset import ResponseCharset
    from oto_mcp.server import build_root_app
    from oto_mcp.subdomain_project import HostDispatch
    from oto_mcp.version_header import VersionHeader

    racine = build_root_app(object(), object())
    assert isinstance(racine, ClientDisconnectGuard)
    assert isinstance(racine.app, VersionHeader)
    assert isinstance(racine.app.app, ResponseCharset)
    assert isinstance(racine.app.app.app, HostDispatch)


# --- le warning de visibilité (2e moitié de #352) ----------------------------

def test_visibilite_client_parti_en_debug_le_reste_en_warning(caplog):
    """337 « Failed to apply tool visibility » en 2 h le 15/08, tous de la même cause :
    le client avait fermé son POST avant qu'on pousse `tools/list_changed`. Attendu →
    debug. Une vraie panne de visibilité reste un warning (la session tourne alors avec
    une toolbox plus large que prévu — ça, il faut le voir)."""
    from oto_mcp.session_visibility import _log_visibility_failure

    with caplog.at_level("DEBUG", logger="oto_mcp.session_visibility"):
        _log_visibility_failure("apply", "sub-1", anyio.BrokenResourceError())
        _log_visibility_failure("apply", "sub-2", anyio.ClosedResourceError())
        _log_visibility_failure("apply", "sub-3", KeyError("registre vide"))

    niveaux = {r.levelname for r in caplog.records if "sub-1" in r.getMessage()}
    assert niveaux == {"DEBUG"}
    niveaux = {r.levelname for r in caplog.records if "sub-2" in r.getMessage()}
    assert niveaux == {"DEBUG"}
    niveaux = {r.levelname for r in caplog.records if "sub-3" in r.getMessage()}
    assert niveaux == {"WARNING"}
