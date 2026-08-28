"""La chaîne capitalistique se remonte ET se descend — signal #337.

Le besoin est de qualifier l'INDÉPENDANCE : une entreprise appartient-elle, directement
ou via N intermédiaires, à un groupe ? Cas fondateur d'une campagne : 4 leads sur 5
écartés parce que l'INSEE les classait « grande entreprise » — c'étaient des FILIALES,
petites en propre (la catégorie INSEE se calcule sur le périmètre GROUPE, pas sur
l'entité). Sans la chaîne, `categorie_entreprise` fait mentir le ciblage.

La matière existe déjà côté amont, elle n'était pas assemblée :
- l'arête ENFANT → PARENT est le mandataire PERSONNE MORALE du RNE, rendu avec son
  SIREN par `entreprises.get_by_siren` (le même bloc que lit `fr_get`) ;
- l'arête PARENT → ENFANTS passe par l'index plein texte amont, qui indexe les
  DIRIGEANTS (relevé du 2026-08-28 sur l'OpenAPI de recherche-entreprises :
  « q : termes pour une recherche textuelle (dénomination et/ou adresse, dirigeants,
  élus) »). Vérifié par différentiel : `q=LEFEBVRE SARRUT` rend FLS IMMOBILIER,
  EDITIONS LEGISTATIVES et SOCIETE CIVILE ARVIL — aucun de ces noms ne partage un
  token avec la requête, seul le mandataire les relie.

Les charges utiles ci-dessous sont RÉELLES (relevées le 2026-08-28 sur
recherche-entreprises), y compris leur bruit : commissaires aux comptes mêlés aux
actionnaires, trois parents au même niveau, SAS muette sur son actionnaire.
"""
from __future__ import annotations

import pytest


class _Reg:
    """FastMCP minimal : capture les fonctions décorées par @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


def _pm(denom, siren, qualite):
    return {"denomination": denom, "siren": siren, "qualite": qualite,
            "type_dirigeant": "personne morale"}


def _pp(nom, qualite):
    return {"nom": nom, "prenoms": "X", "qualite": qualite,
            "type_dirigeant": "personne physique"}


# --- Répertoire amont RÉEL (relevé 2026-08-28) --------------------------------
_KPMG = _pm("KPMG SA", "775726417", "Commissaire aux comptes titulaire")

_REPERTOIRE = {
    # Chaîne à 3 étages : LEFEBVRE DALLOZ → LEFEBVRE SARRUT → FROJAL → SPIL.
    "572195550": {
        "siren": "572195550", "nom_complet": "LEFEBVRE DALLOZ",
        "nature_juridique": "5710", "categorie_entreprise": "ETI",
        "etat_administratif": "A", "activite_principale": "58.11Z",
        "dirigeants": [_pm("LEFEBVRE SARRUT", "542052451", "Président de SAS"), _KPMG],
    },
    "542052451": {
        "siren": "542052451", "nom_complet": "LEFEBVRE SARRUT",
        "nature_juridique": "5599", "categorie_entreprise": "ETI",
        "etat_administratif": "A",
        "dirigeants": [
            _pp("SILLARD", "Président du conseil d’administration"),
            _pm("FROJAL", "316263003", "Administrateur"),
            _pm("SALUSTRO REYDEL", "652044371", "Commissaire aux comptes suppléant"),
            _KPMG,
        ],
    },
    "316263003": {
        "siren": "316263003", "nom_complet": "FROJAL", "nature_juridique": "5699",
        "categorie_entreprise": "ETI", "etat_administratif": "A",
        "dirigeants": [
            _pp("DURAND", "Président du directoire"),
            _pm("SOCIETE DEPARTICIPATION INVESTISSEMENTS LEFEBVRE \"S.P.I.L\"",
                "378332258", "Membre du conseil de surveillance"),
            _KPMG,
        ],
    },
    # Tête : plus aucun mandataire personne morale hors CAC.
    "378332258": {
        "siren": "378332258", "nom_complet": "S.P.I.L", "nature_juridique": "5499",
        "etat_administratif": "A", "dirigeants": [_pp("LEFEBVRE", "Gérant")],
    },
    # Trois parents AU MÊME NIVEAU : c'est un graphe, pas une chaîne.
    "572082279": {
        "siren": "572082279", "nom_complet": "CALMANN LEVY SA",
        "nature_juridique": "5599", "categorie_entreprise": "PME",
        "etat_administratif": "A",
        "dirigeants": [
            _pp("ROBINET", "Directeur Général"),
            _pm("SARL FRANCE TELEDISTRIBUTIQUE", "351416235", "Administrateur"),
            _pm("HL 93 (SARL)", "390674133", "Administrateur"),
            _pm("DELOITTE & ASSOCIES", "572028041", "Commissaire aux comptes titulaire"),
            _pm("SA HACHETTE LIVRE", "602060147", "Administrateur"),
        ],
    },
    "351416235": {"siren": "351416235", "nom_complet": "FRANCE TELEDISTRIBUTIQUE",
                  "nature_juridique": "5499", "dirigeants": []},
    "390674133": {"siren": "390674133", "nom_complet": "HL 93",
                  "nature_juridique": "5499", "dirigeants": []},
    # `nom_complet` accole le sigle, `nom_raison_sociale` ne l'a pas : c'est la
    # seconde qui part en requête (relevé en réel sur LEFEBVRE SARRUT).
    "602060147": {"siren": "602060147",
                  "nom_complet": "HACHETTE LIVRE (HACHETTE)",
                  "nom_raison_sociale": "HACHETTE LIVRE",
                  "nature_juridique": "5599", "categorie_entreprise": "GE",
                  "etat_administratif": "A", "dirigeants": []},
    # SAS dont l'actionnaire n'est PAS publié : Actes Sud la détient à 100 %, le RNE
    # n'en dit rien. Le piège que le signal met en garde de ne jamais lire comme
    # « indépendante ».
    "315785188": {
        "siren": "315785188", "nom_complet": "EDITIONS PAYOT ET RIVAGES",
        "nature_juridique": "5710", "categorie_entreprise": "PME",
        "etat_administratif": "A",
        "dirigeants": [
            _pp("BAMEULE", "Président de SAS"),
            _pm("CAP SUD EXPERTISE ET AUDIT", "444444444",
                "Commissaire aux comptes titulaire"),
        ],
    },
    # Deux holdings qui se détiennent en boucle — le parcours ne doit pas y tourner.
    "111111111": {"siren": "111111111", "nom_complet": "BOUCLE A",
                  "nature_juridique": "5499",
                  "dirigeants": [_pm("BOUCLE B", "222222222", "Gérant")]},
    "222222222": {"siren": "222222222", "nom_complet": "BOUCLE B",
                  "nature_juridique": "5499",
                  "dirigeants": [_pm("BOUCLE A", "111111111", "Gérant")]},
    # Un seul mandataire personne morale, et c'est un GIE dont on est MEMBRE.
    "333333333": {"siren": "333333333", "nom_complet": "EDITEUR ADHERENT",
                  "nature_juridique": "5710",
                  "dirigeants": [_pm("GIE PROLIVRE", "788242501", "Membre")]},
    # Le répertoire connaît le SIREN mais n'en dit RIEN (unité non diffusible, ou
    # SIREN de test) : nom_complet à None. Relevé sur 999999999.
    "999999999": {"siren": "999999999", "nom_complet": None, "dirigeants": []},
}

# --- Candidats de l'index plein texte pour `q=HACHETTE LIVRE` (29 amont) -------
# 4 d'entre eux n'ont AUCUN lien avec Hachette Livre : ils ne sont là que par le
# token « HACHETTE » ou « LIVRE » de leur nom. C'est le bruit que la vérification
# par SIREN doit écarter — et la raison pour laquelle l'index seul ne suffit pas.
_HL = "602060147"
_CANDIDATS_HL = [
    {"siren": "602060147", "nom_complet": "HACHETTE LIVRE", "dirigeants": []},
    {"siren": "402678007", "nom_complet": "CSE HACHETTE LIVRE", "dirigeants": []},
    {"siren": "441926243", "nom_complet": "L'ESPRIT LIVRE", "dirigeants": []},
    {"siren": "552052425", "nom_complet": "MATRA HACHETTE", "dirigeants": []},
    {"siren": "823331939", "nom_complet": "LEA CHAPEY", "dirigeants": []},
    {"siren": "612035659", "nom_complet": "EDITIONS STOCK", "categorie_entreprise": "PME",
     "etat_administratif": "A",
     "dirigeants": [_pm("HACHETTE LIVRE", _HL, "Associé commandité")]},
    {"siren": "562023705", "nom_complet": "SOCIETE DES EDITIONS GRASSET ET FASQUELLE",
     "categorie_entreprise": "PME", "etat_administratif": "A",
     "dirigeants": [_pm("DELOITTE & ASSOCIES", "572028041",
                        "Commissaire aux comptes titulaire"),
                    _pm("HACHETTE LIVRE", _HL, "Administrateur")]},
    {"siren": "352585624", "nom_complet": "LES EDITIONS HATIER",
     "categorie_entreprise": "PME", "etat_administratif": "A",
     "dirigeants": [_pm("HACHETTE LIVRE", _HL,
                        "Associé indéfiniment et solidairement responsable")]},
    # GIE professionnel : Hachette y est MEMBRE, ce n'est ni une détention ni un
    # contrôle. Le rendre au même rang qu'une filiale fausserait le tri.
    {"siren": "788242501", "nom_complet": "GIE PROLIVRE", "etat_administratif": "A",
     "dirigeants": [_pm("HACHETTE LIVRE", _HL, "Membre")]},
]


class _Entreprises:
    """Stub du proxy FOD — compte ses appels (le fan-out est un coût réel)."""

    def __init__(self, candidats=None):
        self.appels = []
        self._candidats = candidats if candidats is not None else _CANDIDATS_HL

    def get_by_siren(self, siren):
        self.appels.append(("get", siren))
        return _REPERTOIRE.get(siren)

    def search(self, query=None, page=1, per_page=25, **kw):
        self.appels.append(("search", query, page))
        debut = (page - 1) * per_page
        lot = self._candidats[debut:debut + per_page]
        return {"results": lot, "total_results": len(self._candidats),
                "page": page, "per_page": per_page}


@pytest.fixture()
def fr_groupe(monkeypatch):
    amont = _Entreprises()
    monkeypatch.setattr("oto_mcp.fod.fr.entreprises", amont)
    from oto_mcp.tools import fr_groupe as mod
    reg = _Reg()
    mod.register(reg)
    tool = reg.tools["fr_groupe"]
    tool.amont = amont
    return tool


# --- Ascendant ----------------------------------------------------------------

def test_l_ascendant_remonte_toute_la_chaine_jusqu_a_la_tete(fr_groupe):
    out = fr_groupe(siren="572195550", op="ascendant")
    chemin = [(l["de"], l["vers"]) for l in out["liens"]]
    assert chemin == [("572195550", "542052451"),
                      ("542052451", "316263003"),
                      ("316263003", "378332258")]
    assert [t["siren"] for t in out["tetes"]] == ["378332258"]
    assert out["tetes"][0]["profondeur"] == 3


def test_le_commissaire_aux_comptes_n_est_jamais_un_parent(fr_groupe):
    """Un auditeur n'est pas un actionnaire. Sans cette exclusion, KPMG devient la
    tête de groupe de la moitié du CAC 40 et contamine toute la chaîne."""
    out = fr_groupe(siren="572195550", op="ascendant")
    remontes = {l["vers"] for l in out["liens"]}
    assert "775726417" not in remontes   # KPMG
    assert "652044371" not in remontes   # Salustro Reydel
    # La réponse NOMME ce qu'elle a écarté, plutôt que de le faire en silence.
    assert out["exclus"]["commissaires_aux_comptes"] >= 3


def test_calmann_levy_est_un_GRAPHE_pas_une_chaine(fr_groupe):
    """Trois personnes morales au MÊME niveau : un parcours en profondeur en
    manquerait deux. D'où le parcours en largeur."""
    out = fr_groupe(siren="572082279", op="ascendant", max_depth=1)
    niveau1 = sorted(l["vers"] for l in out["liens"] if l["profondeur"] == 1)
    assert niveau1 == ["351416235", "390674133", "602060147"]


def test_une_SAS_muette_est_INDETERMINEE_jamais_independante(fr_groupe):
    """62 % de la cible sont des SAS, dont le registre ne publie pas l'actionnaire
    (le RBE est fermé au public depuis le 31/07/2024). Payot & Rivages ne remonte
    aucune personne morale alors qu'Actes Sud la détient à 100 %."""
    out = fr_groupe(siren="315785188", op="ascendant")
    assert out["tetes"] == []
    assert out["confiance"] == "indeterminee"
    assert "indépendant" in out["caveat"].lower()


def test_l_absence_de_parent_est_une_REPONSE_pas_une_erreur(fr_groupe):
    """Leçon du signal voisin #420 : un agent qui lit `error` arrête sa ligne et la
    campagne repaie le lot. Une absence est une réponse valide."""
    out = fr_groupe(siren="315785188", op="ascendant")
    assert "error" not in out
    assert out["siren"] == "315785188"
    assert out["denomination"] == "EDITIONS PAYOT ET RIVAGES"


def test_un_cycle_de_holdings_ne_boucle_pas(fr_groupe):
    """Les holdings se détiennent en boucle. Le lien qui REFERME la boucle est rendu
    (c'est une information), mais il n'ouvre pas un tour de plus."""
    out = fr_groupe(siren="111111111", op="ascendant")
    assert [(l["de"], l["vers"]) for l in out["liens"]] == [
        ("111111111", "222222222"), ("222222222", "111111111")]
    assert out["tetes"] == []          # A est déjà visité : B n'est pas une tête
    assert out["cycle"] is True
    assert len([a for a in fr_groupe.amont.appels if a[0] == "get"]) == 2


def test_max_depth_borne_le_parcours_et_le_DIT(fr_groupe):
    out = fr_groupe(siren="572195550", op="ascendant", max_depth=1)
    assert len(out["liens"]) == 1
    assert out["tronque"] is True      # il restait des nœuds à explorer


def test_un_parcours_TRONQUE_n_est_pas_INDETERMINE(fr_groupe):
    """Défaut trouvé en exerçant le tool contre l'API RÉELLE : Calmann-Lévy à
    max_depth=1 sortait `confiance="indeterminee"` avec TROIS parents dans `liens`,
    parce que la confiance globale ne se lisait que sur les têtes — et il n'y en a pas
    quand on s'arrête en chemin. Or `indeterminee` porte une consigne précise (« ne
    jamais lire comme indépendante ») : la rendre pour « on s'est arrêté avant »
    la vide de son sens là où elle compte."""
    out = fr_groupe(siren="572082279", op="ascendant", max_depth=1)
    assert out["tetes"] == []
    assert out["tronque"] is True
    assert out["confiance"] == "moyenne"


# --- Confiance ----------------------------------------------------------------

@pytest.mark.parametrize("qualite,attendu", [
    ("Associé commandité", "forte"),
    ("Associé indéfiniment et solidairement responsable", "forte"),
    ("Gérant et associé indéfiniment et solidairement responsable", "forte"),
    ("Président de SAS", "moyenne"),
    ("Administrateur", "moyenne"),
    ("Membre du conseil de surveillance", "moyenne"),
    ("Membre", "faible"),
    ("Liquidateur", "faible"),
    ("Grand Manitou", "inconnue"),
])
def test_la_qualite_gouverne_la_confiance(qualite, attendu):
    from oto_mcp.tools.fr_groupe import confiance_du_lien
    assert confiance_du_lien(qualite) == attendu


def test_forte_prime_sur_moyenne_dans_une_qualite_composee():
    """« Gérant ET associé indéfiniment responsable » porte les deux vocabulaires :
    c'est la mention de DÉTENTION qui doit l'emporter, pas l'ordre du test."""
    from oto_mcp.tools.fr_groupe import confiance_du_lien
    assert confiance_du_lien("Gérant et associé indéfiniment responsable") == "forte"


def test_un_lien_faible_est_rendu_mais_pas_TRAVERSE(fr_groupe):
    """Être MEMBRE d'un GIE professionnel n'est ni une détention ni un contrôle :
    traverser ce lien ferait remonter vers un faux parent, et de là vers tous ses
    autres membres. Le lien est rendu — l'appelant en juge — mais pas suivi."""
    out = fr_groupe(siren="333333333", op="ascendant")
    assert [(l["vers"], l["confiance"], l["traverse"]) for l in out["liens"]] == [
        ("788242501", "faible", False)]
    assert out["tetes"] == []
    assert len([a for a in fr_groupe.amont.appels if a[0] == "get"]) == 1


# --- Descendant ---------------------------------------------------------------

def test_le_descendant_ne_garde_que_les_liens_VERIFIES_par_siren(fr_groupe):
    """L'index amont rend des candidats sur le NOM ; seul le SIREN du mandataire
    prouve le lien. 4 candidats sur 9 ne sont là que par un token de leur nom, et le
    9ᵉ est Hachette Livre elle-même."""
    out = fr_groupe(siren="602060147", op="descendant")
    retenus = sorted(f["siren"] for f in out["filiales"])
    assert retenus == ["352585624", "562023705", "612035659", "788242501"]
    assert "441926243" not in retenus     # L'ESPRIT LIVRE : token « LIVRE » seul
    assert "552052425" not in retenus     # MATRA HACHETTE : token « HACHETTE » seul
    assert out["candidats_examines"] == 9


def test_le_descendant_ecarte_aussi_le_commissaire_aux_comptes(fr_groupe):
    """Grasset porte Deloitte ET Hachette : c'est le lien Hachette qui le retient,
    et il doit être qualifié par LA BONNE qualité."""
    out = fr_groupe(siren="602060147", op="descendant")
    grasset = [f for f in out["filiales"] if f["siren"] == "562023705"][0]
    assert grasset["qualite"] == "Administrateur"
    assert grasset["confiance"] == "moyenne"


def test_le_descendant_ne_se_compte_pas_lui_meme_en_filiale(fr_groupe):
    out = fr_groupe(siren="602060147", op="descendant")
    assert "602060147" not in {f["siren"] for f in out["filiales"]}


def test_le_descendant_cherche_sur_la_RAISON_SOCIALE_et_le_dit(fr_groupe):
    """`nom_complet` accole le sigle entre parenthèses (relevé en réel :
    « LEFEBVRE SARRUT (LEFEBVRE SARRUT) ») — du bruit pour un index plein texte.
    La requête réellement envoyée est rendue, sinon l'appelant ne peut pas juger d'un
    inventaire maigre."""
    out = fr_groupe(siren="602060147", op="descendant")
    assert out["requete"] == "HACHETTE LIVRE"
    assert ("search", "HACHETTE LIVRE", 1) in fr_groupe.amont.appels


def test_les_liens_les_plus_FORTS_arrivent_en_tete(fr_groupe):
    """L'ordre amont est un rang de pertinence TEXTUELLE : il ne dit rien de la force
    du lien, et laisserait une adhésion à un GIE devant une détention."""
    out = fr_groupe(siren="602060147", op="descendant")
    assert [f["confiance"] for f in out["filiales"]] == [
        "forte", "forte", "moyenne", "faible"]


def test_le_descendant_DIT_qu_il_a_tronque(monkeypatch):
    """L'index amont plafonne à 25 par page et 10 000 résultats au total : sur un
    grand groupe (BOUYGUES = 1 476 candidats), l'exhaustivité est hors d'atteinte.
    Le taire ferait passer un échantillon pour un inventaire."""
    bruit = [{"siren": f"9000000{i:02d}", "nom_complet": f"LIVRE {i}",
              "dirigeants": []} for i in range(30)]
    amont = _Entreprises(candidats=_CANDIDATS_HL + bruit)
    monkeypatch.setattr("oto_mcp.fod.fr.entreprises", amont)
    from oto_mcp.tools import fr_groupe as mod
    reg = _Reg()
    mod.register(reg)
    out = reg.tools["fr_groupe"](siren="602060147", op="descendant", max_pages=1)
    assert out["candidats_examines"] == 25
    assert out["candidats_total_amont"] == 39
    assert out["candidats_tronques"] is True


def test_aucune_filiale_est_une_REPONSE_pas_une_erreur(monkeypatch):
    amont = _Entreprises(candidats=[
        {"siren": "441926243", "nom_complet": "L'ESPRIT LIVRE", "dirigeants": []}])
    monkeypatch.setattr("oto_mcp.fod.fr.entreprises", amont)
    from oto_mcp.tools import fr_groupe as mod
    reg = _Reg()
    mod.register(reg)
    out = reg.tools["fr_groupe"](siren="602060147", op="descendant")
    assert out["filiales"] == []
    assert out["total"] == 0
    assert "error" not in out


# --- Refus nommés -------------------------------------------------------------

def test_un_siren_malforme_est_refuse_en_nommant_le_defaut(fr_groupe):
    from mcp.shared.exceptions import McpError
    with pytest.raises(McpError) as e:
        fr_groupe(siren="12345", op="ascendant")
    assert "9 chiffres" in str(e.value)


def test_un_siren_sans_denomination_est_refuse_pas_devine(fr_groupe):
    """L'amont rend une coquille (`nom_complet: null`) pour un SIREN qu'il ne
    connaît pas — et son client `get_by_siren` a un repli qui rend le PREMIER
    résultat quand le SIREN exact manque. Chercher les filiales d'une coquille
    ramènerait celles d'une entreprise homonyme."""
    from mcp.shared.exceptions import McpError
    with pytest.raises(McpError) as e:
        fr_groupe(siren="999999999", op="descendant")
    assert "999999999" in str(e.value)


def test_un_siren_absent_du_repertoire_est_refuse(fr_groupe):
    from mcp.shared.exceptions import McpError
    with pytest.raises(McpError):
        fr_groupe(siren="123456780", op="ascendant")


def test_un_op_inconnu_est_refuse(fr_groupe):
    from mcp.shared.exceptions import McpError
    with pytest.raises(McpError):
        fr_groupe(siren="602060147", op="lateral")


# --- Budget du handshake ------------------------------------------------------

def test_la_description_du_tool_tient_dans_son_budget(fr_groupe):
    """Recopiée dans chaque session, multipliée par ~470 outils : une phrase de plus
    ici est payée à chaque tour par chaque agent (docs/conventions.md, §budget)."""
    assert len(fr_groupe.__doc__) < 1800
