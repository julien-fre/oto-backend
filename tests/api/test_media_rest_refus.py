"""Les deux POST multipart d'images — ce qu'ils REFUSENT (15/09/2026).

`oto_mcp/api/media.py` était à **14 %** de couverture le 15/09/2026, le plus bas de la
surface REST. Ce n'est pas un module mort : `POST /api/me/avatar` et
`POST /api/orgs/{id}/logo` sont servis, appelés par le dashboard, et **écrits à la
main** — ils ne passent pas par l'adaptateur de capacités (corps multipart, pas JSON :
ADR 0009). Aucune des garanties de l'adaptateur ne s'applique donc ici : ni la
validation d'entrée, ni la mise en forme des refus, ni la règle d'autz déclarée. Tout
ce qui protège ces deux routes est écrit dans ces quarante lignes-là.

Ce banc tient les deux gardes qui portent le risque réel :

1. **`_org_logo_gate`** est la SEULE chose entre un membre ordinaire et l'image de
   marque d'une org qu'il n'administre pas. Le logo est affiché à tous les membres et
   part dans les mails de l'org : le poser, c'est parler au nom de l'org. La garde
   doit refuser **avant** d'avoir touché au corps — un non-admin ne doit pas pouvoir
   faire écrire un octet dans le stockage, même refusé ensuite.
2. **`_read_upload`** : le `hasattr(upload, "read")` n'est pas une précaution de
   style. Un `<input name="file">` textuel, ou un client qui poste `file=<url>`, rend
   une CHAÎNE — `await upload.read()` lèverait alors un `AttributeError`, donc un 500
   sur une erreur d'appelant. Un 500 se retente, un 400 non.

⚠️ Les deux autres handlers du module (`avatar_clear`, `org_logo_clear`) ne sont plus
MONTÉS : la table de routes sert `DELETE /api/me/avatar` et `DELETE /api/orgs/{id}/logo`
depuis `capabilities/media_and_files.py` (portage du 2026-08-27). Ils ne sont pas testés
ici — les couvrir donnerait de la couleur à du code que personne n'atteint.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import db, media_store, org_store, roles
from oto_mcp.api import base, media

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@media.invalid", "name": sub}


class _Verifier:
    """Le bearer EST le sub — ce qu'on éprouve est en aval de l'authentification."""

    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture
def journal():
    """Ce que le module a réellement APPELÉ. Pas un mock : un carnet de bord.

    Un `MagicMock()` nu répondrait à `journal.nimporte_quoi` et ne prouverait donc
    aucune absence d'appel — or c'est exactement ce qu'on veut prouver ici (le refus
    n'a rien écrit).
    """
    return {"uploads": [], "avatars": [], "logos": [], "suppressions": []}


@pytest.fixture
def client(monkeypatch, journal):
    """Les VRAIS handlers et les VRAIES primitives de `base` (dont `_authenticate`) ;
    seuls le stockage objet, la base et le rôle sont bouchonnés."""
    # `_authenticate` touche la base à chaque requête JWT : on la neutralise sans
    # toucher au chemin d'authentification lui-même, qui est ce qu'on veut exercer.
    monkeypatch.setattr("oto_mcp.account_suspension.refus", lambda sub: None)
    monkeypatch.setattr(base, "alias_drain_armed", lambda: False)
    monkeypatch.setattr(db, "upsert_user", lambda *a, **k: None)

    monkeypatch.setattr(db, "get_user", lambda sub: {"sub": sub, "avatar_url": None})
    monkeypatch.setattr(db, "set_avatar_url",
                        lambda sub, url: journal["avatars"].append((sub, url)))
    monkeypatch.setattr(org_store, "get_org", lambda oid: {"id": oid, "logo_url": None})
    monkeypatch.setattr(org_store, "set_org_logo",
                        lambda oid, url: journal["logos"].append((oid, url)))
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, oid: sub == "admin")

    def _upload_image(prefix, owner_id, data, content_type):
        journal["uploads"].append((prefix, owner_id, len(data)))
        return f"https://media.invalid/{prefix}/{owner_id}.png"

    monkeypatch.setattr(media_store, "upload_image", _upload_image)
    monkeypatch.setattr(media_store, "delete_by_url",
                        lambda url: journal["suppressions"].append(url))

    verifier = _Verifier()
    return TestClient(Starlette(routes=[
        Route("/api/me/avatar", base.bind(media.avatar_save, verifier=verifier),
              methods=["POST"]),
        Route("/api/orgs/{id}/logo", base.bind(media.org_logo_save, verifier=verifier),
              methods=["POST"]),
    ]))


def _fichier():
    return {"file": ("logo.png", PNG, "image/png")}


def _en_tant_que(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


# ── sans jeton, rien ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("chemin", ["/api/me/avatar", "/api/orgs/7/logo"])
def test_sans_jeton_aucune_image_n_est_deposee(client, journal, chemin):
    r = client.post(chemin, files=_fichier())
    assert r.status_code == 401
    assert r.json()["error"] == "missing_bearer"
    assert journal["uploads"] == [], "un refus d'auth ne doit rien écrire au stockage"


@pytest.mark.parametrize("chemin", ["/api/me/avatar", "/api/orgs/7/logo"])
def test_un_bearer_vide_ne_passe_pas_pour_un_jeton(client, chemin):
    # `auth[7:].strip()` rend "" : sans le `if not token`, la chaîne vide descendrait
    # jusqu'au vérifieur, et un vérifieur permissif ferait le reste.
    r = client.post(chemin, files=_fichier(), headers={"Authorization": "Bearer "})
    assert r.status_code == 401 and r.json()["error"] == "missing_bearer"


# ── le logo d'org : qui a le droit de parler au nom de l'org ─────────────────

def test_un_membre_simple_ne_pose_pas_le_logo_de_son_org(client, journal):
    """La garde du lot : le logo est l'image de marque de l'org, pas celle du membre."""
    r = client.post("/api/orgs/7/logo", files=_fichier(), headers=_en_tant_que("membre"))
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"
    assert journal["logos"] == [] and journal["uploads"] == []


def test_le_refus_tombe_AVANT_que_le_corps_ne_soit_touche(client, journal):
    """Un non-admin ne fait pas transiter d'octet par le stockage, même refusé ensuite.

    L'ordre du handler est auth → gate → lecture du corps → upload. Inverser gate et
    lecture ferait payer au serveur le téléversement d'un fichier qu'il refuse, et
    offrirait à n'importe quel membre un point d'entrée pour saturer le stockage.
    """
    gros = {"file": ("gros.png", b"\x89PNG\r\n\x1a\n" + b"x" * 2_000_000, "image/png")}
    r = client.post("/api/orgs/7/logo", files=gros, headers=_en_tant_que("membre"))
    assert r.status_code == 403
    assert journal["uploads"] == []


def test_une_org_inconnue_rend_404_et_pas_403(client, monkeypatch):
    """L'ordre des refus est un contrat : id illisible (400) → org inconnue (404) →
    non-admin (403). Un 403 sur une org inexistante confirmerait son existence."""
    monkeypatch.setattr(org_store, "get_org", lambda oid: None)
    r = client.post("/api/orgs/999/logo", files=_fichier(), headers=_en_tant_que("admin"))
    assert r.status_code == 404 and r.json()["error"] == "unknown_org"


@pytest.mark.parametrize("brut", ["abc", "7bis", "1.5", "0x7", "-", "7,8"])
def test_un_identifiant_d_org_illisible_est_un_400_pas_un_500(client, brut):
    """Le chemin est `{id}` sans convertisseur : la chaîne arrive telle quelle au
    handler. Sans le `try/except`, `int()` lèverait — un 500 sur une faute d'appelant,
    que le client retente en boucle."""
    r = client.post(f"/api/orgs/{brut}/logo", files=_fichier(),
                    headers=_en_tant_que("admin"))
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_id"


def test_l_admin_pose_le_logo_et_l_url_publique_est_persistee(client, journal):
    r = client.post("/api/orgs/7/logo", files=_fichier(), headers=_en_tant_que("admin"))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "logo_url": journal["logos"][0][1]}
    assert journal["logos"] == [(7, "https://media.invalid/org-logos/7.png")]


def test_le_logo_remplace_efface_l_ancien_objet_pas_le_nouveau(client, monkeypatch,
                                                               journal):
    """Un ré-upload du MÊME contenu rend la même URL (clé par hash) : effacer « l'ancien »
    sans comparer supprimerait l'objet qu'on vient d'écrire."""
    monkeypatch.setattr(org_store, "get_org",
                        lambda oid: {"id": oid,
                                     "logo_url": "https://media.invalid/org-logos/7.png"})
    r = client.post("/api/orgs/7/logo", files=_fichier(), headers=_en_tant_que("admin"))
    assert r.status_code == 200
    assert journal["suppressions"] == [], (
        "la même URL a été réécrite : la supprimer laisserait la ligne pointer "
        "sur un objet effacé")


def test_un_logo_remplace_efface_l_ancien_objet(client, monkeypatch, journal):
    """Le pendant du test précédent : quand l'URL CHANGE, l'ancien objet doit partir.
    Sans cette branche, chaque changement de logo laisse un orphelin payant, et le
    stockage grossit d'un fichier par clic."""
    monkeypatch.setattr(org_store, "get_org",
                        lambda oid: {"id": oid,
                                     "logo_url": "https://media.invalid/org-logos/vieux.png"})
    r = client.post("/api/orgs/7/logo", files=_fichier(), headers=_en_tant_que("admin"))
    assert r.status_code == 200
    assert journal["suppressions"] == ["https://media.invalid/org-logos/vieux.png"]


def test_un_multipart_illisible_est_refuse_AUSSI_sur_la_route_du_logo(client, journal):
    """Les deux routes partagent `_read_upload`, mais chacune a son propre `if err`.
    En oublier un rendrait `data=None` à `upload_image` — donc une pile, pas un refus."""
    r = client.post("/api/orgs/7/logo", content=b"peu importe",
                    headers={**_en_tant_que("admin"),
                             "content-type": "multipart/form-data"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_multipart"
    assert journal["uploads"] == [] and journal["logos"] == []


# ── le corps multipart, écrit à la main ──────────────────────────────────────

def test_un_champ_file_textuel_est_un_400_pas_un_500(client, journal):
    """`file=<chaîne>` : sans `hasattr(upload, "read")`, `await upload.read()` lève
    un AttributeError et la route rend 500 sur une erreur d'appelant."""
    r = client.post("/api/me/avatar", data={"file": "https://ailleurs.invalid/x.png"},
                    headers=_en_tant_que("u1"))
    assert r.status_code == 400 and r.json()["error"] == "missing_file"
    assert journal["uploads"] == []


def test_un_formulaire_sans_champ_file_est_refuse(client):
    r = client.post("/api/me/avatar", data={"autre": "x"}, headers=_en_tant_que("u1"))
    assert r.status_code == 400 and r.json()["error"] == "missing_file"


@pytest.mark.parametrize("entete, corps", [
    ("multipart/form-data", b"peu importe"),                 # aucune frontière annoncée
    ("multipart/form-data; boundary=f", b"\x00\x01n-importe-quoi"),  # corps illisible
])
def test_un_multipart_illisible_est_un_400_nomme_pas_une_pile(client, journal,
                                                              entete, corps):
    """Le corps vient du réseau, donc de n'importe qui. Sans le `try` autour de
    `request.form()`, un en-tête sans frontière remonte en 500 — et un 500 se retente
    en boucle là où un 400 s'arrête."""
    r = client.post("/api/me/avatar", content=corps,
                    headers={**_en_tant_que("u1"), "content-type": entete})
    assert r.status_code == 400 and r.json()["error"] == "invalid_multipart"
    assert journal["uploads"] == []


def test_un_multipart_tronque_degrade_en_champ_absent_jamais_en_500(client):
    """Mesuré : une partie annoncée et jamais fermée ne fait PAS lever `form()` — le
    parseur rend un formulaire vide. C'est donc le `missing_file` qui rattrape, et il
    doit rester là : sans lui, `form.get("file")` vaudrait None et la suite lèverait."""
    r = client.post("/api/me/avatar", content=b"--frontiere\r\nincomplet",
                    headers={**_en_tant_que("u1"),
                             "content-type": "multipart/form-data; boundary=frontiere"})
    assert r.status_code == 400 and r.json()["error"] == "missing_file"


def test_un_refus_du_stockage_garde_son_code_et_son_statut(client, monkeypatch, journal):
    """`MediaError` porte le statut ET le jeton machine : une image de 30 Mo doit
    rendre 413/`image_too_large`, pas un 500 anonyme que le front ne sait pas expliquer."""
    def _refuse(*a, **k):
        raise media_store.MediaError(413, "image_too_large", "Image trop grande.")

    monkeypatch.setattr(media_store, "upload_image", _refuse)
    r = client.post("/api/me/avatar", files=_fichier(), headers=_en_tant_que("u1"))
    assert r.status_code == 413 and r.json()["error"] == "image_too_large"
    assert journal["avatars"] == [], "rien n'est persisté quand le stockage a refusé"


def test_le_logo_aussi_rend_le_refus_du_stockage_sans_persister(client, monkeypatch,
                                                                journal):
    """La branche jumelle de l'avatar, sur l'autre handler : deux chemins d'écriture,
    deux fois la même traduction de `MediaError` — et deux fois rien en base."""
    def _refuse(*a, **k):
        raise media_store.MediaError(500, "upload_failed", "stockage HS")

    monkeypatch.setattr(media_store, "upload_image", _refuse)
    r = client.post("/api/orgs/7/logo", files=_fichier(), headers=_en_tant_que("admin"))
    assert r.status_code == 500 and r.json()["error"] == "upload_failed"
    assert journal["logos"] == []


def test_un_type_non_image_est_refuse_par_le_stockage_et_rien_n_est_persiste(
        client, monkeypatch, journal):
    def _refuse(*a, **k):
        raise media_store.MediaError(400, "unsupported_type", "png, jpeg, gif, webp.")

    monkeypatch.setattr(media_store, "upload_image", _refuse)
    r = client.post("/api/me/avatar",
                    files={"file": ("charge.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
                    headers=_en_tant_que("u1"))
    assert r.status_code == 400 and r.json()["error"] == "unsupported_type"
    assert journal["avatars"] == []


# ── l'avatar, chemin nominal (en complément, pas en premier) ─────────────────

def test_l_avatar_est_range_sous_le_sub_du_porteur_pas_sous_un_parametre(client, journal):
    """Le propriétaire de l'objet vient du JETON. S'il venait du corps, n'importe qui
    écraserait l'avatar de n'importe qui."""
    r = client.post("/api/me/avatar", files=_fichier(), headers=_en_tant_que("u1"))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert journal["uploads"][0][:2] == ("avatars", "u1")
    assert journal["avatars"] == [("u1", r.json()["avatar_url"])]


def test_un_avatar_remplace_efface_l_ancien_objet(client, monkeypatch, journal):
    monkeypatch.setattr(db, "get_user",
                        lambda sub: {"sub": sub, "avatar_url": "https://media.invalid/vieux.png"})
    r = client.post("/api/me/avatar", files=_fichier(), headers=_en_tant_que("u1"))
    assert r.status_code == 200
    assert journal["suppressions"] == ["https://media.invalid/vieux.png"], (
        "sans cette suppression, chaque changement d'avatar laisse un orphelin payant "
        "dans le stockage")
