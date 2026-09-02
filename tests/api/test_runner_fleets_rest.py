"""Les flottes SUR LA ROUTE SERVIE — la réponse est sérialisée, pas inspectée.

⚠️ **Ce fichier existe à cause de ce que son absence a laissé passer.** Le lot qui a
créé cette capacité ne la faisait jamais passer par HTTP : ses tests lisaient des
champs de modèle et attrapaient des refus levés en appelant le handler à la main.
Deux défauts en sont sortis, de la MÊME famille — une valeur que PostgreSQL rend en
`Decimal`, que rien ne normalise, et qui fait un 500 à la sérialisation :

1. `max_cost_usd NUMERIC` — trouvé en relecture ;
2. `SUM(...)::bigint`, qui rend `numeric` en PostgreSQL — **passé sous le correctif
   du premier**, parce que la garde ajoutée balayait des NOMS (`usd|cost|euro`) et
   que le champ s'appelle `usage_tokens`.

> **Le correctif visait l'axe sur lequel le défaut s'était présenté (un nom), pas
> l'axe sur lequel il vit (un type qui ne se sérialise pas).** Un balayage de noms
> élargi à quatre modèles donnait l'impression d'avoir appris la leçon ; la seule
> occurrence vivante est passée dessous.

**La seule garde qui ne peut pas se tromper d'axe : sérialiser une vraie réponse.**
`TestClient` rend le JSON par la même pile que la production — si un type ne passe
pas, le test rougit, quel que soit le nom du champ.

Les refus sont rejoués ici aussi, parce qu'une déclaration `Capability.errors` sans
rejeu est décorative : elle promet un statut que le serveur ne rend peut-être pas.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ROUTE = "/api/me/runner/fleets"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@fleets.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_fleets_rest_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(),
                                                              mcp_instance=None)))


@pytest.fixture(scope="module")
def org(live):
    from oto_mcp import db, org_store
    membre = "usr_fleets_membre"
    db.upsert_user(membre, email=f"{membre}@fleets.invalid", name=membre)
    oid = org_store.create_org("Org des flottes", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    return {"id": oid, "membre": membre}


@pytest.fixture(scope="module")
def flotte(client, org):
    """Une flotte déclarée PAR LA ROUTE — pas insérée en base à la main."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "passage d'essai", "procedure": "enrichissement",
        "tools": ["oto_kb"], "namespace": "un-tableau", "row_filter": {"lot": "a"},
        "provider": "openai", "model": "un-modele",
        "max_rows": 10, "max_tokens": 1_000_000, "max_tokens_per_row": 50_000})
    assert r.status_code == 200, r.text
    return r.json()["fleet"]


# ── la réponse se SÉRIALISE — la garde qui ne peut pas se tromper d'axe ───────

def test_l_etat_d_une_flotte_vierge_se_serialise(client, org, flotte):
    """Le défaut vivait ICI, et il touchait 100 % des appels.

    `COALESCE(SUM(...), 0)` rend un `Decimal` **même sans un seul job** : la lecture
    phare de la capacité — celle qui devait rendre un opérateur autonome — répondait
    500 sur toute flotte, y compris vierge. Aucun test de modèle ne pouvait le voir.
    """
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "state", "fleet_id": flotte["id"]})
    assert r.status_code == 200, r.text
    etat = r.json()["state"]
    assert etat["no_jobs_attached"] is True
    assert etat["jobs_total"] == 0
    # les compteurs sont des ENTIERS servis, pas des chaînes ni des décimaux
    assert isinstance(etat["usage_tokens"], int)
    assert not isinstance(etat["usage_tokens"], bool)


def test_l_etat_se_serialise_aussi_avec_des_travaux_rattaches(client, org, flotte):
    """Le chemin où `SUM` rend vraiment une somme — l'autre moitié du défaut."""
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        for jetons in (1200, 3400):
            c.execute(
                "INSERT INTO runner_jobs (org_id, kind, fleet_id, status, result) "
                "VALUES (%s, 'start', %s, 'done', %s::jsonb)",
                (org["id"], flotte["id"], '{"usage_tokens": %d}' % jetons))
        c.commit()
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "state", "fleet_id": flotte["id"]})
    assert r.status_code == 200, r.text
    etat = r.json()["state"]
    assert etat["jobs_total"] == 2 and etat["no_jobs_attached"] is False
    assert etat["usage_tokens"] == 4600
    assert isinstance(etat["usage_tokens"], int)


def test_la_flotte_elle_meme_se_serialise_sur_list_et_get(client, org, flotte):
    """`list` ramène la flotte dans le lot des autres : un type qui ne passe pas y
    rend TOUTE la liste illisible, pas seulement la flotte fautive."""
    for corps in ({"op": "list"}, {"op": "get", "fleet_id": flotte["id"]}):
        r = client.post(ROUTE, headers=_h(org["membre"]), json=corps)
        assert r.status_code == 200, f"{corps} → {r.text}"
    lot = client.post(ROUTE, headers=_h(org["membre"]),
                      json={"op": "list"}).json()["fleets"]
    assert any(f["id"] == flotte["id"] for f in lot)


# ── les refus DÉCLARÉS, rejoués sur la route servie ──────────────────────────

def _refus(client, org, corps: dict) -> tuple[int, str]:
    r = client.post(ROUTE, headers=_h(org["membre"]), json=corps)
    return r.status_code, (r.json() or {}).get("error", "")


def test_la_cible_figee_est_refusee_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "namespace": "ailleurs"}) == (400, "target_is_frozen")
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "row_filter": {"lot": "b"}}) == (400, "target_is_frozen")


def test_le_contexte_fige_est_refuse_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "model": "autre"}) == (400, "context_is_frozen")


def test_l_etat_ne_se_pose_pas_par_update_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "status": "stopped"}) == (400, "status_not_settable")


def test_un_champ_non_modifiable_ne_rend_pas_200_sans_effet(client, org, flotte):
    """`procedure` est le champ le plus lourd de la configuration — ce que la flotte
    EXÉCUTE. Il n'était ni refusé ni appliqué : 200, et rien ne changeait.

    ⚠️ La garde appartient au SEAM, pas au champ : tout champ d'entrée ni structurel
    ni modifiable doit aboutir ou être refusé. Écrite champ par champ, elle oublie
    exactement ceux auxquels on n'a pas pensé — et ceux qu'on ajoutera."""
    for champ, valeur in (("procedure", "UNE-AUTRE"), ("project_id", 4242)):
        statut, code = _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                            champ: valeur})
        assert (statut, code) == (400, "field_not_settable"), f"{champ} → {statut}"
    # et la procédure n'a pas bougé
    f = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "fleet_id": flotte["id"]}).json()["fleet"]
    assert f["procedure"] == "enrichissement"


def test_un_perimetre_sans_tableau_est_refuse_sur_la_route(client, org):
    assert _refus(client, org, {"op": "create", "label": "x", "procedure": "p",
                                "tools": ["oto_kb"], "row_filter": {"lot": "a"}}
                  ) == (400, "target_incomplete")


def test_create_sans_les_champs_requis_est_refuse_sur_la_route(client, org):
    assert _refus(client, org, {"op": "create", "label": "x"}
                  ) == (400, "missing_fields")


def test_une_flotte_inconnue_rend_404_sur_la_route(client, org):
    assert _refus(client, org, {"op": "get", "fleet_id": 999_999_999}
                  ) == (404, "fleet_not_found")


def test_une_flotte_d_une_autre_org_est_invisible(client, org, flotte):
    """L'isolation par org se vérifie sur la route, pas sur le handler : c'est là
    que `org_id` est résolu depuis le porteur."""
    from oto_mcp import db, org_store
    autre = "usr_fleets_etranger"
    db.upsert_user(autre, email=f"{autre}@fleets.invalid", name=autre)
    oid = org_store.create_org("Une autre org", created_by=autre)
    org_store.add_org_member(oid, autre, "org_admin")
    org_store.set_active_org(autre, oid)
    r = client.post(ROUTE, headers=_h(autre),
                    json={"op": "get", "fleet_id": flotte["id"]})
    assert (r.status_code, r.json().get("error")) == (404, "fleet_not_found")


# ── les résidus de la 3ᵉ passe : le seam vaut pour TOUTE opération ────────────

def test_create_refuse_ce_qu_il_n_applique_pas(client, org):
    """`create status="running"` rendait 200 avec une flotte `draft` et le champ
    avalé — mot pour mot le geste que le refus d'`update` prédit.

    ⚠️ La garde était écrite dans la branche `update` SEULE. **Une garde écrite
    dans une branche ne garde que cette branche** : c'est le même défaut que celui
    qu'elle corrigeait, déplacé d'un verbe."""
    base = {"op": "create", "label": "x", "procedure": "p", "tools": ["oto_kb"]}
    assert _refus(client, org, {**base, "status": "running"}
                  ) == (400, "status_not_settable")
    assert _refus(client, org, {**base, "fleet_id": 1}
                  ) == (400, "field_not_settable")


def test_une_borne_absurde_est_refusee_des_DEUX_cotes(client, org, flotte):
    """Une borne se compte, donc elle vaut au moins 1. `max_rows=-5`, `workers=0`
    passaient à la création ET à la retouche — une borne absurde acceptée est une
    panne différée, découverte au lancement plutôt qu'à la déclaration."""
    base = {"op": "create", "label": "x", "procedure": "p", "tools": ["oto_kb"]}
    for champ, valeur in (("max_rows", -5), ("workers", 0), ("max_tokens", -1),
                          ("max_tokens_per_row", 0), ("max_steps", -2),
                          ("max_consecutive_failures", 0)):
        assert _refus(client, org, {**base, champ: valeur}) == (400, "invalid_bound"), champ
        assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                    champ: valeur}) == (400, "invalid_bound"), champ


def test_update_ne_peut_pas_annuler_ce_que_create_exige(client, org, flotte):
    """`create tools=[]` était refusé et `update tools=[]` vidait l'allowlist.
    Une garde qui ne tient qu'à l'entrée laisse la sortie ouverte."""
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"], "tools": []}
                  ) == (400, "missing_fields")
    f = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "fleet_id": flotte["id"]}).json()["fleet"]
    assert f["tools"] == ["oto_kb"]


# ── LANCER et ARRÊTER : deux verbes, deux planchers, deux gardes ─────────────
#
# ⚠️ Ils ne sont PAS symétriques, et c'est tout le point. Ils entrent par la même
# porte mais n'engagent pas la même chose :
#
#   lancer   de l'argent et des effets externes IRRÉVERSIBLES — des lignes
#            écrites chez un tiers ⟹ plancher ADMIN
#   arrêter  une interruption et un travail à reprendre ⟹ tout MEMBRE, parce
#            qu'un passage qui part en vrille doit pouvoir être stoppé par la
#            première personne qui le voit
#
# ⚠️ Et aucun des deux ne pose un FAIT : `launch` arme (on a demandé), `stop`
# demande l'arrêt (la boucle ne l'a pas lu). Une intention déclarée et un fait
# constaté ne partagent jamais une colonne.

@pytest.fixture(scope="module")
def flotte_a_piloter(client, org):
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "pilotage", "procedure": "p", "tools": ["oto_kb"]})
    assert r.status_code == 200, r.text
    return r.json()["fleet"]


def test_lancer_ARME_et_ne_pretend_pas_que_ca_tourne(client, org, flotte_a_piloter):
    """⚠️ LE point : `armed`, jamais `running`.

    `running` veut dire qu'un ordonnanceur l'a PRISE et donne signe. Poser
    `running` ici ferait lire « en cours » un passage que personne n'exécute —
    et une flotte armée que nul n'a réclamée doit se DIRE, pas se confondre."""
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "launch", "fleet_id": flotte_a_piloter["id"]})
    assert r.status_code == 200, r.text
    f = r.json()["fleet"]
    assert f["status"] == "armed", "une intention, pas un fait"
    assert f["armed_at"] and not f["started_at"]


def test_arreter_DEMANDE_et_ne_pretend_pas_que_c_est_fait(client, org, flotte_a_piloter):
    """⚠️ Le mensonge symétrique, et il est PIRE.

    Entre cet appel et la lecture par la boucle, le passage continue : il réserve,
    il appelle, il dépense. Annoncer `stopped` ferait croire qu'on a coupé une
    dépense qui continue — on part tranquille pendant que ça brûle."""
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "stop", "fleet_id": flotte_a_piloter["id"],
                          "reason": "ça part en vrille"})
    assert r.status_code == 200, r.text
    f = r.json()["fleet"]
    assert f["status"] == "stopping", "l'ordre est posé, la boucle ne l'a pas lu"
    assert f["stopping_at"] and not f["stopped_at"]
    assert f["stop_reason"] == "ça part en vrille", "la raison est ÉCRITE"


def test_l_ecart_entre_demande_et_effectif_est_le_diagnostic(client, org, flotte_a_piloter):
    """Un `stopping` qui ne devient jamais `stopped` désigne un ordonnanceur mort.
    Fondu dans un seul état, ce cas ressemblerait à un arrêt réussi."""
    from oto_mcp import db
    assert db.accuser_arret(flotte_a_piloter["id"], org["id"], None) is True
    f = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "fleet_id": flotte_a_piloter["id"]}
                    ).json()["fleet"]
    assert f["status"] == "stopped" and f["stopped_at"]
    # et la raison posée à la demande SURVIT à l'accusé de réception
    assert f["stop_reason"] == "ça part en vrille"


def test_on_n_arme_pas_ce_qui_tourne_deja(client, org, flotte_a_piloter):
    """Relancer un passage en cours en ouvrirait un second sur la même cible."""
    client.post(ROUTE, headers=_h(org["membre"]),
                json={"op": "launch", "fleet_id": flotte_a_piloter["id"]})
    statut, code = _refus(client, org, {"op": "launch",
                                        "fleet_id": flotte_a_piloter["id"]})
    assert (statut, code) == (409, "not_launchable")


def test_on_n_arrete_pas_ce_qui_ne_tourne_pas(client, org):
    """Un refus qui NOMME l'état, plutôt qu'un 200 qui laisserait croire à un
    arrêt sur un passage jamais lancé."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "jamais-lancee", "procedure": "p",
        "tools": ["oto_kb"]})
    fid = r.json()["fleet"]["id"]
    assert _refus(client, org, {"op": "stop", "fleet_id": fid}) == (409, "not_stoppable")


@pytest.fixture(scope="module")
def simple_membre(client, org):
    """Un membre SANS le rôle admin — sans lui, le plancher n'est pas éprouvé :
    la fixture `org` crée son porteur en `org_admin`."""
    from oto_mcp import db, org_store
    sub = "usr_fleets_simple"
    db.upsert_user(sub, email=f"{sub}@fleets.invalid", name=sub)
    org_store.add_org_member(org["id"], sub, "org_member")
    org_store.set_active_org(sub, org["id"])
    return sub


def test_un_simple_membre_ne_LANCE_pas(client, org, simple_membre):
    """⚠️ Le plancher, éprouvé sur quelqu'un qui n'est PAS admin.

    Lancer engage une dépense et des écritures chez un tiers. Le refus dit aussi
    ce qui reste ouvert — un refus qui n'enseigne rien pousse à chercher un
    contournement."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "plancher", "procedure": "p", "tools": ["oto_kb"]})
    fid = r.json()["fleet"]["id"]
    rr = client.post(ROUTE, headers=_h(simple_membre),
                     json={"op": "launch", "fleet_id": fid})
    assert (rr.status_code, rr.json().get("error")) == (403, "org_admin_required")
    # La phrase du refus est servie sous `detail` (enveloppe REST), pas `message`.
    assert "ARRÊTER" in rr.json().get("detail", ""), (
        "un refus qui n'enseigne pas ce qui RESTE ouvert pousse à chercher un "
        "contournement — ici, que tout membre peut arrêter")


def test_un_simple_membre_ARRÊTE(client, org, simple_membre):
    """L'autre moitié, et c'est le choix de conception : un passage qui part en
    vrille doit pouvoir être stoppé par la première personne qui le voit, pas par
    celle qui a le bon rôle. Attendre un admin pendant qu'une flotte dépense est
    le mauvais échange."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "arret-par-membre", "procedure": "p",
        "tools": ["oto_kb"]})
    fid = r.json()["fleet"]["id"]
    assert client.post(ROUTE, headers=_h(org["membre"]),
                       json={"op": "launch", "fleet_id": fid}).status_code == 200
    rr = client.post(ROUTE, headers=_h(simple_membre),
                     json={"op": "stop", "fleet_id": fid, "reason": "vu passer"})
    assert rr.status_code == 200, rr.text
    assert rr.json()["fleet"]["status"] == "stopping"


# ── LE CYCLE COMPLET : l'intention devient un fait, et par qui ───────────────
#
# ⚠️ C'est ce cycle qui rend `op=stop` RÉEL. Sans lui, l'arrêt est une écriture
# que personne ne lit — et `stopping` resterait éternellement `stopping`, ce qui
# est exactement le symptôme qu'on veut pouvoir DIAGNOSTIQUER.

@pytest.fixture(scope="module")
def flotte_cycle(client, org):
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "cycle", "procedure": "p", "tools": ["oto_kb"]})
    return r.json()["fleet"]


def test_le_cycle_armee_prise_arret_demande_arret_accuse(client, org, flotte_cycle):
    """Les quatre pas, dans l'ordre, et chacun par qui a le droit de le poser."""
    fid = flotte_cycle["id"]
    h = _h(org["membre"])

    armee = client.post(ROUTE, headers=h, json={"op": "launch", "fleet_id": fid}).json()["fleet"]
    assert armee["status"] == "armed" and not armee["started_at"]

    prise = client.post(ROUTE, headers=h, json={"op": "take", "fleet_id": fid}).json()["fleet"]
    assert prise["status"] == "running" and prise["started_at"], (
        "c'est l'ordonnanceur qui pose le FAIT `running`, en prenant la flotte")

    demande = client.post(ROUTE, headers=h, json={
        "op": "stop", "fleet_id": fid, "reason": "budget"}).json()["fleet"]
    assert demande["status"] == "stopping", "l'ordre est posé, pas encore exécuté"

    # l'ordonnanceur LIT l'ordre en battant — c'est ce qui rend `stop` réel
    beat = client.post(ROUTE, headers=h, json={"op": "beat", "fleet_id": fid}).json()
    assert beat["stop_requested"] is True

    acc = client.post(ROUTE, headers=h, json={"op": "ack_stop", "fleet_id": fid}).json()["fleet"]
    assert acc["status"] == "stopped" and acc["stopped_at"]
    assert acc["stop_reason"] == "budget", "la raison de la DEMANDE survit à l'accusé"


def test_un_battement_sans_ordre_ne_dit_pas_qu_il_faut_s_arreter(client, org):
    """Le cas nominal doit être aussi net que le cas d'arrêt : un ordonnanceur qui
    lirait « arrête-toi » par défaut s'éteindrait en boucle."""
    fid = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "battement", "procedure": "p",
        "tools": ["oto_kb"]}).json()["fleet"]["id"]
    client.post(ROUTE, headers=_h(org["membre"]), json={"op": "launch", "fleet_id": fid})
    client.post(ROUTE, headers=_h(org["membre"]), json={"op": "take", "fleet_id": fid})
    beat = client.post(ROUTE, headers=_h(org["membre"]),
                       json={"op": "beat", "fleet_id": fid}).json()
    assert beat["stop_requested"] is False and beat["beat_taken"] is True


def test_deux_ordonnanceurs_ne_prennent_pas_la_meme_flotte(client, org):
    """⚠️ Le second doit l'APPRENDRE, pas partir en croyant l'avoir prise —
    sinon le passage double et son état ne dit la vérité pour aucun des deux."""
    fid = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "concurrence", "procedure": "p",
        "tools": ["oto_kb"]}).json()["fleet"]["id"]
    client.post(ROUTE, headers=_h(org["membre"]), json={"op": "launch", "fleet_id": fid})
    assert client.post(ROUTE, headers=_h(org["membre"]),
                       json={"op": "take", "fleet_id": fid}).status_code == 200
    assert _refus(client, org, {"op": "take", "fleet_id": fid}) == (409, "not_takeable")


def test_on_n_accuse_pas_un_arret_qui_n_a_pas_ete_demande(client, org):
    """Un accusé sans demande effacerait la distinction : `stopped` ne voudrait
    plus dire « l'ordonnanceur a obéi » mais « quelqu'un a écrit stopped »."""
    fid = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "sans-demande", "procedure": "p",
        "tools": ["oto_kb"]}).json()["fleet"]["id"]
    assert _refus(client, org, {"op": "ack_stop", "fleet_id": fid}
                  ) == (409, "nothing_to_acknowledge")
