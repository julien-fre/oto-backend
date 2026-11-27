"""Les modules qui lient le lecteur d'identité sont importés avant tout test (#1111) : un
premier import pendant le patch d'`auth.hooks` ne peut plus leur laisser le faux lecteur."""
import sys

import _etat_global


def test_le_releve_trouve_les_modules_qui_lient_le_lecteur():
    noms = _etat_global.modules_qui_lient_le_lecteur()
    assert "oto_mcp.middleware.call_context" in noms
    assert "oto_mcp.auth.hooks" not in noms


def test_ils_sont_deja_importes_et_lient_l_original():
    from oto_mcp.auth import hooks
    origine = hooks.__dict__["current_user_sub_from_token"]
    for nom in _etat_global.modules_qui_lient_le_lecteur():
        assert nom in sys.modules, nom
        assert sys.modules[nom].__dict__["current_user_sub_from_token"] is origine, nom
