"""serper_scrape : la page rend « aucun contact » alors que le HTML en porte un.

Le défaut, mesuré le 03/09/2026 (signal #681, palier de contrôle de 23 fiches,
8 faux négatifs de contact dont 4 imputables à l'outil — cinq entreprises
actives classées « indéterminé », deux écartées du fichier) :

  le scraper hébergé rend 200 et un markdown propre de plusieurs milliers de
  caractères, SANS la moindre adresse, là où le HTML en affiche une en clair.
  L'agent ouvre la page, n'y voit rien, écrit de bonne foi « aucun contact
  publié ». Le silence de l'outil devient un mensonge dans le livrable.

Trois motifs relevés à la source, tous reproduits ici avec le HTML RÉEL :

  - `<joomla-hidden-mail text="…">` — base64 dans un ATTRIBUT, donc rien à
    rendre pour un convertisseur qui ne garde que le texte
    (lavoixdeslivres.fr/index.php/l-association) ;
  - `mailto:` en entités HTML décimales (stranumundueditions.wordpress.com) ;
  - `data-cfemail` de Cloudflare, qui n'est pas silencieux mais MENTEUR : le
    rendu affiche le texte littéral `[email protected]`, qu'aucune regex
    d'adresse ne reconnaît (association.lourugby.fr/rugby-loisir).

Et le second volet du signal : sur les trois hébergeurs refusés par le
fournisseur (deux Wix, un WordPress.com), une requête ordinaire portant un UA
de navigateur passe. Les bancs ci-dessous décrivent ces deux chemins ET leur
prix : la relecture du HTML ne se déclenche que sur une page où l'agent
s'apprêtait à conclure « rien », jamais sur une page qui montre déjà un
contact (#662 — ce qu'une attente emporte coûte plus cher que la page).
"""
from __future__ import annotations

import pytest
from mcp.types import ErrorData as _ErrorData
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import mail_obfuscation as M

# ── HTML réel, capturé le 03/09/2026 ─────────────────────────────────────────
HTML_JOOMLA = (
    '<p>Contact : <joomla-hidden-mail  is-link="1" is-email="1" '
    'first="cHJlc2lkZW50ZQ==" last="bGF2b2l4ZGVzbGl2cmVzLmZy" '
    'text="cHJlc2lkZW50ZUBsYXZvaXhkZXNsaXZyZXMuZnI=" base=""  '
    'target="_blank">présidente</joomla-hidden-mail></p>')
HTML_ENTITES = (
    '<a href="mailto:&#115;&#116;ran&#117;&#109;u&#110;&#100;ued&#105;t&#105;'
    'ons&#064;&#103;&#109;ail&#046;com">Nous écrire</a>')
HTML_CLOUDFLARE = (
    '<a href="/cdn-cgi/l/email-protection#ec8083998083859f859e9f9e998b8e95ac'
    '8b818d8580c28f8381"><span class="__cf_email__" '
    'data-cfemail="7f13100a1310160c160d0c0d0a181d063f18121e1613511c1012">'
    '[email&#160;protected]</span></a>')


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


@pytest.fixture()
def monte(monkeypatch):
    """`serper_scrape` monté sur un scraper et un fetch direct tous deux pilotés.

    `etat["scrape"]` = ce que rend le scraper hébergé (ou l'exception qu'il
    lève) ; `etat["html"]` = ce que rend NOTRE requête directe ; `etat["fetchs"]`
    compte les requêtes directes réellement parties — c'est le prix du remède,
    et il se mesure."""
    etat = {"scrape": {}, "html": None, "fetchs": []}

    class _Client:
        _NEVER_SCRAPABLE = {"linkedin.com": "mur de connexion."}

        def __init__(self, *a, **k):
            ...

        @classmethod
        def _refuses_scraping(cls, url):
            return cls._NEVER_SCRAPABLE.get(
                (url.split("//")[-1].split("/")[0] or "").removeprefix("www."))

        def scrape_page(self, url, include_markdown=True, timeout_s=None):
            reponse = etat["scrape"]
            if isinstance(reponse, Exception):
                raise reponse
            return dict(reponse)

    def _fetch(url, deadline_s=M.SONDE_DELAI_S):
        etat["fetchs"].append((url, deadline_s))
        page = etat["html"]
        if page is None:
            return {"ok": False, "verdict": "timeout après 0 redirection(s)"}
        return {"ok": True, "status": 200, "html": page,
                "final_url": url, "verdict": "lu"}

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", False))
    monkeypatch.setattr(M, "fetch", _fetch)
    from oto_mcp.tools import serper
    reg = _Reg()
    serper.register(reg)
    return reg.tools["serper_scrape"], etat


def _page_sans_contact(md: str = "# Association\n\nNous lisons pour les autres."):
    return {"markdown": md, "text": md, "credits": 2, "metadata": {}}


# ── ① le décodage des trois motifs ───────────────────────────────────────────
@pytest.mark.parametrize("html, attendue, motif", [
    (HTML_JOOMLA, "presidente@lavoixdeslivres.fr", "joomla-hidden-mail"),
    (HTML_ENTITES, "stranumundueditions@gmail.com", "mailto en entités HTML"),
    (HTML_CLOUDFLARE, "louloisirsrugby@gmail.com", "cloudflare-email-protection"),
])
def test_adresse_invisible_au_rendu_est_rendue_et_collee_dans_la_page(
        monte, html, attendue, motif):
    """Le scrape réussit sans montrer d'adresse ; le HTML en porte une.

    L'adresse doit ressortir DEUX fois : dans un champ (`adresses_obfusquees`,
    pour qui lit la structure) et dans le contenu lui-même (pour qui lit la
    page — c'est là que l'agent conclut « aucun contact publié »)."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact()
    etat["html"] = html

    res = fn("https://acme.test/contact")

    assert res["adresses_obfusquees"] == [attendue]
    assert res["motifs_obfuscation"] == [motif]
    assert attendue in res["markdown"], (
        "l'adresse doit être COLLÉE dans le contenu servi : un agent qui lit la "
        "page sans regarder les champs conclurait encore « aucun contact »")


def test_cloudflare_ne_disparait_pas_il_ment(monte):
    """Cas Cloudflare : le rendu montre `[email protected]`, texte littéral.

    Ce n'est pas une absence, c'est un faux positif de présence — l'agent voit
    « une adresse » qu'aucune regex ne reconnaît et qu'aucun humain ne peut
    écrire. La relecture doit donc se déclencher malgré ce texte."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact(
        "# Rugby loisir\n\nÉcrivez-nous : [email protected]")
    etat["html"] = HTML_CLOUDFLARE

    res = fn("https://acme.test/rugby-loisir")

    assert res["adresses_obfusquees"] == ["louloisirsrugby@gmail.com"]


def test_motif_vu_mais_non_decodable_est_annonce(monte):
    """Un motif reconnu dont le décodage échoue ne redevient pas un silence.

    « Le pire n'est pas de ne pas décoder — c'est que la page semble ne rien
    contenir » (#681). Ici l'attribut base64 est corrompu : aucune adresse à
    rendre, mais le contenu doit porter le marqueur et dire où chercher."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact()
    etat["html"] = '<joomla-hidden-mail is-email="1" text="!!!pas-du-base64!!!">x'

    res = fn("https://acme.test/contact")

    assert "adresses_obfusquees" not in res
    assert res["motifs_obfuscation"] == ["joomla-hidden-mail"]
    assert "obfuscation d'adresse détectée" in res["markdown"]
    assert 'format="html"' in res["markdown"]


def test_page_qui_montre_deja_une_adresse_ne_coute_aucune_requete(monte):
    """Le prix du remède. La relecture ne part QUE là où l'agent allait conclure
    « rien » : une page qui affiche déjà un contact n'en déclenche aucune.

    Sans cette borne, chaque scrape paierait une requête de plus — et #662 a
    mesuré que l'attente coûte moins par les secondes perdues que par le cache
    de contexte qu'elle fait expirer."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact("Contact : bonjour@acme.test")
    etat["html"] = HTML_JOOMLA

    res = fn("https://acme.test/contact")

    assert etat["fetchs"] == [], "aucune requête directe ne doit partir"
    assert "adresses_obfusquees" not in res


def test_sonde_qui_echoue_le_dit_au_lieu_de_se_taire(monte):
    """Notre relecture peut échouer aussi (site lent, coupé). Elle le DIT.

    Un champ vide se lirait « il n'y a rien » ; `sonde_obfuscation` dit « je
    n'ai pas pu regarder », ce qui n'est pas la même information."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact()
    etat["html"] = None

    res = fn("https://acme.test/contact")

    assert "non concluante" in res["sonde_obfuscation"]
    assert "adresses_obfusquees" not in res


def test_html_sans_obfuscation_laisse_la_reponse_intacte(monte):
    """Relecture faite, rien trouvé : la réponse ne bouge pas d'un caractère.

    Le silence redevient une information juste — la description servie promet
    que l'outil relit toujours le HTML d'une page sans adresse, donc « rien »
    veut dire « on a regardé, il n'y a rien ». Un champ de plus ici ferait
    grossir la quasi-totalité des réponses pour redire le contrat."""
    fn, etat = monte
    etat["scrape"] = _page_sans_contact()
    etat["html"] = "<p>Nous lisons pour les autres.</p>"

    res = fn("https://acme.test/contact")

    attendu = _page_sans_contact()
    attendu.pop("text")          # doublon déjà retiré par le format markdown
    assert len(etat["fetchs"]) == 1, "la relecture doit bien avoir eu lieu"
    assert res == attendu, "aucun champ de plus quand il n'y a rien à dire"


def test_une_sonde_refusee_ne_fait_pas_tomber_un_scrape_reussi(monte, monkeypatch):
    """La relecture est un COMPLÉMENT : son refus ne casse pas la lecture.

    La garde SSRF de notre fetch lève sur un hôte qui ne résout pas ou qui
    pointe vers une adresse non publique. Laisser cette exception remonter
    transformerait un scrape réussi en échec — un remède qui coûte la page
    qu'il devait sauver."""
    fn, etat = monte

    def _refuse(url, deadline_s=None):
        etat["fetchs"].append((url, deadline_s))
        raise McpError(_ErrorData(code=-32600,
                                  message="`acme.test` ne résout pas"))

    monkeypatch.setattr(M, "fetch", _refuse)
    etat["scrape"] = _page_sans_contact()

    res = fn("https://acme.test/contact")

    assert res["markdown"] == _page_sans_contact()["markdown"]
    assert "écartée" in res["sonde_obfuscation"]
    assert "ne résout pas" in res["sonde_obfuscation"]


# ── ② le HTML brut, pour l'agent qui doute ───────────────────────────────────
def test_html_brut_ne_passe_pas_par_le_scraper(monte):
    """`format="html"` rend la page telle quelle, par notre propre requête.

    Le scraper hébergé ne rend AUCUN champ HTML (vérifié sur l'API le 03/09 :
    `text`, `markdown`, `metadata`, `jsonld`, `credits`) — le demander à travers
    lui n'aurait rien donné. Donc : zéro appel au fournisseur, zéro crédit."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("le scraper ne doit pas être appelé")
    etat["html"] = HTML_JOOMLA

    res = fn("https://acme.test/contact", format="html")

    assert res["html"] == HTML_JOOMLA
    assert res["credits"] == 0
    assert res["html_caracteres"] == len(HTML_JOOMLA)
    assert res["html_tronque"] is False
    assert res["adresses_obfusquees"] == ["presidente@lavoixdeslivres.fr"]


def test_html_brut_plafonne_mais_dit_le_total(monte):
    """Le HTML part dans le contexte de l'agent : il est plafonné.

    Le total se sert À CÔTÉ du contenu tronqué — un plafond mesuré sur ce qui a
    déjà été coupé serait inatteignable, donc vert pour toujours."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("le scraper ne doit pas être appelé")
    etat["html"] = "<p>" + "x" * (M.HTML_MAX_CHARS + 5_000) + "</p>"

    res = fn("https://acme.test/grosse-page", format="html")

    assert len(res["html"]) == M.HTML_MAX_CHARS
    assert res["html_caracteres"] == M.HTML_MAX_CHARS + 5_007
    assert res["html_tronque"] is True


def test_html_brut_ne_rouvre_pas_une_source_close(monte):
    """Le client refuse d'emblée les sources closes à l'extraction (mur de
    connexion). `format="html"` ne doit pas être la porte dérobée qui les
    rouvre : le refus vaut pour les deux chemins."""
    fn, etat = monte
    etat["html"] = "<p>peu importe</p>"

    with pytest.raises(McpError) as ei:
        fn("https://www.linkedin.com/in/quelquun", format="html")

    assert "mur de connexion" in ei.value.error.message
    assert etat["fetchs"] == []


def test_html_brut_qui_echoue_leve_une_erreur_nommee(monte):
    """Pas de repli muet : si notre requête ne passe pas, on le dit avec la
    raison, on ne rend pas un HTML vide qui se lirait « page vide »."""
    fn, etat = monte
    etat["html"] = None

    with pytest.raises(McpError) as ei:
        fn("https://acme.test/contact", format="html")

    assert "Lecture directe impossible" in ei.value.error.message
    assert "timeout" in ei.value.error.message


# ── ③ le repli quand le fournisseur refuse ───────────────────────────────────
def test_refus_du_fournisseur_declenche_une_lecture_directe(monte):
    """Second volet du signal : deux sites Wix et un WordPress.com renvoyés en
    échec par le fournisseur répondent normalement à une requête ordinaire
    portant un UA de navigateur.

    Le repli DIT son chemin (`source`) et DIT ce qu'il sert (`format_servi` =
    du texte, pas du markdown : il n'y a pas de convertisseur ici, et prétendre
    le contraire serait pire que le dire)."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("Serper scrape 500: Scraping failed.")
    etat["html"] = ("<h1>Gorge bleue</h1><p>" + "Nature et patrimoine. " * 20
                    + "</p>" + HTML_ENTITES)

    res = fn("https://acme.test/wix")

    assert "scraper hébergé a refusé" in res["source"]
    assert res["format_servi"] == "text"
    assert "Gorge bleue" in res["text"]
    assert res["adresses_obfusquees"] == ["stranumundueditions@gmail.com"]


def test_repli_qui_echoue_aussi_nomme_les_deux_echecs(monte):
    """Quand les deux chemins tombent, le message nomme les deux — sinon
    l'agent croit n'avoir essayé qu'une fois et rejoue la même URL."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("Serper scrape 404: Page not found.")
    etat["html"] = None

    with pytest.raises(McpError) as ei:
        fn("https://acme.test/morte")

    message = ei.value.error.message
    assert "n'existe pas" in message
    assert "lecture directe a échoué aussi" in message
    assert "timeout" in message


def test_repli_sur_une_coquille_vide_ne_se_fait_pas_passer_pour_une_lecture(monte):
    """Une page lue en direct mais sans texte utile n'est pas un succès : la
    servir ferait croire à une entreprise sans contenu."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("Serper scrape 503: Scraping failed.")
    etat["html"] = "<html><body><div id=app></div></body></html>"

    with pytest.raises(McpError) as ei:
        fn("https://acme.test/js")

    assert "lue en direct mais vide" in ei.value.error.message


def test_expiration_ne_declenche_aucun_repli(monte):
    """Une EXPIRATION n'ouvre pas le repli, et c'est délibéré.

    Elle a déjà consommé le budget de l'appelant ; #662 a mesuré que ce qu'une
    attente emporte (le cache de contexte qui expire) coûte plus cher que la
    page qu'on espère. Le repli sert les REFUS, qui sont immédiats."""
    fn, etat = monte
    import requests
    etat["scrape"] = requests.Timeout("read timeout")
    etat["html"] = HTML_JOOMLA

    with pytest.raises(requests.Timeout):
        fn("https://acme.test/lente")
    assert etat["fetchs"] == []


def test_le_budget_serre_par_l_appelant_vaut_aussi_pour_notre_lecture(monte):
    """Un agent qui a demandé 3 s ne veut pas en attendre 20 de plus parce que
    le fournisseur a refusé : notre propre lecture hérite de sa contrainte."""
    fn, etat = monte
    etat["scrape"] = RuntimeError("Serper scrape 500: Scraping failed.")
    etat["html"] = None

    with pytest.raises(McpError):
        fn("https://acme.test/wix", timeout_s=3)

    assert etat["fetchs"] == [("https://acme.test/wix", 3)]
