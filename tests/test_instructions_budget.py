"""Budget du socle injecté + funnel vers le guide `notice` (#478).

Le champ `instructions` du handshake est un canal à livraison NON GARANTIE :
Claude Code coupe l'artefact composé à 2 048 caractères (mesuré au caractère près,
#478 — coupe constatée au milieu du mot « dessous »), et claude.ai ne le transmet
pas au modèle. Le bloc A est donc un SOCLE-RÉSUMÉ qui doit tenir ENTIER sous cette
coupe — c'est le seul contenu dont la livraison est garantie aux clients qui
tronquent — et pointer les deux canaux pull : le guide `notice` (la version
intégrale, seed `oto_mcp/guides/notice.md`) et `oto_context` (le bloc C à la
demande). « Un test qui casse au-delà du plafond retenu vaut mieux qu'une doctrine
qu'on croit servie » (#478) : c'est ce test.
"""
from oto_mcp import call_axes, guide_store, instructions

# Plafond du socle : la coupe client mesurée est 2 048 ; 2 000 garde une marge pour
# que le début du catalogue (la couche suivante) ne soit pas le point de coupe exact.
MAX_SOCLE = 2_000

# La description d'un guide est recopiée dans la description d'`oto_guide` au
# `tools/list` de CHAQUE session (guides_index_md) : une ligne, pas un paragraphe.
MAX_GUIDE_DESC = 160


def test_le_socle_tient_sous_la_coupe_client():
    socle = instructions._SECRET_SAUCE.strip()
    assert len(socle) <= MAX_SOCLE, (
        f"socle de {len(socle)} caractères (max {MAX_SOCLE}) : au-delà de ~2 048, la "
        f"suite n'atteint JAMAIS un client qui tronque (#478). Rallonger le socle = "
        f"amputer sa fin chez ces clients — le détail va dans le guide `notice` "
        f"(oto_mcp/guides/notice.md), pas ici."
    )


def test_le_socle_pointe_les_canaux_pull():
    """Le funnel est le contrat du socle : les clients qui tronquent ne reçoivent QUE
    lui, donc c'est à lui de dire où vit le reste."""
    socle = instructions._SECRET_SAUCE
    assert "slug=notice" in socle, "le socle ne pointe plus le guide `notice`"
    assert "oto_context" in socle, "le socle ne pointe plus `oto_context` (bloc C)"


def test_le_guide_notice_existe_en_seed():
    g = guide_store.file_guide("notice")
    assert g is not None, "seed oto_mcp/guides/notice.md absent — le socle pointe un vide"
    assert g["title"] and g["description"]
    assert len(g["description"]) <= MAX_GUIDE_DESC, (
        f"description de {len(g['description'])} caractères (max {MAX_GUIDE_DESC}) — "
        f"elle est recopiée au tools/list de chaque session via guides_index_md."
    )


def test_la_notice_porte_la_forme_longue():
    """Contrepoids de la coupe : le socle résume, donc la version INTÉGRALE de chaque
    règle doit exister dans la notice — sinon le raccourci n'est pas une déduplication,
    c'est une perte (même logique que
    test_call_axes_schema_budget.test_the_long_form_lives_in_the_server_instructions)."""
    body = guide_store.file_guide("notice")["body_md"]
    for axis in call_axes.AXES:
        assert axis.param in body, (
            f"`{axis.param}` absent de la notice : réduit à une ligne au socle et aux "
            f"schémas, il n'est plus décrit nulle part.")
    for token in ("oto_procedure", "run_start", "run_finish", "feedback",
                  "oto_call", "oto_kb", "oto_doc", "data_claim_next",
                  "<slot:name>", "oto_connector"):
        assert token in body, f"« {token} » a disparu de la notice"
