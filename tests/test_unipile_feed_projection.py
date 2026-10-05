"""Le feed LinkedIn tient dans un résultat d'outil (#384) — et il est servi EN DIRECT (oto#156).

`linkedin_unipile_post(op="feed", limit=40)` rendait **67 383 caractères**, au-delà du
plafond d'un résultat MCP : observé en conditions réelles sur la procédure
`veille-linkedin`, le harnais a déversé la sortie dans un fichier et l'agent a dû
repasser au `jq` pour la ramener à 42 Ko — deux tours et un détour par le shell avant
de commencer le vrai travail. Un client MCP nu (agent n8n, pas de shell) n'a lui aucun
recours : il cale sur l'appel.

Ce que ce fichier verrouille, c'est le **défaut** — pas la présence d'un paramètre
optionnel de plus (ADR 0047 §Amendement du 11/08 : *le chemin paresseux doit être le
chemin juste*). Le signal jumeau #281 avait ajouté `fields`/`text_max_chars` à
`op="posts"` sans toucher au défaut : le payload est resté lourd, et le même incident
s'est rejoué ici. D'où, en regard de chaque allègement, le test que **rien n'est caché**
(chemin brut intact, champs écartés nommés dans la réponse).

**oto#156 (05/10/2026)** : le feed n'est plus recopié dans le datastore. Le miroir
`linkedin-feed` se resynchronisait en REMPLAÇANT ses lignes (`upsert_row`) ; une page
est désormais lue chez Unipile à chaque appel, triée en mémoire, paginée par le `cursor`
de LinkedIn. Le dernier banc de ce fichier prouve, sur une vraie base, que `op="feed"`
n'écrit plus rien.

Les tailles ci-dessous sont calibrées sur 40 posts RÉELS (texte : médiane 730, moyenne
971, max 2 712 caractères ; post brut ~1 650 caractères).
"""
from __future__ import annotations

import asyncio
import json
import random
from unittest.mock import MagicMock

import pytest

# Longueurs de texte reproduisant la distribution mesurée sur 40 posts réels
# (somme ~38 800 caractères) : quelques posts fleuve, une majorité de moyens, du bruit.
_TEXT_LENGTHS = [
    2712, 2410, 2088, 1904, 1743, 1602, 1488, 1371, 1266, 1180,
    1104, 1032, 966, 918, 872, 830, 792, 760, 744, 736,
    729, 722, 700, 664, 620, 574, 520, 470, 418, 372,
    320, 274, 228, 186, 140, 96, 60, 32, 12, 1,
]


def _post(i: int) -> dict:
    """Un post tel que `client.get_feed` le rend (oto-core `parse_feed`)."""
    urn = f"urn:li:activity:74929182333737{90000 + i}"
    return {
        "urn": urn,
        "author_name": f"Auteur Numéro {i}",
        "author_headline": "Fondateur & CEO | On parle IA appliquée, agents et ops",
        "text": "x" * _TEXT_LENGTHS[i],
        # décroissant avec `i` : le feed trie par date, l'ordre de la fixture est
        # donc celui du résultat (item[0] = le plus récent = le plus long ici).
        "posted_at": f"2026-08-11T12:{39 - i:02d}:58.525000+00:00",
        "posted_relative": "6m •   ",
        "reactions_count": i * 3,
        "comments_count": i,
        "feed_reason": "Suggéré pour toi" if i % 3 else None,
        "surfaced_by": None,
        "comment_authors": [],
        "content_type": "text",
        "content_title": None,
        "post_url": f"https://www.linkedin.com/feed/update/{urn}",
        "is_repost": False,
        "original_author_name": None,
        "original_text": None,
        "original_content_type": None,
    }


ROWS = [_post(i) for i in range(40)]


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import unipile as U

    m = FastMCP("t")
    U.register(m)
    return asyncio.run(m.get_tool("linkedin_unipile_post")).fn


@pytest.fixture
def client(monkeypatch):
    """Le client Unipile : une page de 40 posts rendue DANS LE DÉSORDRE, comme la home
    LinkedIn (tri « pertinence ») — le tri par date est le travail de l'outil."""
    from oto_mcp.tools import unipile as U

    desordre = list(ROWS)
    random.Random(156).shuffle(desordre)
    c = MagicMock()
    c.get_feed.return_value = {"items": desordre, "cursor": "40|jeton-suivant",
                               "count": len(desordre)}
    monkeypatch.setattr(U, "unipile_client", lambda *a, **k: c)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    # Le cooldown 429 est un état de MODULE : un autre banc du même worker a pu l'armer
    # pour ce sub. Chaque banc part d'un compte qui n'est pas en attente.
    monkeypatch.setattr(U, "_RATE_LIMIT_UNTIL", {})
    return c


@pytest.fixture
def feed(client):
    return _tool()


# Le seuil DISCRIMINE la demi-mesure : sur cette fixture (les champs d'un post d'Unipile,
# sans la comptabilité de l'ancien miroir), la vue de tri rend 0,71 de la page brute et
# **tronquer le texte seul** 0,76 — un défaut qui ne ferait que couper le texte échoue
# donc ici. (Sur les 40 lignes réelles de l'ancien miroir : 65 899 → 40 765 caractères.)
SEUIL = 0.73


def _chars(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _raw_page_chars() -> int:
    return _chars({"items": ROWS, "cursor": "40|jeton-suivant", "count": len(ROWS)})


# --- le défaut, c'est-à-dire le sujet du signal --------------------------------

def test_le_defaut_tient_dans_un_resultat_doutil(feed):
    """LE test du signal : `limit=40` SANS paramètre supplémentaire.

    Le budget est exprimé en part de la page brute, pas en valeur absolue : ce qui
    compte est le coût PAR POST (la page grandit linéairement avec `limit`). Un défaut
    qui rendrait le brut — même flanqué de `fields` et `text_max_chars` optionnels —
    échoue ici, et c'est voulu : c'est exactement ce qui a été livré pour #281.
    """
    raw = _raw_page_chars()
    assert raw > 50_000, (
        "fixture non représentative : la page brute doit être énorme (la vraie, "
        "mesurée sur 40 posts d'un compte réel, pèse ~66 000 caractères)")

    out = feed(op="feed", limit=40)

    assert _chars(out) < SEUIL * raw, (
        f"la page par défaut pèse {_chars(out)} caractères pour {raw} en brut — "
        "un défaut qui ne coupe pas laisse l'agent faire le tri au shell (#384)")
    assert len(out["items"]) == 40, "alléger la page ne doit pas rendre moins de posts"


def test_le_texte_est_un_extrait_et_la_coupe_est_marquee(feed):
    out = feed(op="feed", limit=40)
    long_post, short_post = out["items"][0], out["items"][-1]

    assert len(long_post["text"]) == 601 and long_post["text"].endswith("…")
    assert long_post["text_truncated"] is True, (
        "une coupe non marquée ferait croire à l'agent qu'il a lu le post entier")
    assert "text_truncated" not in short_post, "un texte court n'est pas marqué coupé"
    assert len(short_post["text"]) == 1, "un texte déjà court n'est pas touché"


def test_le_defaut_garde_de_quoi_trier_et_agir(feed):
    """La vue par défaut doit porter tout ce dont le guide `veille-linkedin` se
    sert : classer (auteur, headline, date, texte, traction) et restituer (`post_url`),
    dédupliquer et rouvrir (`urn`)."""
    it = feed(op="feed", limit=40)["items"][0]
    for col in ("urn", "post_url", "author_name", "author_headline", "posted_at",
                "text", "reactions_count", "comments_count"):
        assert col in it, f"`{col}` sert au tri du feed : il ne peut pas sauter"


def test_le_defaut_ecarte_ce_qui_ne_sert_pas_au_tri(feed):
    """Le temps relatif se dérive de `posted_at` ; les deux listes sont vides en pratique."""
    it = feed(op="feed", limit=40)["items"][0]
    for col in ("posted_relative", "surfaced_by", "comment_authors"):
        assert col not in it


def test_ce_qui_est_ecarte_est_nomme_dans_la_reponse(feed):
    """Un défaut qui résume doit DIRE ce qu'il a rogné, sinon il cache — et le chemin
    qu'il indique existe : plus de `data_rows('linkedin-feed', …)` (oto#156)."""
    out = feed(op="feed", limit=40)
    proj = out["projection"]
    assert set(proj["omitted_fields"]) == {
        "posted_relative", "surfaced_by", "comment_authors"}
    assert proj["text_max_chars"] == 600
    assert "fields=['*']" in proj["hint"] and "text_max_chars=None" in proj["hint"]
    assert "op='get'" in proj["hint"]
    assert "data_rows" not in proj["hint"] and "linkedin-feed" not in proj["hint"]


# --- le chemin vers le brut : on ne retire rien -------------------------------

def test_le_brut_reste_atteignable_a_loctet_pres(feed):
    """`fields=["*"]` + `text_max_chars=None` = les posts d'Unipile, INTACTS (triés)."""
    out = feed(op="feed", limit=40, fields=["*"], text_max_chars=None)
    assert out["items"] == ROWS
    assert "projection" not in out, (
        "rien n'a été rogné : pas d'avertissement à poser")


def test_tous_les_champs_avec_le_texte_en_extrait(feed):
    out = feed(op="feed", limit=40, fields=["*"])
    assert set(out["items"][0]) >= set(ROWS[0]), "aucun champ perdu"
    assert out["items"][0]["text_truncated"] is True


# --- `fields` : une projection qui garde toujours l'adresse du post -----------

def test_fields_projette_et_garde_l_urn(feed):
    out = feed(op="feed", limit=40, fields=["author_name"], text_max_chars=None)
    it = out["items"][0]
    assert set(it) == {"author_name", "urn"}, (
        "la projection garde toujours de quoi ADRESSER le post (`urn`, que `op='get'` "
        "prend en `post_id`)")


def test_une_colonne_inconnue_est_signalee_sans_bloquer(feed):
    """Une faute de frappe rendrait un champ vide sans rien dire."""
    out = feed(op="feed", limit=40, fields=["auteur_name"])
    assert "auteur_name" in out["warning"]
    assert len(out["items"]) == 40, "on signale, on ne bloque pas"


def test_fields_vide_est_refuse(feed):
    """Demande ambiguë : la traiter comme « pas de projection » rendrait silencieusement
    PLUS que le défaut — l'inverse de ce que l'appelant demandait."""
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError, match="liste vide"):
        feed(op="feed", limit=40, fields=[])


def test_text_max_chars_zero_est_refuse(feed):
    """`0` est faux en Python donc « aucune limite » : exactement l'inverse de ce que
    demande qui l'écrit."""
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError, match="text_max_chars"):
        feed(op="feed", limit=40, text_max_chars=0)


# --- en direct : tri, pagination, amont ---------------------------------------

def test_la_page_est_triee_par_date_la_plus_recente_en_tete(feed, client):
    out = feed(op="feed", limit=40, fields=["posted_at"])
    dates = [it["posted_at"] for it in out["items"]]
    assert dates == sorted(dates, reverse=True)
    assert client.get_feed.return_value["items"] != ROWS, "la fixture arrive en désordre"


def test_la_pagination_est_celle_de_linkedin(feed, client):
    """Le `cursor` rendu est celui de LinkedIn, et celui qu'on passe lui est remis tel
    quel — pas de numéro de page reconstruit côté serveur."""
    out = feed(op="feed", limit=10, cursor="30|jeton")
    client.get_feed.assert_called_once_with(count=10, cursor="30|jeton",
                                            sort_order="MEMBER_SETTING")
    assert out["cursor"] == "40|jeton-suivant" and out["count"] == 40
    assert set(out) >= {"items", "cursor", "count"}
    assert not {"total", "page", "synced"} & set(out), "plus rien d'un miroir"


def test_limit_par_defaut_et_limit_nul_refuse(feed, client):
    from oto_mcp.mcp_errors import McpError
    feed(op="feed")
    assert client.get_feed.call_args.kwargs["count"] == 20
    with pytest.raises(McpError, match="limit"):
        feed(op="feed", limit=0)


def test_une_enveloppe_illisible_leve_au_lieu_de_rendre_une_page_vide(feed, client):
    """`parse_feed` rend `{items: [], _raw: …}` quand Voyager change de forme : servir
    une page vide se lirait « rien de neuf dans ton feed »."""
    from oto_mcp.mcp_errors import McpError
    client.get_feed.return_value = {"items": [], "cursor": None, "count": 0,
                                    "_raw": {"data": {}}}
    with pytest.raises(McpError, match="structure inattendue"):
        feed(op="feed")


def test_un_429_passe_par_la_discipline_de_rate_limit(feed, client, monkeypatch):
    """Comme toute lecture LinkedIn : le 429 arme le cooldown et devient le refus nommé
    `unipile_rate_limited`, avec le délai demandé par Unipile."""
    from oto.tools.unipile.client import UnipileRateLimited
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import unipile as U

    client.get_feed.side_effect = UnipileRateLimited("We only allow 10 requests")
    with pytest.raises(McpError) as e:
        feed(op="feed")
    assert e.value.error.data["code"] == "unipile_rate_limited"
    assert "sub-1" in U._RATE_LIMIT_UNTIL, "le cooldown est armé pour ce compte"


# --- oto#156 : le feed n'écrit RIEN dans le datastore -------------------------

def test_op_feed_n_ecrit_rien_dans_le_datastore(live, client, monkeypatch):
    """⚠️ Le banc de la décision du 05/10. Sur une vraie base : aucun tableau créé,
    aucune ligne écrite, aucun store ouvert — deux pages lues, rien en base."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.upsert_user("sub-1", email="sub-1@feed.invalid", name="sub-1")

    def _interdit(*a, **k):
        raise AssertionError("op='feed' ne doit ouvrir aucun store du datastore")

    monkeypatch.setattr("oto_mcp.datastore.core.make_store", _interdit)

    def _compte():
        with _connect() as conn:
            return (conn.execute("SELECT count(*) AS n FROM user_datastores").fetchone()["n"],
                    conn.execute("SELECT count(*) AS n FROM datastore_rows").fetchone()["n"])

    avant = _compte()
    feed = _tool()
    premiere = feed(op="feed", limit=40)
    feed(op="feed", limit=40, cursor=premiere["cursor"])
    assert len(premiere["items"]) == 40
    assert _compte() == avant, "le feed est servi en direct : rien n'est recopié"
    with _connect() as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM user_datastores WHERE namespace = 'linkedin-feed'"
        ).fetchone()["n"] == 0


# --- l'autre bout du même seam (`_slim`) --------------------------------------

def test_les_posts_dun_membre_ont_le_meme_defaut(monkeypatch):
    """#281 avait ajouté les paramètres à `op="posts"` sans corriger son défaut : le
    même incident s'est rejoué sur le feed. Un seul extrait par défaut pour toute la
    famille — l'agent l'apprend une fois."""
    from fastmcp import FastMCP
    from oto_mcp.tools import unipile as U

    client = MagicMock()
    client.list_member_posts.return_value = {
        "items": [{"id": "p1", "social_id": "urn:li:activity:1", "text": "x" * 5000}],
        "cursor": None,
    }
    monkeypatch.setattr(U, "unipile_client", lambda *a, **k: client)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr(U, "_rate_limit_guard", lambda sub: None)

    m = FastMCP("t")
    U.register(m)
    profile = asyncio.run(m.get_tool("linkedin_unipile_profile")).fn

    it = profile(op="posts", identifier="marie-dupont")["items"][0]
    assert len(it["text"]) == 601 and it["text_truncated"] is True

    whole = profile(op="posts", identifier="marie-dupont",
                    text_max_chars=None)["items"][0]
    assert len(whole["text"]) == 5000, "le texte entier reste à un paramètre"


# --- le texte de l'ORIGINAL d'un repost est borné lui aussi -------------------

def _shape(lignes, fields=None, text_max_chars=600):
    from oto_mcp.tools import unipile as U
    return U._shape_feed(
        {"items": [dict(r) for r in lignes], "cursor": None, "count": len(lignes)},
        fields, text_max_chars)


def test_le_texte_de_loriginal_est_borne_comme_le_texte():
    """Depuis oto-core v1.80.0 un repost porte `original_text` — le propos RÉEL, quand
    `text` ne contient que le mot du re-partageur (souvent « 👏 »). Ne borner que
    `text` laisserait celui-là passer entier, et annulerait le plafond sur précisément
    les posts où il y a le plus à lire."""
    ligne = dict(ROWS[0], urn="urn:li:activity:repost", is_repost=True,
                 text="👏", original_text="z" * 3000)
    it = _shape([ligne])["items"][0]
    assert len(it["original_text"]) == 601 and it["original_text"].endswith("…")
    assert it["original_text_truncated"] is True, "la coupe doit être marquée"


def test_un_original_court_nest_pas_marque_tronque():
    ligne = dict(ROWS[0], urn="urn:li:activity:repost2", is_repost=True,
                 text="👏", original_text="court")
    it = _shape([ligne])["items"][0]
    assert it["original_text"] == "court"
    assert "original_text_truncated" not in it


def test_le_defaut_dit_de_quoi_le_post_est_fait():
    """Sans `content_type`, un post dont tout le propos est dans l'image (texte
    « 🧐 », 2 775 réactions) est INCLASSABLE — c'est le manque qui a motivé
    oto-core v1.80.0."""
    ligne = dict(ROWS[0], urn="urn:li:activity:image", text="🧐",
                 content_type="image", content_title="Schéma d'architecture")
    it = _shape([ligne])["items"][0]
    assert it["content_type"] == "image"
    assert it["content_title"] == "Schéma d'architecture"
