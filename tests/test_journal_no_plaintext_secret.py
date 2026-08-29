"""Les deux autres canaux par lesquels un secret sortait du serveur (#558, #564).

1. `tool_calls.args` — le jeton d'invitation passe aussi par l'outil (`oto_org
   op=accept_invite`), pas seulement par l'URL. Même masque, même déclaration.
2. Sentry — `include_local_variables` vaut `True` par défaut (sentry-sdk 2.63.0),
   donc chaque exception emportait les variables locales de chaque frame, dont
   celles du chemin de résolution qui tiennent le secret DÉCHIFFRÉ.
"""
from __future__ import annotations

import pytest


# --- 1. Les arguments d'outil ------------------------------------------------

def test_le_jeton_dinvitation_ne_part_pas_en_clair_dans_les_args():
    from oto_mcp import calllog
    args = calllog.truncated_args(
        {"op": "accept_invite", "token": "inv_Zm9vYmFyBAZ", "code": "ABC1234"},
        tool="oto_org")
    assert "inv_Zm9vYmFyBAZ" not in str(args)
    assert "ABC1234" not in str(args)
    assert args["op"] == "accept_invite"          # l'intention reste lisible
    assert args["token"].startswith("#")


def test_un_argument_homonyme_dun_connecteur_reste_lisible():
    """Le masquage suit la DÉCLARATION, pas le nom : `droit_article(code='CT')`
    n'est pas un secret et le journal doit continuer à le montrer."""
    from oto_mcp import calllog
    args = calllog.truncated_args({"code": "CT", "op": "get"}, tool="droit_article")
    assert args["code"] == "CT"


def test_le_dispatch_universel_ne_rouvre_pas_le_canal():
    """`oto_call` porte les arguments de l'outil VISÉ dans un sous-dictionnaire :
    sans reprise de la déclaration de la cible, le jeton repasserait en clair."""
    from oto_mcp import calllog
    args = calllog.truncated_args(
        {"name": "oto_org",
         "arguments": {"op": "accept_invite", "token": "inv_Zm9vYmFyBAZ"}},
        tool="oto_call")
    assert "inv_Zm9vYmFyBAZ" not in str(args)


# --- 2. Sentry ---------------------------------------------------------------

def test_sentry_ninit_jamais_avec_les_variables_locales(monkeypatch):
    """Cliquet : le réglage ABSENT vaut `True` chez sentry-sdk. Un retrait de la
    ligne rouvre donc la fuite sans changer une seule ligne visible ailleurs."""
    from oto_mcp import sentry_setup
    vus: dict = {}
    monkeypatch.setattr(sentry_setup.sentry_sdk, "init",
                        lambda **kw: vus.update(kw))
    monkeypatch.setenv("OTO_SENTRY_DSN", "https://x@example.invalid/1")
    assert sentry_setup.init_sentry() is True
    assert vus.get("include_local_variables") is False, (
        "include_local_variables absent ou vrai : chaque exception repart avec les "
        "locales de chaque frame, dont le secret déchiffré du chemin de résolution.")


# --- 3. Le secret déchiffré ne se raconte pas --------------------------------
#
# Le `repr` expurgé des deux porteurs a sa propre couture et ses propres tests :
# `oto_mcp/access/secret_repr.py` et `tests/test_secret_repr.py`. Ici, la seule
# chose qui les met à l'épreuve pour de vrai — une panne sur le chemin chaud.

def test_une_exception_du_palier_plateforme_ne_ramasse_pas_le_secret(monkeypatch):
    """La reconstitution de #564 : une panne RÉELLE (pas une erreur gérée) levée
    pendant que la frame tient le grant plateforme. On sérialise ensuite chaque
    frame du traceback comme le ferait un collecteur d'erreurs, et le secret ne
    doit s'y trouver nulle part."""
    from oto_mcp import session_org
    from oto_mcp.access import cascade, quotas, rbac, resolve, scope

    SECRET = "sk_live_TRESSECRET"
    monkeypatch.setattr(rbac, "require_connector_access", lambda p, s: None)
    monkeypatch.setattr(session_org, "current_call_instance", lambda: None)
    monkeypatch.setattr(scope, "project_pinned_instance", lambda p: None)
    monkeypatch.setattr(scope, "current_org", lambda s: 1)
    monkeypatch.setattr(cascade, "_is_multi_account", lambda p, o: False)
    monkeypatch.setattr(
        cascade, "cascade_winner",
        lambda *a, **k: cascade.CascadeRung(
            "platform", "platform", "cle-plateforme",
            {"secret": SECRET, "label": "cle-plateforme", "daily_quota": 10}))

    def _panne(sub, provider):
        raise RuntimeError("compteur de quota indisponible")

    monkeypatch.setattr(quotas, "usage_today", _panne)

    tb = None
    try:
        resolve._resolve_credential_impl("stripe", "auto", "u-1")
    except RuntimeError as e:
        tb = e.__traceback__
    assert tb is not None, "la reconstitution n'a levé aucune panne"
    tb = tb.tb_next          # la frame du test tient la constante, forcément
    frames = 0
    while tb is not None:
        frames += 1
        for nom, val in tb.tb_frame.f_locals.items():
            assert SECRET not in repr(val), (
                f"le secret déchiffré est lisible dans la locale `{nom}` de "
                f"`{tb.tb_frame.f_code.co_name}` — une exception l'emporte avec elle.")
        tb = tb.tb_next
    assert frames >= 2, "traceback trop court : la reconstitution n'a rien exercé"


def test_le_secret_epingle_ne_reste_pas_lie_apres_usage():
    """Cliquet de FORME, faute de mieux : la frame de `_resolve_pinned_instance` ne
    lève rien après avoir lu le coffre, donc aucun test de comportement ne peut
    l'exercer aujourd'hui — c'est une édition FUTURE qui rouvrirait le trou. On
    garde donc la forme : le nom est délié une fois le credential remis."""
    import ast
    import inspect

    from oto_mcp.access import resolve
    arbre = ast.parse(inspect.getsource(resolve._resolve_pinned_instance))
    dels = {t.id for n in ast.walk(arbre) if isinstance(n, ast.Delete)
            for t in n.targets if isinstance(t, ast.Name)}
    assert "secret" in dels, (
        "`_resolve_pinned_instance` lie le secret déchiffré à `secret` sans le "
        "délier : la frame le garde jusqu'à sa sortie.")
