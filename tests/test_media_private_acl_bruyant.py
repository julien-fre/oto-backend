"""B1 de l'inventaire des silences (27/08) : « privé » ne se dit pas quand l'ACL a échoué.

`make_public` LÈVE une `MediaError` depuis toujours ; `make_private` avalait la même
panne et rendait `None`. L'asymétrie était le bug : en fermant le partage d'un fichier,
l'ACL S3 restait `public-read` — donc l'URL permanente restait ouverte — pendant que la
base écrivait `public=false` et que l'appel rendait `{"ok": true}`. L'écran affichait
« privé », le fichier ne l'était pas.

**Ce banc a failli mourir avec sa surface.** Il éprouvait la route REST
`project_file_public`, retirée le 15/09/2026 parce que plus rien ne la montait : la
bascule était portée par la capacité depuis le 27/08. Le retrait était juste, mais ce
banc était la seule trace écrite de la propriété — et un test rouge qui appelle une
fonction supprimée se classe « périmé » en une seconde. Il est porté ici sur la
capacité, PAS supprimé.

Ce que le portage a révélé : dans la capacité, seule l'OUVERTURE était protégée. La
fermeture appelait `make_private` hors du `try`, donc son échec ne devenait pas un refus
nommé — il remontait en erreur interne. La propriété tenait encore (l'exception coupe
avant l'écriture en base, donc aucun fichier n'est dit privé en restant ouvert), mais par
accident de structure et sans que rien ne l'éprouve. Sur un geste de confidentialité,
« erreur interne » est la pire réponse à qui essaie de refermer quelque chose : il ne
sait pas s'il a réussi.

Les trois tests décrivent le SYSTÈME — ce que le seam lève, ce que la capacité refuse
dans les DEUX sens — et non l'intention.
"""
from __future__ import annotations

import pytest

from oto_mcp import media_store


class _AclRefusee:
    """Client S3 dont le `put_object_acl` échoue — la panne exacte du scénario B1."""

    def put_object_acl(self, **kw):
        raise RuntimeError("AccessDenied")


def test_make_private_leve_comme_make_public(monkeypatch):
    monkeypatch.setattr(media_store, "_get_client", lambda: _AclRefusee())
    monkeypatch.setattr(media_store, "_bucket", lambda: "b")
    with pytest.raises(media_store.MediaError) as e:
        media_store.make_private("k/abc/doc.pdf")
    assert e.value.status == 500 and e.value.code == "acl_failed"
    # Symétrie : la même panne sur la bascule inverse porte le MÊME code.
    with pytest.raises(media_store.MediaError) as pub:
        media_store.make_public("k/abc/doc.pdf")
    assert pub.value.code == e.value.code


@pytest.fixture
def bascule(monkeypatch):
    """Toutes les gardes en amont ouvertes : on n'observe QUE la bascule d'ACL.

    Rend la liste des écritures en base — vide tant que l'ACL n'a pas bougé."""
    from oto_mcp.capabilities import media_and_files as M

    monkeypatch.setattr(M, "_org_context", lambda *a, **k: None)
    monkeypatch.setattr(M.db, "get_project_file",
                        lambda fid: {"id": 1, "project_id": 7, "s3_key": "k/abc/doc.pdf",
                                     "title": "Brief", "filename": "doc.pdf"})
    import oto_mcp.ownership as ownership
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: True)
    monkeypatch.setattr(media_store, "_get_client", lambda: _AclRefusee())
    monkeypatch.setattr(media_store, "_bucket", lambda: "b")
    ecrits: list = []
    monkeypatch.setattr(M.db, "set_project_file_public",
                        lambda *a, **k: ecrits.append(a) or {"id": 1})
    monkeypatch.setattr(M.db, "log_project_activity", lambda *a, **k: None)
    return ecrits


@pytest.mark.parametrize("public, geste", [(False, "refermer"), (True, "ouvrir")])
def test_une_ACL_refusee_est_un_REFUS_nomme_et_n_ecrit_rien(bascule, public, geste):
    """Les DEUX sens, et c'est la fermeture qui manquait.

    Un refus nommé plutôt qu'une erreur interne : celui qui referme un partage et n'y
    arrive pas doit l'apprendre comme un échec de son geste, pas comme une panne dont il
    ne sait pas ce qu'elle a laissé derrière elle."""
    from oto_mcp.capabilities import media_and_files as M
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

    with pytest.raises(AuthzDenied) as e:
        M._file_public(ResolvedCtx(sub="u1", org_id=42),
                       M.ProjectFilePublicInput(project_id=7, file_id=1, public=public))

    assert e.value.code == "acl_failed", f"{geste} : le refus doit nommer l'ACL"
    assert e.value.status == 500
    # …et surtout : la base n'a RIEN enregistré sur un objet dont l'ACL n'a pas bougé.
    assert bascule == []
