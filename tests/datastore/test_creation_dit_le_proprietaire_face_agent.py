"""La création d'un tableau dit QUI le possède — sur les DEUX faces, pas sur une seule.

Le 05/09/2026 (`0e23177e`, otomata-tech/oto#45), la réponse de création a gagné
`owner_type`, `owner_id`, `is_personal` et un `avertissement`. **Côté REST seulement.**
Le même commit a ajouté à la description servie du tool MCP la phrase :

    « The reply tells you the owner, and warns you in exactly that case. »

Elle y était FAUSSE : `data_create_datastore` rendait `{datastore, id, url}`, et son
appelant est précisément celui qui ne peut pas aller vérifier — un modèle, qui lit la
promesse et passe à la suite. Le tableau naît personnel (ADR 0068, c'est voulu), tout
continue de marcher pour son créateur, et l'écart se découvre au second agent.

Ces bancs tiennent la promesse **au niveau du store**, que les deux faces partagent.
C'est ce qui interdit qu'une face reparte seule : la capacité REST n'assemble plus les
champs de son côté, elle rend ce que le store a rendu. Un seul vocabulaire.

⚠️ Le dernier banc CHAÎNE le remède : un avertissement qui nomme un geste que sa propre
face refuse ne vaut pas mieux que le silence. Sur la face agent, `owner` n'existe pas —
le message doit donc nommer `oto_resource`, et ce banc vérifie que ce geste-là existe
vraiment, avec ces paramètres-là.
"""
from __future__ import annotations

import pytest

from oto_mcp import session_org
from oto_mcp.datastore import registre
from oto_mcp.datastore.core import DatastorePg


@pytest.fixture
def store(monkeypatch):
    """Un store réel, sans base : seule la FORME de la réponse est en jeu ici."""
    monkeypatch.setattr(registre.db, "create_datastore",
                        lambda ot, oid, ns, *, context_org_id: 42)
    monkeypatch.setattr(registre, "_ns_url", lambda ns_id, sub, org=None: f"https://d/data/{ns_id}")
    # L'org de contexte (oto#160) a son propre banc (`test_contexte_org_160.py`).
    monkeypatch.setattr(DatastorePg, "_org_de_l_appel", lambda self: None)
    return DatastorePg("u-1")


@pytest.fixture
def org_demandee():
    """Une org EXPLICITEMENT demandée pour cet appel (`_org=` côté agent)."""
    token = session_org.set_call_org(35)
    yield 35
    session_org.reset_call_org(token)


@pytest.fixture
def face_agent():
    token = session_org.set_call_face(session_org.FACE_MCP)
    yield
    session_org.reset_call_face(token)


def test_la_creation_rend_le_proprietaire(store):
    out = store.create_datastore("vivier")
    assert out["owner_type"] == "user"
    assert out["owner_id"] == "u-1"
    assert out["is_personal"] is True
    # Les champs historiques ne bougent pas : c'est un AJOUT, pas un remplacement.
    assert out["datastore"] == "vivier" and out["id"] == 42 and "url" in out


def test_un_tableau_d_org_se_dit_non_personnel(store):
    out = store.create_datastore("vivier", owner_type="org", owner_id="35")
    assert (out["owner_type"], out["owner_id"]) == ("org", "35")
    assert out["is_personal"] is False


def test_sans_org_demandee_aucun_avertissement(store):
    """Un avertissement qui se déclenche toujours ne se lit plus (piège de `0e23177e` :
    `ctx.org_id` vaut l'org active, TOUJOURS posée)."""
    assert "avertissement" not in store.create_datastore("vivier")


def test_une_org_demandee_et_un_tableau_perso_avertit(store, org_demandee):
    out = store.create_datastore("vivier")
    assert "avertissement" in out
    assert "35" in out["avertissement"]


def test_un_owner_demande_explicitement_n_avertit_pas(store, org_demandee):
    """L'appelant a dit ce qu'il voulait : il n'y a plus de surprise à lui signaler."""
    out = store.create_datastore("vivier", owner_type="org", owner_id="35")
    assert "avertissement" not in out


def test_le_remede_de_la_face_agent_ne_nomme_pas_un_parametre_qu_elle_n_a_pas(
        store, org_demandee, face_agent):
    """`owner` est un paramètre de la ROUTE REST. Le tool MCP ne l'a pas.

    Le prescrire à un agent, c'est lui faire dépenser un appel pour un refus — le
    défaut même que ce lot répare, retourné."""
    message = store.create_datastore("vivier")["avertissement"]
    assert "owner:" not in message
    assert "oto_resource" in message and "new_owner_org" in message


def test_le_remede_prescrit_a_l_agent_existe_vraiment(store, org_demandee, face_agent):
    """Chaîner la destination : le geste nommé doit être servi, avec CES paramètres.

    Un banc qui se contente de lire la phrase prouve que la phrase est bien écrite,
    pas qu'elle mène quelque part."""
    from oto_mcp.capabilities.resources import ResourceInput

    message = store.create_datastore("vivier")["avertissement"]
    assert "oto_resource" in message

    champs = ResourceInput.model_fields
    assert "new_owner_org" in champs, "le remède nomme un paramètre qui n'existe plus"
    assert "transfer" in champs["op"].annotation.__args__, (
        "`op='transfer'` n'est plus servi : le remède prescrit un geste refusé")
    # Et il porte bien sur cette famille-là, sans avoir à la nommer.
    assert champs["resource_type"].default == "datastore_namespace"


def test_le_TOOL_SERVI_tient_la_promesse_de_sa_propre_description(monkeypatch):
    """Le banc qui ferme oto#45 : on appelle le tool MONTÉ, pas le store sous lui.

    La description servie de `data_create_datastore` promet depuis le 05/09 que « la
    réponse te dit le propriétaire ». Le store peut bien la rendre : si le tool la
    ré-emballait, ou n'en relayait qu'une partie, le modèle lirait toujours une
    promesse fausse — et c'est le seul lecteur qui compte ici."""
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as surface

    monkeypatch.setattr(registre.db, "create_datastore",
                        lambda ot, oid, ns, *, context_org_id: 42)
    monkeypatch.setattr(registre, "_ns_url", lambda ns_id, sub, org=None: f"https://d/data/{ns_id}")
    # L'org de contexte (oto#160) a son propre banc (`test_contexte_org_160.py`).
    monkeypatch.setattr(DatastorePg, "_org_de_l_appel", lambda self: None)
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: "u-1")
    monkeypatch.setattr(surface, "_store_for", lambda sub: DatastorePg("u-1"))

    mcp = FastMCP("sonde")
    surface.register(mcp)
    tool = asyncio.run(mcp.get_tool("data_create_datastore"))

    out = tool.fn(datastore="vivier")
    assert out["owner_type"] == "user" and out["owner_id"] == "u-1"
    assert out["is_personal"] is True


def test_le_TOOL_SERVI_avertit_quand_une_org_etait_demandee(monkeypatch, org_demandee,
                                                            face_agent):
    """L'autre moitié de la promesse : « … et t'avertit dans ce cas précis »."""
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as surface

    monkeypatch.setattr(registre.db, "create_datastore",
                        lambda ot, oid, ns, *, context_org_id: 42)
    monkeypatch.setattr(registre, "_ns_url", lambda ns_id, sub, org=None: f"https://d/data/{ns_id}")
    # L'org de contexte (oto#160) a son propre banc (`test_contexte_org_160.py`).
    monkeypatch.setattr(DatastorePg, "_org_de_l_appel", lambda self: None)
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: "u-1")
    monkeypatch.setattr(surface, "_store_for", lambda sub: DatastorePg("u-1"))

    mcp = FastMCP("sonde")
    surface.register(mcp)
    tool = asyncio.run(mcp.get_tool("data_create_datastore"))

    out = tool.fn(datastore="vivier")
    assert "35" in out["avertissement"]


def test_la_face_rest_rend_exactement_ce_que_le_store_rend(monkeypatch):
    """Un seul vocabulaire : la capacité REST n'assemble plus les champs de son côté.

    Si elle recommençait, les deux faces pourraient diverger sans qu'aucun banc ne
    tombe — c'est la dette qu'on ferme ici."""
    from _datastore_rest import call as _call, stub_authz

    stub_authz(monkeypatch)
    # Un propriétaire que la capacité ne peut PAS avoir dérivé elle-même (l'appel ne
    # porte aucun `owner`, elle en conclurait « user / u-1 ») : si elle réassemble, le
    # banc tombe. C'est la seule façon de distinguer « elle relaie » de « elle refait ».
    rendu = {"datastore": "vivier", "id": 42, "url": "https://d/data/42",
             "owner_type": "group", "owner_id": "9", "is_personal": False}

    class _Store:
        def create_datastore(self, datastore, *, owner_type=None, owner_id=None):
            return dict(rendu)

    from oto_mcp.capabilities.datastore import datastores as dsn
    monkeypatch.setattr(dsn, "make_store", lambda sub: _Store())
    code, body = _call("me.datastore.create_datastore", body={"datastore": "vivier"})
    assert code == 201
    for cle, valeur in rendu.items():
        assert body[cle] == valeur
