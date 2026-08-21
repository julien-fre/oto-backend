"""Les NATURES servies au front (lot ⑧) — dérivées d'un rôle, jamais d'un genre.

Le rail attend `type ∈ page | table | agent | execution`. Trois d'entre elles existent
maintenant ; la quatrième n'est pas inventée. Ce qui compte ici est la MÉCANIQUE : le
genre dit ce que l'objet EST, le rôle ce qu'il JOUE, et c'est le rôle qui donne la
nature. Créer `kind='agent'` aurait rouvert le second axe que les lots ⑦ et ⑧ ferment.
"""
import pytest

from oto_mcp.capabilities import node_view as N
from oto_mcp.capabilities import shell as S
from oto_mcp.db import nodes as db_nodes
from oto_mcp.db import shell as db_shell


@pytest.mark.parametrize("surface", [S, N], ids=["rail", "fiche"])
def test_une_procedure_se_rend_en_AGENT_sur_les_deux_surfaces(surface):
    # Les deux surfaces doivent s'accorder : un même nœud qui changerait de nature
    # entre le rail et sa fiche ferait douter de l'un des deux écrans.
    assert surface._type_of("page", {"role": "procedure"}) == "agent"
    assert surface._type_of("page", {}) == "page"
    assert surface._type_of("tableau", {}) == "table"


@pytest.mark.parametrize("surface", [S, N], ids=["rail", "fiche"])
def test_un_role_INCONNU_retombe_sur_le_genre(surface):
    # Une nature inventée serait pire qu'une nature générique : le front choisit un
    # glyphe dessus, et un glyphe faux se lit comme une information.
    assert surface._type_of("page", {"role": "quelque_chose_de_neuf"}) == "page"
    assert surface._type_of("tableau", {"role": "quelque_chose_de_neuf"}) == "table"


def test_execution_n_est_PAS_inventee():
    """`execution` (0054-D7) reste sans source, et volontairement.

    Un nœud-conteneur par run est une décision de VOLUMÉTRIE — autant de nœuds que
    d'exécutions, indéfiniment — pas un trou à combler. La servir depuis un rôle qui
    n'existe pas fabriquerait une nature que rien n'alimente.
    """
    assert "execution" not in S._TYPE_PAR_ROLE.values()
    assert "execution" not in N._TYPE_PAR_ROLE.values()


# ── La conversion des procédures ───────────────────────────────────────────────
def test_la_cle_derivee_est_l_ID_jamais_le_SLUG():
    """Un slug se renomme, un id de séquence non.

    Dériver l'identifiant d'un nœud du slug aurait produit une adresse qui change au
    premier renommage — exactement la classe de défaut que #362 vient de retirer sur les
    blocs. Et c'est l'`id` que `resource_grants` désigne pour une doctrine.
    """
    sql = db_nodes.CONVERT_DOCTRINES_TO_NODES_SQL
    assert "md5('prc:' || (d.id)" in sql
    assert "d.slug" not in sql.split("public_id")[0]     # jamais dans la clé dérivée


def test_la_conversion_est_REJOUABLE_et_purge_ce_qui_a_disparu():
    # Même contrat que les trois autres familles : ON CONFLICT newer-wins, et une
    # procédure supprimée ne laisse pas son nœud derrière elle.
    assert "ON CONFLICT ON CONSTRAINT nodes_public_id_key" in db_nodes.CONVERT_DOCTRINES_TO_NODES_SQL
    purge = db_nodes.PURGE_DOCTRINE_NODES_SQL
    assert "props->>'legacy' = 'prc'" in purge
    # ⚠️ Le prédicat sur `legacy` est ce qui rend la purge sûre : un nœud NATIF n'a pas
    # cette clé, donc n'est jamais candidat. Le relâcher effacerait le contenu neuf.
    assert "NOT EXISTS" in purge


def test_le_pont_grant_vers_noeud_couvre_les_procedures():
    assert db_shell._FAMILLE_PAR_GRANT["doctrine"] == "prc"
    # Le pont est bidirectionnel et dérivé, comme pour les projets et les tableaux.
    import hashlib
    attendu = "nod_" + hashlib.md5(b"prc:41").hexdigest()[:24]
    assert db_shell._public_id_derive("prc", "41") == attendu


def test_une_procedure_convertie_porte_son_role_et_ses_slots():
    sql = db_nodes.CONVERT_DOCTRINES_TO_NODES_SQL
    assert "'role', 'procedure'" in sql        # le rôle, jamais un `kind`
    assert "'slug', d.slug" in sql             # le slug reste ADRESSABLE, en propriété
    assert "'slots'" in sql
    # `kind='page'` comme tout le reste : une procédure est de la prose possédée.
    assert "'page', d.owner_type" in sql
