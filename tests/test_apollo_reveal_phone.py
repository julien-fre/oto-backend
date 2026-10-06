"""`apollo_reveal_phone` — le seul geste d'Apollo qui ne rend pas son résultat.

Le client demandait `reveal_phone_number` et `webhook_url` : Apollo ne rend PAS
les mobiles dans la réponse, il les POSTe à une URL quelques minutes plus tard.
Ce que ces tests figent :

1. **Le reveal ne part JAMAIS sur la clé plateforme.** Apollo facture ~9 crédits
   un appel qui rend un mobile là où un match nu en coûte 1, pendant que
   `record_platform_usage` débite 1 unité : servir le reveal sur la clé commune
   ferait mentir `platform_quota`, le seul chiffre sur lequel un worker batch
   s'arrête avant le mur (oto-backend#710). Le refus s'exerce sur le GESTE RÉEL,
   et on vérifie qu'AUCUNE requête ne part (`docs/conventions.md` : « un cran ne
   se relit pas, il s'exécute »).
2. **`request_id` est servi en CHAÎNE.** C'est un entier signé 64 bits (~7,2e17),
   au-delà de la précision d'un nombre JavaScript : rendu en nombre il revient
   faux d'une unité ou deux, sans que rien ne le dise, et un id faux ne sonde
   rien.
3. **L'URL de livraison est la NÔTRE** — générée par `apollo_receiver`, jamais
   fournie par l'appelant ; une `webhook_url` héritée est ignorée
   (`tests/test_apollo_receveur.py` éprouve le receveur lui-même).
4. **Pas de `next_step` qui promette un outil inutilisable** : si Apollo accepte
   sans rendre d'id, la réponse le DIT au lieu d'annoncer un sondage impossible.

Mock la CLASSE client (jamais `requests`) — cf. `tests/test_apollo_location_filters.py`.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import MagicMock

import pytest


_WEBHOOK = "https://hooks.acme.test/apollo"
#: L'URL que `apollo_receiver.commander` rend dans ce montage — la seule qu'Apollo
#: doit jamais recevoir.
_RECEVEUR = "https://mcp.acme.test/api/receivers/apollo/phones/jeton"

# `None` est une VALEUR DE RETOUR à part entière ici (le client oto-core traduit
# le 404 d'Apollo en None) : le défaut du montage ne peut donc pas s'écrire
# `if x is None`, sinon le cas le plus intéressant devient intestable.
_DEFAUT = object()


def _mount(monkeypatch, *, byo: bool = True, is_platform=None, quota=None,
           match_return=_DEFAUT, poll_return=_DEFAUT, recu=None):
    """Monte apollo.py sur un FastMCP nu. `byo=False` simule l'absence de
    credential propre (donc le palier plateforme) en faisant lever
    `resolve_credential` comme le fait la vraie résolution.

    ⚠️ `is_platform` est DÉCOUPLÉ de `byo`, et sans ça un test ne prouve rien :
    avec le défaut (`is_platform = not byo`), un org qui a sa propre clé voit
    `resolve_api_key` rendre « pas plateforme », donc « aucun crédit plateforme
    débité » serait vrai même si l'outil ne basculait PAS sur la clé propre. Le
    cas qui mord est celui où les deux sont disponibles : c'est là que le choix
    de la clé se lit."""
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from mcp.types import ErrorData, INVALID_PARAMS
    from oto_mcp import access
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import apollo as apollo_tool

    client = MagicMock()
    client.match_person.return_value = (
        {"person": {"id": "p1"}, "request_id": 718432950164203900}
        if match_return is _DEFAUT else match_return)
    client.poll_webhook_result.return_value = (
        {"done": False, "retry_after_seconds": 10}
        if poll_return is _DEFAUT else poll_return)
    usage: list[str] = []

    def _resolve(provider, want="auto", *a, **k):
        if not byo:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message="No `apollo` credential configured for you"))
        return MagicMock(key="k-byo")

    monkeypatch.setattr(access, "resolve_credential", _resolve)
    plateforme = (not byo) if is_platform is None else is_platform
    monkeypatch.setattr(access, "resolve_api_key", lambda *a, **k: ("k", plateforme))
    monkeypatch.setattr(access, "record_platform_usage",
                        lambda p, n=1: usage.append(p))
    monkeypatch.setattr(access, "platform_quota_hint", lambda p: quota)
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)

    # Le receveur : sa base est éprouvée ailleurs (`test_apollo_receveur.py`) ; ici
    # on relève ce que l'outil lui DEMANDE. `recu` = ce qu'Apollo nous aurait livré
    # (None : rien d'arrivé, le sondage d'Apollo prend le relais).
    from oto_mcp import apollo_receiver
    client.receveur = []
    monkeypatch.setattr(apollo_receiver, "commander",
                        lambda cle: (client.receveur.append(("commande",)) or
                                 ("jeton", _RECEVEUR)))
    monkeypatch.setattr(apollo_receiver, "lier",
                        lambda j, rid: client.receveur.append(("lier", j, str(rid))))
    monkeypatch.setattr(apollo_receiver, "abandonner",
                        lambda j: client.receveur.append(("abandon", j)))
    monkeypatch.setattr(apollo_receiver, "resultat_recu", lambda rid, cle: recu)

    m = FastMCP("t")
    apollo_tool.register(m)
    return m, client, usage


def _tool(m, name):
    return asyncio.run(m.get_tool(name)).fn


# --------------------------------------------------------------------------- #
# 1. Le cran : jamais sur la clé plateforme
# --------------------------------------------------------------------------- #

def test_reveal_is_refused_without_your_own_apollo_key(monkeypatch):
    from oto_mcp.mcp_errors import McpError

    m, client, usage = _mount(monkeypatch, byo=False)
    with pytest.raises(McpError):
        _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    assert not client.match_person.called, "aucun appel ne doit partir"
    assert usage == [], "aucun crédit plateforme ne doit être débité"


def test_polling_too_is_refused_without_your_own_key(monkeypatch):
    """Le sondage rend « les résultats de ton ÉQUIPE » : sur la clé plateforme
    mutualisée, un `request_id` deviné rendrait le lead de quelqu'un d'autre."""
    from oto_mcp.mcp_errors import McpError

    m, client, _ = _mount(monkeypatch, byo=False)
    with pytest.raises(McpError):
        _tool(m, "apollo_reveal_phone_result")(request_id="718432950164203900")
    assert not client.poll_webhook_result.called


def test_the_refusal_names_the_cost_not_the_data_boundary(monkeypatch):
    """Le reste du module est byo-only pour une frontière de DONNÉES ; celui-ci
    l'est pour le COÛT. Servir le mauvais motif envoie chercher au mauvais
    endroit — c'est la phrase qu'on éprouve ici, donc on la cite (cf.
    `docs/conventions.md` : la phrase ne se cite que quand c'est elle l'objet)."""
    from oto_mcp.mcp_errors import McpError

    m, _, _ = _mount(monkeypatch, byo=False)
    with pytest.raises(McpError) as e:
        _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    msg = e.value.error.message or ""
    assert "9 credits" in msg
    assert "sequences" not in msg, "le motif « tes propres données » ne vaut pas ici"


# --------------------------------------------------------------------------- #
# 2. Le geste nominal
# --------------------------------------------------------------------------- #

def test_reveal_forwards_the_flag_and_OUR_receiver(monkeypatch):
    """Apollo reçoit l'URL générée par oto — même quand un appelant d'avant ce lot
    passe encore la sienne."""
    m, client, usage = _mount(monkeypatch)
    _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    kw = client.match_person.call_args.kwargs
    assert kw["reveal_phone_number"] is True
    assert kw["webhook_url"] == _RECEVEUR
    assert kw["person_id"] == "p1"
    assert usage == [], "clé BYO : rien à débiter du pot plateforme"


def test_request_id_is_served_as_a_string(monkeypatch):
    """718432950164203900 > 2^53 : rendu en nombre, il revient faux."""
    m, _, _ = _mount(monkeypatch)
    out = _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    assert out["request_id"] == "718432950164203900"
    assert isinstance(out["request_id"], str)
    assert "apollo_reveal_phone_result" in out["next_step"]


def test_no_request_id_says_so_instead_of_promising_a_poll(monkeypatch):
    """Personne TROUVÉE, mais pas d'id : le reveal est parti, il n'est
    simplement pas sondable."""
    m, _, _ = _mount(monkeypatch, match_return={"person": {"id": "p1"}})
    out = _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    assert "request_id" not in out
    assert "Nothing to poll" in out["next_step"]
    assert out.get("matched") is not False, "une personne EST revenue"


@pytest.mark.parametrize("reponse, cas", [
    (None, "404 Apollo → le client rend None"),
    ({}, "corps vide"),
    ({"person": None}, "200 sans personne"),
])
def test_apollo_finding_nobody_is_not_an_accepted_reveal(monkeypatch, reponse, cas):
    """Le mensonge le plus cher est celui qui RASSURE : annoncer un reveal en vol
    quand Apollo n'a rien trouvé fait attendre à l'agent un POST qui ne partira
    jamais, et lui fait rendre le lead pour traité sans réessayer."""
    m, _, _ = _mount(monkeypatch, match_return=reponse)
    out = _tool(m, "apollo_reveal_phone")(webhook_url=_WEBHOOK, person_id="p1")
    assert out["matched"] is False, cas
    assert "request_id" not in out
    assert "No Apollo match" in out["next_step"]
    assert "accepted" not in out["next_step"], (
        "rien n'a été accepté : ni commande, ni crédit, ni webhook")


# --------------------------------------------------------------------------- #
# 4. Le sondage
# --------------------------------------------------------------------------- #

def test_pending_poll_is_not_a_failure(monkeypatch):
    m, _, _ = _mount(monkeypatch, poll_return={"done": False,
                                               "retry_after_seconds": 10})
    out = _tool(m, "apollo_reveal_phone_result")(request_id="718432950164203900")
    assert out["done"] is False
    assert out["retry_after_seconds"] == 10
    assert "10s" in out["next_step"]


def test_a_finished_poll_hands_back_the_numbers(monkeypatch):
    """⚠️ L'enveloppe est celle qu'Apollo PUBLIE, pas une forme commode : les
    numéros vivent sous `webhook_result`, un cran plus bas que le raccourci
    qu'on aurait écrit spontanément. Un fixture inventé ici rendrait ce test
    vert sur une description fausse — c'est exactement ce qui était arrivé."""
    payload = {
        "request_id": 718432950164203900,
        "webhook_status": "delivered",
        "failure_reason": None,
        "webhook_result": {"people": [
            {"id": "p1", "phone_numbers": [{"sanitized_number": "+33600000000",
                                            "type_cd": "mobile"}]}]},
    }
    m, _, usage = _mount(monkeypatch, poll_return={"done": True, "result": payload})
    out = _tool(m, "apollo_reveal_phone_result")(request_id="718432950164203900")
    # ⚠️ L'enveloppe ressort TELLE QUELLE à une exception près, et c'en est une
    # volontaire : l'identifiant ré-échoté par Apollo sort en CHAÎNE (cf.
    # `test_the_poll_does_not_echo_a_damaged_id`). Ce test figeait l'égalité
    # stricte avec le payload d'Apollo et a donc rougi quand la sérialisation est
    # descendue d'un niveau — c'est ce qu'on lui demande : dire qu'un contrat
    # servi a changé, plutôt que de suivre en silence.
    assert out == {"done": True,
                   "result": {**payload, "request_id": "718432950164203900"}}
    assert usage == [], "un sondage coûte 0 crédit — rien à débiter"


def test_the_poll_description_names_the_path_the_numbers_ACTUALLY_take(monkeypatch):
    """Le chemin annoncé se lit sur le corps qu'Apollo publie, pas sur celui
    qu'on aurait aimé. Servir `result.people[]` là où il y a
    `result.webhook_result.people[]` fait lire « aucun numéro » à chaque sondage
    réussi — et une description est relue comme une instruction."""
    corps = {"webhook_result": {"people": [
        {"phone_numbers": [{"sanitized_number": "+33600000000"}]}]}}
    m, _, _ = _mount(monkeypatch, poll_return={"done": True, "result": corps})
    out = _tool(m, "apollo_reveal_phone_result")(request_id="1")
    doc = asyncio.run(m.get_tool("apollo_reveal_phone_result")).description or ""

    # On PARCOURT le chemin que la description dicte, sur la réponse servie :
    # relire les deux et les trouver d'accord ne prouve rien, les suivre si.
    chemin = doc.split("the numbers are at ")[1].split("(")[0].strip().strip("`")
    noeud = out
    for cle in chemin.split("."):
        noeud = noeud[cle.removesuffix("[]")]
        if cle.endswith("[]"):
            noeud = noeud[0]
    assert noeud, f"le chemin servi (`{chemin}`) ne mène nulle part"


# --------------------------------------------------------------------------- #
# 5. Ce que les descriptions promettent existe vraiment
# --------------------------------------------------------------------------- #

def test_the_reveal_only_names_tools_that_exist(monkeypatch):
    """Une description d'outil est relue comme une instruction : un agent qui a
    une intention et pas de destination s'en fabrique une (#613/#632)."""
    m, _, _ = _mount(monkeypatch)
    doc = asyncio.run(m.get_tool("apollo_reveal_phone")).description or ""
    for cite in ("apollo_reveal_phone_result", "apollo_search_people",
                 "apollo_match_person"):
        assert cite in doc
        assert asyncio.run(m.get_tool(cite)) is not None


def test_match_person_says_the_phone_lives_elsewhere(monkeypatch):
    """Sans ça, un agent qui cherche un mobile le demande ici, ne le trouve pas,
    et conclut que la plateforme ne sait pas faire — le signal du client."""
    m, _, _ = _mount(monkeypatch)
    doc = asyncio.run(m.get_tool("apollo_match_person")).description or ""
    assert "apollo_reveal_phone" in doc


def test_match_person_forwards_reveal_personal_emails(monkeypatch):
    m, client, _ = _mount(monkeypatch)
    _tool(m, "apollo_match_person")(person_id="p1", reveal_personal_emails=True)
    assert client.match_person.call_args.kwargs["reveal_personal_emails"] is True


# --------------------------------------------------------------------------- #
# 6. L'autre reveal : les emails personnels, MÊME règle
# --------------------------------------------------------------------------- #

def test_personal_emails_reveal_is_refused_without_your_own_key(monkeypatch):
    """Le reveal d'emails personnels est un reveal : Apollo le facture EN PLUS du
    match pendant que `record_platform_usage` débite une unité — le prix d'un
    match nu. Le laisser partir sur la clé commune fait mentir `platform_quota`,
    exactement comme le téléphone ; que l'écart soit mesuré (~9×) ou seulement
    inconnu ne change pas le sens du défaut."""
    from oto_mcp.mcp_errors import McpError

    m, client, usage = _mount(monkeypatch, byo=False, is_platform=True)
    with pytest.raises(McpError):
        _tool(m, "apollo_match_person")(person_id="p1", reveal_personal_emails=True)
    assert not client.match_person.called, "aucun appel ne doit partir"
    assert usage == [], "aucun crédit plateforme ne doit être débité"


def test_the_personal_emails_refusal_names_the_cost_not_the_data_boundary(monkeypatch):
    """Même exigence que pour le téléphone : servir « tes propres données » à qui
    bute sur un mur de COÛT l'envoie chercher au mauvais endroit."""
    from oto_mcp.mcp_errors import McpError

    m, _, _ = _mount(monkeypatch, byo=False, is_platform=True)
    with pytest.raises(McpError) as e:
        _tool(m, "apollo_match_person")(person_id="p1", reveal_personal_emails=True)
    msg = e.value.error.message or ""
    assert "reveal_personal_emails" in msg
    assert re.search(r"bills|credit", msg), "le motif est le coût, il doit se dire"
    assert "sequences" not in msg, "le motif « tes propres données » ne vaut pas ici"


def test_a_personal_emails_reveal_never_rides_the_shared_meter(monkeypatch):
    """Le geste, pas l'outil. Sur un org qui a les DEUX (sa clé ET le palier
    plateforme ouvert), demander les emails personnels bascule sur la clé propre :
    rien n'est débité du pot commun, et la réponse ne porte pas un `platform_quota`
    qui décrirait une clé qu'on n'a pas utilisée."""
    m, client, usage = _mount(
        monkeypatch, byo=True, is_platform=True, quota={"remaining": 42},
        match_return={"person": {"id": "p1"}})
    out = _tool(m, "apollo_match_person")(person_id="p1",
                                          reveal_personal_emails=True)
    assert client.match_person.call_args.kwargs["reveal_personal_emails"] is True
    assert usage == [], "clé propre : rien à débiter du pot plateforme"
    assert "platform_quota" not in out


def test_a_plain_match_still_rides_the_shared_meter(monkeypatch):
    """Contrôle positif du test ci-dessus — sans lui, « rien n'a été débité » ne
    distingue pas « la bascule a eu lieu » de « le montage ne débite jamais »."""
    m, _, usage = _mount(
        monkeypatch, byo=True, is_platform=True, quota={"remaining": 42},
        match_return={"person": {"id": "p1"}})
    out = _tool(m, "apollo_match_person")(person_id="p1")
    assert usage == ["apollo"], "un match nu reste sur le palier plateforme"
    assert out["platform_quota"] == {"remaining": 42}


# --------------------------------------------------------------------------- #
# 7. Le refus « pas de match » n'affirme rien sur le coût
# --------------------------------------------------------------------------- #

def _sans_match(monkeypatch) -> str:
    m, _, _ = _mount(monkeypatch, match_return=None)
    return _tool(m, "apollo_reveal_phone")(
        webhook_url=_WEBHOOK, person_id="p1")["next_step"]


@pytest.mark.parametrize("formule", [
    "no credit spent", "nothing was ordered", "no credit", "no charge",
    "free of charge", "at no cost", "nothing was spent", "nothing was billed",
])
def test_the_no_match_message_never_claims_the_call_was_free(monkeypatch, formule):
    """⚠️ Le code ne sait PAS ce que l'amont a facturé, et Apollo facture l'appel,
    pas le résultat : il facture même la coquille vide qu'il fabrique (~12 crédits
    pour zéro donnée, oto-core `44acc08`). Une branche d'échec qui affirme la
    gratuité est une MESURE qu'on n'a pas prise — et elle est servie à un agent
    qui la relit comme une instruction.

    La classe refusée est l'absolu non mesurable sur la dépense, pas un mot : les
    formules ci-dessous en sont les formes rencontrées."""
    assert formule not in _sans_match(monkeypatch).lower()


def test_the_no_match_message_prices_the_second_attempt(monkeypatch):
    """Le symétrique, et il compte autant : bannir « gratuit » sans dire que ce
    n'est pas gratuit laisserait un message muet sur le coût, qui enchaîne quand
    même sur « réessaie ». Un second appel présenté comme offert est le défaut
    qu'on corrige ici — il doit donc être CHIFFRÉ."""
    msg = _sans_match(monkeypatch)
    assert re.search(r"\bbill|\bcharge|\bpaid\b|\bcost", msg, re.I), (
        "le refus doit dire que l'appel n'était pas gratuit")
    assert re.search(r"free", msg, re.I), (
        "et le dire en visant la lecture fausse (« do NOT read that as free »)")


# --------------------------------------------------------------------------- #
# 8. Toute capacité de reveal ANNONCE sa réserve de clé, dans le texte servi
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("outil, marqueur", [
    ("apollo_reveal_phone", "own Apollo key"),
    ("apollo_match_person", "reveal_personal_emails"),
])
def test_a_reveal_says_in_its_served_text_that_it_needs_your_own_key(
        monkeypatch, outil, marqueur):
    """Un outil offert sera appelé : la réserve de clé doit se lire AVANT l'appel,
    dans ce que le serveur sert — pas seulement dans le refus qui arrive après.
    Et elle doit porter sa RAISON : « ta propre clé » sans le coût se lit comme une
    tracasserie d'installation, et un agent qui n'en comprend pas le motif cherche
    à la contourner (#613/#632).

    On ne fige aucune phrase : on exige la classe (réserve de clé + motif de coût)
    dans le voisinage de la capacité nommée."""
    m, _, _ = _mount(monkeypatch)
    doc = asyncio.run(m.get_tool(outil)).description or ""
    assert marqueur in doc, f"{outil} ne nomme même pas la capacité"
    i = doc.index(marqueur)
    voisinage = doc[max(0, i - 250):i + 400]
    assert re.search(r"your own Apollo key", voisinage, re.I), (
        f"{outil} : la réserve de clé ne se lit pas près de « {marqueur} »")
    assert re.search(r"credit|bill|charge|cost", voisinage, re.I), (
        f"{outil} : la réserve est annoncée sans son motif (le coût)")
