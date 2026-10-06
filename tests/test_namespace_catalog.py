"""Le catalogue de namespaces des instructions serveur est DÉRIVÉ — garde-fou
anti-dérive sur ses DEUX moitiés.

Moitié connecteurs : tout namespace déclaré au registre (hors transports email pur-
credential) doit apparaître. Casse si on ajoute un connecteur sans qu'il soit présenté
— le bug corrigé en son temps (reddit/culture cités, apollo/foncier/pennylane omis de
la liste écrite à la main).

Moitié socle : même exigence, posée le 08/09/2026 après le signal #813. La moitié
plateforme était restée une liste de quatre entrées écrite à la main — `oto_resource`,
`oto_doc`, `oto_kb`, `oto_project` n'étaient nommés nulle part dans une carte qui
s'annonce « complète », et trois agents en ont conclu le même jour, sincèrement, qu'oto
ne savait pas transférer une ressource à une équipe.

Ces tests visent la DÉRIVATION, jamais le contenu : ils n'énumèrent aucune ligne
attendue. Un test qui figerait la liste actuelle serait à réécrire à chaque capacité
ajoutée, donc abandonné, donc inutile.
"""
from oto_mcp import providers
from oto_mcp import spine_catalog as _spine


def test_every_registry_namespace_is_presented():
    cat = providers.render_namespace_catalog()
    for c in providers._REGISTRY_LIST:
        if c.name in providers.EMAIL_CONNECTOR_TRANSPORT:
            continue  # credential-only → présenté via le concept spine email_send
        for ns in c.namespaces:
            assert f"{ns}_*" in cat, f"namespace {ns}_* absent du catalogue (connecteur {c.name})"


def test_email_transports_not_listed_as_namespaces():
    cat = providers.render_namespace_catalog()
    # scaleway/resend = pur credential (aucun tool propre) → pas une ligne de namespace
    assert "scaleway_*" not in cat
    assert "resend_*" not in cat
    # …mais l'email reste présenté comme concept spine
    assert "email_send" in cat


def _outils_spine_montes() -> list[str]:
    """Les outils SPINE de l'instance RÉELLEMENT montée — registre vivant, pas une
    liste écrite ici. C'est ce qui rend ces tests des tests de dérivation : ils
    grandissent avec le produit sans qu'on les touche."""
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import tools as _tools
    from oto_mcp.capabilities import _mcp_adapter, registry

    mcp = FastMCP("test-carte")
    _tools.register_all(mcp)
    _mcp_adapter.register(mcp, registry.caps_with_mcp())
    noms = [t.name for t in asyncio.run(mcp.list_tools())]
    return [n for n in noms if not _spine.is_connector_tool(n)]


def test_un_outil_spine_inconnu_est_quand_meme_nomme():
    """LE test de dérivation du socle : un outil que personne ne revendique apparaît
    de lui-même dans la carte. C'est la propriété qui manquait — l'ancienne moitié
    plateforme ignorait totalement ce qui était monté et ne pouvait donc que taire ce
    qu'elle ne connaissait pas."""
    cat = providers.render_namespace_catalog(spine_tools=["oto_capacite_toute_neuve"])
    assert "oto_capacite_toute_neuve" in cat
    assert "NON CLASSÉE" in cat


def test_chaque_outil_spine_monte_est_couvert():
    """Aucune omission silencieuse : tout outil du socle est revendiqué par une
    famille. Un outil neuf hors des familles fait rougir ici — et la carte le nomme
    quand même en attendant qu'on lui écrive sa ligne."""
    orphelins = [n for n in _outils_spine_montes() if _spine.family_of(n) is None]
    assert not orphelins, (
        f"outils du socle qu'aucune famille ne décrit : {orphelins} — "
        f"déclare-les dans oto_mcp/spine_catalog.py")


def test_aucune_famille_du_socle_n_est_morte():
    """Une famille dont plus aucun outil n'existe est du texte servi qui ment. Le cas
    arrive par renommage, jamais par intention."""
    montes = _outils_spine_montes()
    vides = [f.display for f in _spine.SPINE_FAMILIES
             if not any(_spine.family_of(n) is f for n in montes)]
    assert not vides, f"familles du socle sans aucun outil monté : {vides}"


def test_chaque_famille_affiche_ce_qu_elle_revendique():
    """`display` est écrit à la main (il doit être stable entre le boot et la session).
    Il ne peut donc pas dériver — mais il ne peut pas non plus mentir."""
    for fam in _spine.SPINE_FAMILIES:
        for prefixe in fam.prefixes:
            assert prefixe in fam.display, (
                f"la famille « {fam.display} » revendique {prefixe!r} sans l'afficher")


def test_le_socle_est_dans_la_carte():
    cat = providers.render_namespace_catalog()
    for token in ("data_*", "oto_resource*", "oto_doc*", "run_*"):
        assert token in cat, f"{token} absent du socle de la carte"


def test_availability_annotations():
    cat = providers.render_namespace_catalog()
    # Socle VIDE (16/07) : plus d'annotation par-ligne « hors socle » (elle taguerait
    # tout = bruit) — le régime est dit UNE fois dans l'en-tête (_CATALOG_HEADER).
    assert "hors socle" not in cat
    assert "à activer" not in cat
    # seule annotation restante : le compte à connecter (hosted auth). La ligne du
    # connecteur unipile s'ouvre sur `linkedin_unipile_*` depuis l'ADR 0010
    # §Amendement (le namespace porte la capacité + le fournisseur).
    unipile = next(l for l in cat.splitlines() if l.startswith("• linkedin_unipile_*"))
    assert "account to connect" in unipile


def test_injected_into_server_instructions():
    from oto_mcp import server
    assert "data_*" in server._SERVER_INSTRUCTIONS
    assert "apollo_*" in server._SERVER_INSTRUCTIONS
    # plus de double mention « (MCP fédéré) (MCP fédéré) »
    assert "(MCP fédéré) (MCP fédéré)" not in server._SERVER_INSTRUCTIONS
