"""Un guide plateforme sans ligne en base retombe sur son FICHIER, il ne disparaît pas.

Découvert le 14/08 en voulant rendre trois guides à leur version de référence. Le bloc A
se répare en vidant son override — il retombe sur la constante du code. J'ai supposé que
les guides marchaient pareil : **faux**. `read_guide_scoped` ne lisait que la DB, et
`delete_guide` supprime vraiment la ligne. Supprimer un guide plateforme le faisait donc
DISPARAÎTRE du catalogue jusqu'au prochain redémarrage (seul `seed_platform_guides`, au
boot, le recréait).

Deux conséquences fermées ici :
- rendre un guide à sa version de référence redevient un geste sûr et immédiat ;
- un environnement neuf sert ses guides avant même d'avoir semé.

⚠️ Vérifié AVANT d'agir en prod, pas après. C'est la supposition qui était dangereuse,
pas le geste.
"""
import pytest

from oto_mcp import guide_store as G


@pytest.fixture
def db_vide(monkeypatch):
    """Aucune ligne en base — l'état d'un environnement neuf, ou d'un guide rendu."""
    import oto_mcp.db as db
    monkeypatch.setattr(db, "get_guide_db", lambda scope, owner, slug: None)
    monkeypatch.setattr(db, "list_guides_db", lambda scope, owner: [])


def test_sans_ligne_en_base_le_guide_est_SERVI_depuis_son_fichier(db_vide):
    g = G.read_guide_scoped("mcp-apps", scope="platform")
    assert g is not None, "un guide sans ligne DB ne doit pas disparaître"
    assert g["scope"] == "platform"
    assert g["body_md"] == G.file_guide("mcp-apps")["body_md"]


def test_la_ligne_en_base_PRIME_sur_le_fichier(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "get_guide_db", lambda scope, owner, slug: {
        "title": "T", "description": "D", "body_md": "version éditée en ligne"})
    g = G.read_guide_scoped("mcp-apps", scope="platform")
    assert g["body_md"] == "version éditée en ligne"


def test_un_slug_qui_n_existe_NI_en_base_NI_en_fichier_reste_introuvable(db_vide):
    assert G.read_guide_scoped("guide-imaginaire", scope="platform") is None


def test_le_catalogue_liste_les_guides_servables_par_repli(db_vide):
    # Sinon le catalogue mentirait au repli : servable mais invisible, donc introuvable.
    slugs = {g["slug"] for g in G.list_guides_for(sub=None, org_id=None)}
    assert {f["slug"] for f in G.list_file_guides()} <= slugs


def test_le_catalogue_ne_DOUBLE_pas_un_guide_present_des_deux_cotes(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "list_guides_db", lambda scope, owner: [
        {"slug": "mcp-apps", "title": "titre DB", "description": "desc DB"}])
    entrees = [g for g in G.list_guides_for(sub=None, org_id=None)
               if g["slug"] == "mcp-apps"]
    assert len(entrees) == 1
    assert entrees[0]["title"] == "titre DB"      # la DB prime sur son slug


def test_un_guide_NE_DU_CLIC_reste_listé(monkeypatch):
    # Écrit en ligne, sans fichier : il n'a pas de seed, il doit rester servi.
    import oto_mcp.db as db
    monkeypatch.setattr(db, "list_guides_db", lambda scope, owner: [
        {"slug": "procedure-en-routine", "title": "T", "description": "D"}])
    slugs = {g["slug"] for g in G.list_guides_for(sub=None, org_id=None)}
    assert "procedure-en-routine" in slugs
