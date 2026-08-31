"""`apollo_contact` — les personnes DANS l'espace de travail du propriétaire de
la clé (≠ `people/*`, la base partagée). Ce que ces tests figent :

1. **BYO-only sur les TROIS ops**, lectures comprises. La règle du module n'est
   pas lecture-vs-écriture mais « à qui appartient la donnée » : un contact est
   le carnet d'adresses de l'équipe qui pose la clé. Une clé plateforme
   mutualisée rendrait ici celui de quelqu'un d'autre.
2. **Le catalogue lu est `typed_custom_fields`, pas `/fields`** — bien qu'Apollo
   marque le premier déprécié. C'est le seul qui rende l'ObjectId NU attendu en
   clé de `typed_custom_fields` ; `/fields` rend un id préfixé de sa modalité,
   qu'Apollo ignorerait en rendant 200. Un « on modernise l'endpoint » casserait
   toutes les écritures de champ perso SANS erreur : d'où un test qui nomme
   l'endroit.
3. **La validation des ids** contre le catalogue de CETTE équipe, avant l'appel,
   en nommant les ids valides — et le refus d'un id d'une AUTRE modalité, qu'
   Apollo avalerait sans rien dire.
4. **Le fail-open dit son nom** : `GET typed_custom_fields` exige une clé Master,
   donc « catalogue illisible » est le cas normal d'une clé scopée. L'écriture
   passe quand même, mais la réponse porte `field_validation` — une écriture qui
   se prétendrait vérifiée sans l'être est pire que pas de vérification.
5. **`dry_run` valide identiquement** et saute le seul appel mutant.
6. **`op="get"` ne touche jamais `match_person`** — c'est sa raison d'être :
   relire un contact qu'on possède déjà sans repayer un crédit.

Mock la CLASSE client (jamais `requests`) — cf. `tests/test_apollo_location_filters.py`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Catalogue réaliste : deux champs de contact (dont une picklist) + un champ de
# compte, pour que « mauvaise modalité » soit un vrai cas et pas une hypothèse.
_CATALOG = {
    "typed_custom_fields": [
        {
            "id": "60c39ed82bd02f01154c470a", "name": "Date de renouvellement",
            "modality": "contact", "type": "datetime", "picklist_values": [],
            "finder_view_ids": ["v1"], "mapped_crm_field": None, "meta": {"x": 1},
        },
        {
            "id": "617ff4041e711500a401c25e", "name": "Segment",
            "modality": "contact", "type": "picklist",
            "picklist_values": [{"id": "617ff4041e711500a401c25f",
                                 "name": "New Customer"}],
            "icon_class": "fa-tag", "group": "Commercial",
        },
        {
            "id": "694095a80f1b6000110fc556", "name": "ARR du compte",
            "modality": "account", "type": "currency", "picklist_values": [],
        },
    ]
}


def _mount(monkeypatch, client=None):
    """Monte apollo.py sur un FastMCP nu, client mocké, résolutions tracées.

    Rend `(apollo_contact, calls, client, usage)`. `calls` note QUELLE résolution
    a servi (`api_key` = palier plateforme admis, `byo` = clé du propriétaire
    seul) : c'est ce qui prouve la propriété n°1 ci-dessus, qu'aucune assertion
    sur le retour ne prouverait.
    """
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool

    client = client or MagicMock()
    calls: list[str] = []

    def _resolve_api_key(provider, *a, **k):
        calls.append("api_key")
        return ("platform-key", True)

    class _RC:
        key = "byo-key"

    def _resolve_credential(provider, want="auto", *a, **k):
        assert want == "byo", "un appel contact doit résoudre want=byo, jamais auto"
        calls.append("byo")
        return _RC()

    usage: list[str] = []
    monkeypatch.setattr(access, "resolve_api_key", _resolve_api_key)
    monkeypatch.setattr(access, "resolve_credential", _resolve_credential)
    monkeypatch.setattr(access, "record_platform_usage", lambda p: usage.append(p))
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)

    m = FastMCP("t")
    apollo_tool.register(m)
    return asyncio.run(m.get_tool("apollo_contact")).fn, calls, client, usage


def _ok_client():
    c = MagicMock()
    c.list_typed_custom_fields.return_value = _CATALOG
    c.get_contact.return_value = {"contact": {"id": "c1", "first_name": "Ada"}}
    c.update_contact.return_value = {"contact": {"id": "c1", "title": "CTO"}}
    return c


# ----------------------------------------------------------------------
# Surface
# ----------------------------------------------------------------------

def test_apollo_contact_is_mounted_on_the_real_registry():
    """Sur le montage RÉEL (`register_all`), pas sur une fixture partielle :
    un test qui rejoue une PARTIE du boot en promettant le tout ment par
    omission, toujours dans le sens rassurant (docs/conventions.md)."""
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all
    from oto_mcp.tool_visibility import namespace_of

    m = FastMCP("t")
    register_all(m)
    tools = {t.name: t for t in asyncio.run(m._list_tools())}
    assert "apollo_contact" in tools
    assert tools["apollo_contact"].description, "apollo_contact sans description"
    assert namespace_of("apollo_contact") == "apollo"


def test_the_whole_docstring_reaches_the_model_including_the_per_op_args():
    """⚠️ FastMCP COUPE une docstring au marqueur `Args:` — tout ce qui suit est
    silencieusement retiré de ce que le modèle voit. Vérifié ici même :
    `apollo_search_organizations` (qui écrit `Args:`) sert 485 caractères et
    perd la totalité de ses paramètres documentés.

    Le module s'en sort en écrivant **`Args by op:`**, qui n'est pas le
    marqueur : l'idiome est donc PORTEUR, pas cosmétique. « Uniformiser » cet
    en-tête en `Args:` effacerait, sans erreur ni test rouge ailleurs, la moitié
    de ce que l'agent sait faire de ce tool.
    """
    from fastmcp import FastMCP
    from oto_mcp.tools import apollo as apollo_tool

    m = FastMCP("t")
    apollo_tool.register(m)
    desc = asyncio.run(m.get_tool("apollo_contact")).description

    # le marqueur qui coupe ne doit apparaître nulle part
    assert "Args:" not in desc and "Args by op:" in desc
    # les quatre pièges…
    for must in ("A CONTACT IS NOT A PERSON", "0 Apollo credits",
                 "KEYED BY ID", "MASTER", "label_names", "picklist_values",
                 # les DEUX sources d'un contact_id : nommer la seule qu'on
                 # expose ici enverrait racheter un match déjà payé
                 "person.contact.id"):
        assert must in desc, f"avertissement absent de la description : {must}"
    # …ET la documentation par op, qu'un `Args:` aurait emportée
    for must in ('`fields`:', '`search`:', '`get`:', '`update`:', "dry_run=True"):
        assert must in desc, f"doc d'op perdue : {must}"

    # la preuve du mécanisme, sur un voisin qui écrit `Args:` :
    truncated = asyncio.run(m.get_tool("apollo_search_organizations")).description
    assert "employee_ranges:" not in truncated, (
        "si ceci passe, fastmcp a cessé de tronquer et l'idiome `Args by op:` "
        "n'est plus nécessaire — le constater plutôt que de le supposer")


def test_client_exposes_the_three_methods_this_tool_calls():
    """Jointure tool ↔ oto-core épinglé (garde version-skew). Redondant avec
    `test_tools_client_methods_exist` par construction, explicite ici pour que
    l'échec NOMME le connecteur au lieu d'un paramétrage générique."""
    from oto.tools.apollo.client import ApolloClient
    for meth in ("list_typed_custom_fields", "get_contact", "update_contact"):
        assert callable(getattr(ApolloClient, meth, None)), \
            f"ApolloClient.{meth} manquant — bumper le pin oto-core (pyproject)"


# ----------------------------------------------------------------------
# Régime de clé — la propriété centrale
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"op": "fields"},
    {"op": "get", "contact_id": "c1"},
    {"op": "update", "contact_id": "c1", "title": "CTO"},
])
def test_every_op_resolves_byo_only_never_the_platform_key(monkeypatch, kwargs):
    fn, calls, _c, _u = _mount(monkeypatch, _ok_client())
    fn(**kwargs)
    assert calls and set(calls) == {"byo"}, (
        f"{kwargs['op']} a résolu {calls} — un contact est le carnet d'adresses "
        "du propriétaire de la clé, jamais celui de la clé plateforme")


def test_no_platform_usage_is_metered_because_nothing_here_costs_a_credit(monkeypatch):
    fn, _calls, _c, usage = _mount(monkeypatch, _ok_client())
    fn(op="fields")
    fn(op="get", contact_id="c1")
    fn(op="update", contact_id="c1", title="CTO")
    assert usage == []


def test_unknown_op_is_refused_before_any_credential_is_resolved(monkeypatch):
    fn, calls, _c, _u = _mount(monkeypatch)
    with pytest.raises(McpError) as e:
        fn(op="delete", contact_id="c1")
    assert "op inconnu" in str(e.value)
    assert not calls, "une op inconnue ne doit pas atteindre la résolution de clé"


# ----------------------------------------------------------------------
# op="fields" — le catalogue, et l'endpoint qu'il interroge
# ----------------------------------------------------------------------

def test_fields_reads_typed_custom_fields_not_the_modern_fields_endpoint(monkeypatch):
    """⚠️ Ne pas « moderniser » : `/fields` rend un id PRÉFIXÉ de sa modalité
    (`account.6940…`), inutilisable comme clé de `typed_custom_fields`. Apollo
    ignorerait ces clés en rendant 200 — une écriture perdue SANS erreur."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="fields")
    c.list_typed_custom_fields.assert_called_once_with()
    assert not c.list_fields.called


def test_fields_defaults_to_contact_modality_and_says_what_it_filtered(monkeypatch):
    fn, _calls, _c, _u = _mount(monkeypatch, _ok_client())
    out = fn(op="fields")
    assert [f["id"] for f in out["fields"]] == [
        "60c39ed82bd02f01154c470a", "617ff4041e711500a401c25e"]
    assert out["count"] == 2
    assert out["projection"]["filtered_on"] == "modality=contact"


def test_fields_modality_all_returns_every_object(monkeypatch):
    fn, _calls, _c, _u = _mount(monkeypatch, _ok_client())
    assert fn(op="fields", modality="all")["count"] == 3
    assert fn(op="fields", modality="account")["count"] == 1


def test_fields_drops_named_plumbing_but_keeps_picklist_values(monkeypatch):
    """La picklist est le cas où le catalogue est INDISPENSABLE : la valeur à
    écrire est l'`id` de l'option, jamais son libellé. La projeter dehors
    rendrait le tool trompeur."""
    fn, _calls, _c, _u = _mount(monkeypatch, _ok_client())
    rows = {f["id"]: f for f in fn(op="fields")["fields"]}
    segment = rows["617ff4041e711500a401c25e"]
    assert segment["picklist_values"] == [
        {"id": "617ff4041e711500a401c25f", "name": "New Customer"}]
    assert "icon_class" not in segment and "group" not in segment
    assert "finder_view_ids" not in rows["60c39ed82bd02f01154c470a"]
    assert "meta" not in rows["60c39ed82bd02f01154c470a"]
    # ce qui a été retiré est NOMMÉ, et le chemin vers le brut est donné
    out = fn(op="fields")
    assert "icon_class" in out["projection"]["dropped"]
    # deux échappatoires DISTINCTES, et la réponse ne doit pas les confondre :
    # `full=True` rend les COLONNES retirées, `modality="all"` les LIGNES filtrées
    assert "full=True" in out["projection"]["how_to_get_all_columns"]
    assert 'modality="all"' in out["projection"]["how_to_get_all_objects"]


def test_fields_full_returns_the_untouched_catalogue(monkeypatch):
    fn, _calls, _c, _u = _mount(monkeypatch, _ok_client())
    assert fn(op="fields", full=True) == _CATALOG


# ----------------------------------------------------------------------
# op="get" — la lecture qui ne coûte rien
# ----------------------------------------------------------------------

def test_get_reads_the_contact_and_never_touches_match_person(monkeypatch):
    """La raison d'être de l'op : `apollo_match_person` coûte 1 crédit ET rend
    la fiche PARTAGÉE — ni le stage, ni le propriétaire, ni les champs perso."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    assert fn(op="get", contact_id="c1") == {"contact": {"id": "c1", "first_name": "Ada"}}
    c.get_contact.assert_called_once_with("c1")
    assert not c.match_person.called


@pytest.mark.parametrize("op", ["get", "update"])
def test_contact_id_is_required(monkeypatch, op):
    fn, _calls, _c, _u = _mount(monkeypatch, _ok_client())
    with pytest.raises(McpError) as e:
        fn(op=op, title="CTO")
    assert "contact_id requis" in str(e.value)


# ----------------------------------------------------------------------
# op="update" — la charge utile et sa validation
# ----------------------------------------------------------------------

def test_update_sends_only_the_fields_that_were_given(monkeypatch):
    """PATCH : ce qu'on omet reste INTACT côté Apollo. Envoyer les autres champs
    à None les écraserait — un `title` posé effacerait l'email."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", title="CTO", label_names=["Q3"])
    assert c.update_contact.call_args == (("c1",), {"title": "CTO",
                                                    "label_names": ["Q3"]})


def test_update_without_any_field_is_refused(monkeypatch):
    fn, _calls, c, _u = _mount(monkeypatch, _ok_client())
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1")
    assert "aucun champ à modifier" in str(e.value)
    assert not c.update_contact.called


def test_update_accepts_a_known_contact_custom_field_id(monkeypatch):
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1",
             typed_custom_fields={"60c39ed82bd02f01154c470a": "2026-08-07"})
    assert c.update_contact.call_args.kwargs["typed_custom_fields"] == {
        "60c39ed82bd02f01154c470a": "2026-08-07"}
    assert "field_validation" not in out, "validation réussie : rien à signaler"


def test_unknown_custom_field_id_is_refused_and_the_valid_ones_are_named(monkeypatch):
    """Sans ça Apollo rend 200 en ignorant la clé : l'agent croit avoir écrit.
    Et un refus qui ne dit pas ce qui AURAIT marché fait réessayer au hasard."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1",
           typed_custom_fields={"Date de renouvellement": "2026-08-07"})
    msg = str(e.value)
    assert "Date de renouvellement" in msg
    assert "60c39ed82bd02f01154c470a" in msg and "617ff4041e711500a401c25e" in msg
    assert "694095a80f1b6000110fc556" not in msg, "un champ de COMPTE n'est pas valide ici"
    assert not c.update_contact.called


def test_an_account_field_id_on_a_contact_is_refused_by_modality(monkeypatch):
    """Apollo l'avalerait sans erreur — un champ personnalisé appartient à UN
    objet, et posé sur le mauvais il n'est pas « presque bon », il est perdu."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1",
           typed_custom_fields={"694095a80f1b6000110fc556": 42})
    msg = str(e.value)
    assert "n'appartiennent pas à l'objet contact" in msg
    assert "ARR du compte" in msg and "account" in msg
    assert not c.update_contact.called


def test_typed_custom_fields_must_be_a_mapping(monkeypatch):
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1", typed_custom_fields=["60c39ed82bd02f01154c470a"])
    assert "objet {id_du_champ: valeur}" in str(e.value)
    assert not c.update_contact.called


# ----------------------------------------------------------------------
# Fail-open — et le fait qu'il se DISE
# ----------------------------------------------------------------------

def test_an_unreadable_catalogue_does_not_block_the_write_but_is_reported(monkeypatch):
    """`GET typed_custom_fields` exige une clé Master : « pas de catalogue » est
    le cas NORMAL d'une clé scopée, pas une panne. La validation est un CONFORT ;
    elle ne doit pas devenir une panne. Mais un fail-open SILENCIEUX ferait
    croire à une vérification qui n'a pas eu lieu — d'où `field_validation`."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.list_typed_custom_fields.side_effect = ApolloError("Apollo 403", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1",
             typed_custom_fields={"60c39ed82bd02f01154c470a": "2026-08-07"})
    assert c.update_contact.called
    assert "ids non vérifiés" in out["field_validation"]
    assert "Master" in out["field_validation"]


def test_a_write_without_custom_fields_never_reads_the_catalogue(monkeypatch):
    """Le catalogue est un appel réseau : le payer pour un simple `title` serait
    doubler le coût de chaque écriture sans rien valider."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", title="CTO")
    assert not c.list_typed_custom_fields.called


# ----------------------------------------------------------------------
# dry_run
# ----------------------------------------------------------------------

def test_dry_run_echoes_the_payload_without_writing(monkeypatch):
    c = _ok_client()
    fn, calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1", title="CTO",
             typed_custom_fields={"60c39ed82bd02f01154c470a": "2026-08-07"},
             dry_run=True)
    assert out["dry_run"] is True and out["action"] == "update"
    assert out["contact_id"] == "c1"
    assert out["payload"] == {"title": "CTO", "typed_custom_fields": {
        "60c39ed82bd02f01154c470a": "2026-08-07"}}
    assert not c.update_contact.called
    assert calls, "un dry-run résout quand même le credential (il valide pour de vrai)"


def test_dry_run_still_refuses_an_unknown_field_id(monkeypatch):
    """Un dry-run qui n'échouerait jamais ne prouverait rien : il sert à savoir
    si l'appel RÉEL passerait."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError):
        fn(op="update", contact_id="c1",
           typed_custom_fields={"inconnu": 1}, dry_run=True)
    assert not c.update_contact.called


def test_dry_run_carries_the_fail_open_note_too(monkeypatch):
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.list_typed_custom_fields.side_effect = ApolloError("Apollo 403", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1",
             typed_custom_fields={"x": 1}, dry_run=True)
    assert "ids non vérifiés" in out["field_validation"]


# ----------------------------------------------------------------------
# Erreurs amont traduites
# ----------------------------------------------------------------------

def test_403_names_the_master_key_prerequisite(monkeypatch):
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.get_contact.side_effect = ApolloError("Apollo 403 sur contacts/c1", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="get", contact_id="c1")
    assert "Master" in str(e.value)


def test_422_explains_that_a_person_id_is_not_a_contact_id(monkeypatch):
    """La confusion que ce tool existe pour dissiper : un id d'
    `apollo_search_people` désigne la base PARTAGÉE, pas un contact de l'équipe."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.get_contact.side_effect = ApolloError("Apollo 422", status_code=422)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="get", contact_id="5f3a")
    assert "id de PERSONNE" in str(e.value)


def test_other_upstream_errors_are_not_swallowed(monkeypatch):
    """Traduire DEUX statuts prévisibles ne doit pas avaler le reste : le
    message amont d'Apollo dit quel champ est refusé, c'est ce qui rend un 400
    corrigeable."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.update_contact.side_effect = ApolloError("Apollo 400 : bad stage", status_code=400)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(ApolloError):
        fn(op="update", contact_id="c1", contact_stage_id="nope")


# ----------------------------------------------------------------------
# Couverture des trous nommés par la revue adversariale : sans ces tests,
# supprimer un champ de la charge utile, retirer la traduction d'erreur de
# deux ops sur trois, ou ne pas contrôler une liste de choix restait vert.
# ----------------------------------------------------------------------

# Les 16 champs que `PATCH /contacts/{id}` documente. La liste est recopiée ici
# EXPRÈS : un test qui la lirait depuis le code sous test ne prouverait rien.
_PATCHABLE = {
    "first_name": "Ada", "last_name": "Lovelace", "organization_name": "Acme",
    "title": "CTO", "account_id": "a1", "email": "ada@acme.com",
    "website_url": "https://acme.com", "label_names": ["Q3"],
    "contact_stage_id": "s1", "present_raw_address": "Paris, France",
    "direct_phone": "+33100000001", "corporate_phone": "+33100000002",
    "mobile_phone": "+33600000000", "home_phone": "+33100000003",
    "other_phone": "+33100000004",
    "typed_custom_fields": {"60c39ed82bd02f01154c470a": "2026-08-07"},
}


@pytest.mark.parametrize("field", sorted(_PATCHABLE))
def test_each_documented_field_actually_reaches_the_patch(monkeypatch, field):
    """Un champ oublié dans la construction de la charge utile ne lève RIEN :
    l'agent croit avoir écrit, Apollo n'a rien reçu. Un cas par champ, pour que
    l'échec nomme lequel."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", **{field: _PATCHABLE[field]})
    assert c.update_contact.call_args.kwargs == {field: _PATCHABLE[field]}, (
        f"`{field}` n'atteint pas le PATCH")


def test_all_sixteen_fields_travel_together(monkeypatch):
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", **_PATCHABLE)
    assert c.update_contact.call_args.kwargs == _PATCHABLE


@pytest.mark.parametrize("op,kwargs", [
    ("fields", {}),
    ("get", {"contact_id": "c1"}),
    ("update", {"contact_id": "c1", "title": "CTO"}),
])
def test_the_master_key_403_is_translated_on_all_three_ops(monkeypatch, op, kwargs):
    """La traduction ne valait que pour `get` : la retirer des deux autres ops
    était indétectable, alors que 403 est le refus le PLUS probable ici."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    for meth in ("list_typed_custom_fields", "get_contact", "update_contact"):
        getattr(c, meth).side_effect = ApolloError("Apollo 403", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op=op, **kwargs)
    assert "Master" in str(e.value)


def test_a_422_on_a_write_does_not_lecture_about_person_ids(monkeypatch):
    """Sur un PATCH, 422 veut dire aussi bien « mauvaise valeur » que « mauvais
    id ». Servir la leçon de la lecture enverrait chercher au mauvais endroit."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.update_contact.side_effect = ApolloError(
        "Apollo 422 : contact_stage_id invalide", status_code=422)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1", contact_stage_id="nope")
    msg = str(e.value)
    assert "VALEUR est invalide" in msg
    assert "contact_stage_id invalide" in msg, "le message amont doit survivre"


# --- listes de choix : le piège que la description NOMME ----------------------

def test_a_picklist_written_with_its_label_is_refused_and_the_options_are_named(
        monkeypatch):
    """« Sending the human label is the one mistake Apollo swallows silently » —
    le catalogue qu'on vient de lire porte de quoi le refuser. Documenter le
    piège sans le fermer serait le pire des deux."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1",
           typed_custom_fields={"617ff4041e711500a401c25e": "New Customer"})
    msg = str(e.value)
    assert "liste de choix" in msg
    assert "617ff4041e711500a401c25f" in msg, "l'id de l'option valide doit être nommé"
    assert not c.update_contact.called


def test_a_picklist_written_with_its_option_id_passes(monkeypatch):
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1",
       typed_custom_fields={"617ff4041e711500a401c25e": "617ff4041e711500a401c25f"})
    assert c.update_contact.called


def test_a_picklist_without_readable_options_is_not_policed(monkeypatch):
    """On ne contrôle QUE ce que le catalogue déclare : une picklist dont les
    options ne sont pas lisibles n'est pas refusée, elle n'est pas contrôlée."""
    c = _ok_client()
    c.list_typed_custom_fields.return_value = {"typed_custom_fields": [
        {"id": "p1", "name": "Segment", "modality": "contact", "type": "picklist"}]}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", typed_custom_fields={"p1": "n'importe quoi"})
    assert c.update_contact.called


def test_a_non_picklist_value_is_never_policed_against_options(monkeypatch):
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1",
       typed_custom_fields={"60c39ed82bd02f01154c470a": "2026-08-07"})
    assert c.update_contact.called


# --- catalogue vide vs catalogue illisible : deux choses différentes ----------

def test_a_readable_but_empty_catalogue_REFUSES_instead_of_failing_open(monkeypatch):
    """Lu et vide ⇒ on SAIT que l'id n'existe pas. Laisser passer, c'est laisser
    Apollo avaler l'écriture en rendant 200. À distinguer d'un catalogue
    illisible, où on ne sait rien et où bloquer serait une panne."""
    c = _ok_client()
    c.list_typed_custom_fields.return_value = {"typed_custom_fields": []}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1", typed_custom_fields={"whatever": 1})
    assert "inconnus" in str(e.value)
    assert not c.update_contact.called


def test_an_unexpected_catalogue_shape_still_fails_open_with_a_note(monkeypatch):
    c = _ok_client()
    c.list_typed_custom_fields.return_value = {"unexpected": "shape"}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1", typed_custom_fields={"x": 1})
    assert c.update_contact.called
    assert "ids non vérifiés" in out["field_validation"]


def test_the_fail_open_note_survives_a_non_dict_reply(monkeypatch):
    """« Je n'ai pas pu vérifier » est une information sur l'APPEL, pas sur la
    forme de la réponse — la perdre selon ce qu'Apollo renvoie la rend
    inutilisable au moment où elle compte."""
    from oto.tools.apollo.client import ApolloError
    c = _ok_client()
    c.list_typed_custom_fields.side_effect = ApolloError("403", status_code=403)
    c.update_contact.return_value = ["surprise"]
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="update", contact_id="c1", typed_custom_fields={"x": 1})
    assert out["result"] == ["surprise"]
    assert "ids non vérifiés" in out["field_validation"]


# --- charge utile vide déguisée ----------------------------------------------

def test_an_empty_typed_custom_fields_is_not_a_modification(monkeypatch):
    """`{}` passait la garde « aucun champ à modifier » et partait en PATCH sans
    effet, rendu comme une écriture réussie."""
    c = _ok_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1", typed_custom_fields={})
    assert "aucun champ à modifier" in str(e.value)
    assert not c.update_contact.called


# --- modality : refuser plutôt que rendre un vide qui a l'air d'une réponse ---

def test_an_unknown_modality_is_refused_by_the_schema(monkeypatch):
    """Un catalogue vide « autoritaire » se lit comme « cette équipe n'a aucun
    champ » — la pire réponse possible à une faute de frappe. Le type fermé fait
    trancher fastmcp avant l'appel."""
    from fastmcp import FastMCP
    from oto_mcp.tools import apollo as apollo_tool

    m = FastMCP("t")
    apollo_tool.register(m)
    schema = asyncio.run(m.get_tool("apollo_contact")).parameters
    modality = schema["properties"]["modality"]
    allowed = modality.get("enum") or [
        c["const"] for c in modality.get("anyOf", []) if "const" in c]
    assert set(allowed) == {"contact", "account", "opportunity", "all"}


def test_a_field_without_a_declared_modality_counts_as_a_contact_field(monkeypatch):
    """Défaut permissif — mais il doit être LE MÊME des deux côtés : la liste des
    ids « valides » et le contrôle qui refuse doivent parler du même ensemble."""
    c = _ok_client()
    c.list_typed_custom_fields.return_value = {"typed_custom_fields": [
        {"id": "nomodality", "name": "Champ sans modalité", "type": "text"}]}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="update", contact_id="c1", typed_custom_fields={"nomodality": "ok"})
    assert c.update_contact.called
    with pytest.raises(McpError) as e:
        fn(op="update", contact_id="c1", typed_custom_fields={"absent": "x"})
    assert "nomodality" in str(e.value), (
        "un champ écrivable doit figurer parmi les ids nommés comme valides")


# ----------------------------------------------------------------------
# op="search" — la seule source de `contact_id`
#
# Sans elle les trois autres ops étaient inatteignables pour un agent :
# `apollo_search_people` rend des ids de PERSONNE, qu'Apollo refuse ici.
# ----------------------------------------------------------------------

_SEARCH_REPLY = {
    "contacts": [
        {"id": "c1", "name": "Ada Lovelace", "title": "CTO",
         "email": "ada@acme.com", "organization_name": "Acme", "account_id": "a1",
         "typed_custom_fields": {"60c39ed82bd02f01154c470a": "2026-08-07"},
         # les deux blocs gras qu'Apollo recopie dans chaque fiche
         "organization": {"id": "o1", "name": "Acme", "raw_address": "…" * 200},
         "account": {"id": "a1", "domain": "acme.com"}},
    ],
    "breadcrumbs": [],
    "pagination": {"page": 1, "per_page": 25, "total_entries": 1, "total_pages": 1},
    "partial_results_only": False,
}


def _search_client():
    c = _ok_client()
    c.search_contacts.return_value = _SEARCH_REPLY
    return c


def test_search_resolves_byo_only_like_the_other_ops(monkeypatch):
    fn, calls, _c, _u = _mount(monkeypatch, _search_client())
    fn(op="search", q_keywords="ada")
    assert set(calls) == {"byo"}


def test_search_passes_its_filters_through(monkeypatch):
    c = _search_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="search", q_keywords="ada", contact_stage_ids=["s1"],
       contact_label_ids=["l1"], sort_by_field="contact_updated_at",
       sort_ascending=True, per_page=50, page=2)
    assert c.search_contacts.call_args.kwargs == {
        "q_keywords": "ada", "contact_stage_ids": ["s1"], "contact_label_ids": ["l1"],
        "sort_by_field": "contact_updated_at", "sort_ascending": True,
        "per_page": 50, "page": 2}


def test_search_defaults_to_a_readable_page_not_apollos_maximum(monkeypatch):
    """Apollo accepte 100 fiches par page ; une fiche de contact est grasse, et
    100 d'un coup noient le contexte de l'agent avant qu'il ait choisi."""
    c = _search_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="search")
    assert c.search_contacts.call_args.kwargs["per_page"] == 25


def test_search_keeps_what_lets_you_choose_and_drops_the_two_fat_blocks(monkeypatch):
    fn, _calls, _c, _u = _mount(monkeypatch, _search_client())
    out = fn(op="search", q_keywords="ada")
    row = out["contacts"][0]
    for kept in ("id", "name", "title", "email", "organization_name", "account_id",
                 "typed_custom_fields"):
        assert kept in row, f"`{kept}` sert à choisir, il ne doit pas être projeté dehors"
    assert "organization" not in row and "account" not in row
    # l'enveloppe survit : sans `pagination` l'agent croit avoir tout vu
    assert out["pagination"]["total_entries"] == 1
    assert out["projection"]["dropped"] == ["organization", "account"]


def test_search_full_returns_the_untouched_reply(monkeypatch):
    fn, _calls, _c, _u = _mount(monkeypatch, _search_client())
    assert fn(op="search", full=True) == _SEARCH_REPLY


def test_search_never_reads_apollos_shared_database(monkeypatch):
    """`op=search` cherche dans les contacts DE L'ÉQUIPE. Retomber sur
    `search_people` rendrait des ids de personne — refusés par op=get/update."""
    c = _search_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="search", q_keywords="ada")
    assert c.search_contacts.called
    assert not c.search_people.called and not c.match_person.called


def test_search_403_is_translated_like_the_rest_of_the_family(monkeypatch):
    from oto.tools.apollo.client import ApolloError
    c = _search_client()
    c.search_contacts.side_effect = ApolloError("Apollo 403", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="search")
    assert "Master" in str(e.value)


def test_a_refused_sort_field_surfaces_as_an_actionable_error(monkeypatch):
    """Le client refuse un tri inconnu (Apollo l'ignorerait et rendrait son ordre
    par défaut) ; l'outil doit rendre ce refus lisible, pas un 500."""
    c = _search_client()
    c.search_contacts.side_effect = ValueError("sort_by_field invalide : 'nope'")
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="search", sort_by_field="nope")
    assert "sort_by_field invalide" in str(e.value)


def test_the_client_exposes_search_contacts():
    from oto.tools.apollo.client import ApolloClient
    assert callable(getattr(ApolloClient, "search_contacts", None))


# ----------------------------------------------------------------------
# op="create_field" — le geste de MISE EN PLACE, pour qui n'a pas accès à
# l'interface Apollo du compte client.
# ----------------------------------------------------------------------

def _field_client():
    c = _ok_client()
    c.create_custom_field.return_value = {"typed_custom_fields": [
        {"id": "32d42c92-5be4-4ec4-96c7-f689b43ec8a8", "name": "Personalized opener",
         "modality": "contact", "text_field_max_length": None}]}
    return c


def test_create_field_declares_the_field_and_hands_back_its_id(monkeypatch):
    c = _field_client()
    fn, calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="create_field", label="Personalized opener", field_type="textarea")
    assert c.create_custom_field.call_args.kwargs == {
        "label": "Personalized opener", "modality": "contact",
        "field_type": "textarea", "max_length": None}
    assert out["typed_custom_fields"][0]["id"] == "32d42c92-5be4-4ec4-96c7-f689b43ec8a8"
    assert set(calls) == {"byo"}


def test_create_field_requires_a_label(monkeypatch):
    c = _field_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="create_field", field_type="textarea")
    assert "`label` requis" in str(e.value)
    assert not c.create_custom_field.called


def test_creating_a_field_that_already_exists_is_REFUSED_with_its_id(monkeypatch):
    """Apollo ne déduplique pas sur le libellé : il créerait un SECOND champ
    homonyme. La variable d'une séquence en désignerait un, et les écritures qui
    visent l'autre n'apparaîtraient nulle part — un silence, pas une erreur."""
    c = _field_client()
    c.list_typed_custom_fields.return_value = {"typed_custom_fields": [
        {"id": "existing1", "name": "Personalized opener", "modality": "contact",
         "type": "textarea"}]}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="create_field", label="Personalized opener", field_type="textarea")
    msg = str(e.value)
    assert "existe déjà" in msg and "existing1" in msg
    assert not c.create_custom_field.called


def test_the_same_label_on_a_DIFFERENT_object_is_not_a_duplicate(monkeypatch):
    c = _field_client()
    c.list_typed_custom_fields.return_value = {"typed_custom_fields": [
        {"id": "acct1", "name": "Personalized opener", "modality": "account",
         "type": "textarea"}]}
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    fn(op="create_field", label="Personalized opener", field_type="textarea")
    assert c.create_custom_field.called


def test_create_field_says_so_when_it_could_not_check_for_duplicates(monkeypatch):
    """Clé non-Master : le catalogue est illisible. On crée quand même — mais on
    ne laisse pas croire que l'absence de doublon a été vérifiée."""
    from oto.tools.apollo.client import ApolloError
    c = _field_client()
    c.list_typed_custom_fields.side_effect = ApolloError("403", status_code=403)
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="create_field", label="Personalized opener", field_type="textarea")
    assert c.create_custom_field.called
    assert "doublons non vérifiés" in out["field_validation"]


def test_create_field_dry_run_creates_nothing(monkeypatch):
    c = _field_client()
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    out = fn(op="create_field", label="Personalized opener", field_type="textarea",
             dry_run=True)
    assert out["dry_run"] is True and out["field_type"] == "textarea"
    assert not c.create_custom_field.called


def test_a_refused_field_type_surfaces_as_an_actionable_error(monkeypatch):
    c = _field_client()
    c.create_custom_field.side_effect = ValueError("field_type invalide : 'essay'")
    fn, _calls, _c, _u = _mount(monkeypatch, c)
    with pytest.raises(McpError) as e:
        fn(op="create_field", label="x", field_type="essay")
    assert "field_type invalide" in str(e.value)


def test_the_textarea_trap_is_stated_where_the_model_will_read_it():
    """`string` est plafonné à 120 caractères et Apollo tronque sans se plaindre :
    une accroche personnalisée arriverait coupée en plein mot."""
    from fastmcp import FastMCP
    from oto_mcp.tools import apollo as apollo_tool

    m = FastMCP("t")
    apollo_tool.register(m)
    desc = asyncio.run(m.get_tool("apollo_contact")).description
    assert "textarea" in desc and "truncates" in desc


def test_the_client_exposes_create_custom_field():
    from oto.tools.apollo.client import ApolloClient
    assert callable(getattr(ApolloClient, "create_custom_field", None))
