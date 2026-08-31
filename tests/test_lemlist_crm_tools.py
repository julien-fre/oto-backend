"""Les tools de la moitié non-campagne (CRM, inbox, désinscriptions, signaux…).

`test_lemlist_surface_coverage.py` prouve que TOUT est atteignable ; ce fichier
ne re-teste donc pas les 13 familles op par op — il verrouille la poignée de
décisions qu'une réécriture casse sans bruit : quel op tape quelle méthode quand
DEUX gestes partagent une route, ce qu'un argument omis vaut, et le refus local
d'un appel incomplet (aucun aller-retour, donc rien qui parte).
"""
import asyncio
from unittest.mock import patch

import pytest


def _tool(name, module="lemlist_crm"):
    from fastmcp import FastMCP
    from oto_mcp.tools import lemlist, lemlist_crm

    m = FastMCP("t")
    {"lemlist": lemlist, "lemlist_crm": lemlist_crm}[module].register(m)
    return asyncio.run(m.get_tool(name))


def _with_fake_client():
    key = patch("oto_mcp.access.resolve_api_key", return_value=("fake-key", False))
    cls = patch("oto.tools.lemlist.LemlistClient")
    return key, cls


CRM_TOOLS = {
    "lemlist_contact", "lemlist_company", "lemlist_inbox", "lemlist_inbox_send",
    "lemlist_unsubscribe", "lemlist_task", "lemlist_watchlist",
    "lemlist_database", "lemlist_team", "lemlist_mailbox",
    "lemlist_deliverability", "lemlist_webhook",
    "lemlist_delete_activity_transcript",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name for t in asyncio.run(m._list_tools())}


def test_le_second_module_est_charge_par_le_boot_reel(all_tools):
    """Un fichier de tools posé mais non déclaré au registre dort invisible :
    ce test monte le VRAI `register_all`, pas le module isolé."""
    from oto_mcp.tool_visibility import namespace_of

    assert CRM_TOOLS <= all_tools
    # Le namespace vient du NOM du tool, pas du fichier — un `lemlist_crm_*`
    # tomberait dans un namespace `lemlist_crm` inexistant et ouvrirait le gate.
    assert all(namespace_of(t) == "lemlist" for t in CRM_TOOLS)


@pytest.mark.parametrize("name,module,extra", [
    ("lemlist_contact", "lemlist_crm", {}),
    ("lemlist_company", "lemlist_crm", {}),
    ("lemlist_inbox", "lemlist_crm", {}),
    ("lemlist_unsubscribe", "lemlist_crm", {}),
    ("lemlist_task", "lemlist_crm", {}),
    ("lemlist_watchlist", "lemlist_crm", {}),
    ("lemlist_database", "lemlist_crm", {}),
    ("lemlist_team", "lemlist_crm", {}),
    ("lemlist_mailbox", "lemlist_crm", {}),
    ("lemlist_deliverability", "lemlist_crm", {}),
    ("lemlist_webhook", "lemlist_crm", {}),
    ("lemlist_inbox_send", "lemlist_crm",
     {"message": "x", "send_user_id": "usr_1"}),
    ("lemlist_lead", "lemlist", {}),
])
def test_un_op_inconnu_est_refuse_avant_tout_appel(name, module, extra):
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        with pytest.raises(Exception, match="op inconnu"):
            _tool(name, module).fn(op="teleport", **extra)
        assert not inst.method_calls


# --- Les routes que DEUX gestes partagent -------------------------------------

def test_supprimer_et_desinscrire_un_lead_se_departagent_par_action():
    """Une seule route lemlist, deux gestes, et son défaut est le DOUX."""
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_lead", "lemlist")

        tool.fn(op="delete", campaign_id="cam_1", lead_id="lea_1")
        assert inst.delete_lead.call_args.kwargs == {"action": "remove"}

        tool.fn(op="unsubscribe", campaign_id="cam_1", email="a@acme.fr")
        assert inst.delete_lead.call_args.kwargs == {"action": None}
        assert inst.delete_lead.call_args.args == ("cam_1", "a@acme.fr")


def test_pause_dun_lead_sans_campagne_vise_toutes_les_campagnes():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_lead", "lemlist")

        tool.fn(op="pause", lead_id="lea_1")
        assert inst.pause_lead.call_args.kwargs == {"campaign_id": None}

        tool.fn(op="pause", lead_id="lea_1", campaign_id="cam_1")
        assert inst.pause_lead.call_args.kwargs == {"campaign_id": "cam_1"}


def test_les_trois_listes_de_desinscription_ne_se_confondent_pas():
    """v1 emails, v2 variables, v2 contacts : écrire dans l'une n'écrit pas
    dans les autres, et le dispatch doit le refléter."""
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_unsubscribe")

        tool.fn(op="add", email="a@acme.fr")
        inst.add_unsubscribe.assert_called_once_with("a@acme.fr")

        tool.fn(op="var_add", value="acme.fr")
        inst.unsubscribe_variable.assert_called_once_with("acme.fr")
        inst.add_unsubscribe.assert_called_once()      # toujours une seule fois

        tool.fn(op="contact_add", contact_id="ctc_1")
        inst.unsubscribe_contact.assert_called_once_with("ctc_1")


def test_ajouter_ou_retirer_dune_liste_de_contacts_partage_une_route():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_contact")

        tool.fn(op="list_manage", list_id="lst_1", contact_ids=["ctc_1"])
        assert inst.manage_contact_list.call_args.kwargs == {"action": None}

        tool.fn(op="list_manage", list_id="lst_1", contact_ids=["ctc_1"],
                action="remove")
        assert inst.manage_contact_list.call_args.kwargs == {"action": "remove"}


# --- Ce qui envoie : refus local, et masquage ----------------------------------

def test_un_envoi_incomplet_est_refuse_sans_aller_retour():
    """Le refus AU BORD compte plus qu'ailleurs ici : c'est le seul tool qui
    parle à une personne réelle sans campagne ni revue devant lui."""
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_inbox_send")

        with pytest.raises(Exception, match="send_user_mailbox_id"):
            tool.fn(op="email", message="hi", send_user_id="usr_1",
                    send_user_email="me@x.fr")
        with pytest.raises(Exception, match="lead_id"):
            tool.fn(op="linkedin", message="hi", send_user_id="usr_1")
        assert not inst.method_calls

        tool.fn(op="email", message="hi", send_user_id="usr_1",
                send_user_email="me@x.fr", send_user_mailbox_id="mbx_1")
        assert inst.send_inbox_email.call_args.kwargs["send_user_mailbox_id"] == "mbx_1"


def test_auto_review_a_son_propre_tool_et_construit_le_patch():
    """Le champ reste atteignable — c'est le GESTE qui devient explicite."""
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_campaign_auto_review", "lemlist")

        tool.fn(campaign_id="cam_1", enabled=True, conditions=["deliverable"])
        inst.update_campaign.assert_called_once_with("cam_1", {
            "autoReview": True, "autoReviewConditions": ["deliverable"]})

        tool.fn(campaign_id="cam_1", enabled=False)
        assert inst.update_campaign.call_args.args[1] == {"autoReview": False}


def test_le_dict_de_reglages_de_campagne_ne_peut_pas_armer_lenvoi():
    """L'autre moitié du même contrat : la porte de service reste fermée."""
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_campaign", "lemlist")

        with pytest.raises(Exception, match="lemlist_campaign_auto_review"):
            tool.fn(op="update", campaign_id="cam_1",
                    settings={"autoReview": True})
        inst.update_campaign.assert_not_called()


# --- L'audio d'une note vocale : la seule entrée de FICHIER du connecteur -------

def test_laudio_dune_note_vocale_est_borne_pendant_la_lecture():
    """On télécharge à la place de l'agent, donc c'est nous qui bornons — et on
    s'arrête PENDANT la lecture plutôt que d'accumuler puis tronquer."""
    from oto_mcp.tools import lemlist

    with pytest.raises(Exception, match="https"):
        lemlist._fetch_audio("http://x.fr/a.mp3")

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, n):
            while True:
                yield b"\x00" * n

    with patch("requests.get", return_value=_R()):
        with pytest.raises(Exception, match="au-delà de 20 Mo"):
            lemlist._fetch_audio("https://x.fr/enorme.mp3")


def test_les_activites_savent_marcher_les_pages():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.sync_activities.return_value = [{"type": "emailsSent"}]
        tool = _tool("lemlist_get_activities", "lemlist")

        out = tool.fn(campaign_id="cam_1", all_pages=True, since="2026-08-01")
        inst.sync_activities.assert_called_once_with(
            campaign_id="cam_1", since="2026-08-01", max_pages=50)
        inst.get_activities.assert_not_called()
        assert out["count"] == 1
