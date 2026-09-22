"""Le worker d'extraction (#298, barreau 3) — la boucle qui vide la file.

Deux propriétés valent tout le reste ici, et aucune ne se lit dans le code :

1. **un fichier ne peut pas bloquer la file** — quoi qu'il arrive à l'un, les autres
   passent, et il ne revient pas indéfiniment ;
2. **la boucle est SÉPARÉE de l'indexation sémantique** — parce que l'extraction ne
   dépend d'aucun service tiers, alors que `run_embed_loop` sort d'emblée sans clé
   Mistral. Les greffer ensemble rendrait l'extraction muette sur un déploiement sans
   clé, sous le symptôme « la recherche ne trouve rien », sans erreur nulle part.

Le SQL de la file est couvert par `test_project_file_texts.py` (vrai PostgreSQL) ; ici
on exerce l'orchestration, par stubs — c'est la LOGIQUE qui est en jeu.
"""
from __future__ import annotations

import pytest

from oto_mcp import file_extract, file_extract_worker as w


class _FauxDb:
    """Un store minimal : la file d'un côté, ce qui a été écrit de l'autre."""

    def __init__(self, fichiers):
        self.file = list(fichiers)
        self.ecrits = []

    def files_pending_extraction(self, limit=20):
        return self.file[:limit]

    def save_extracted_text(self, file_id, *, status, text="", pages=None, detail=""):
        self.ecrits.append({"file_id": file_id, "status": status,
                            "text": text, "pages": pages, "detail": detail})
        # Comme en vrai : écrire un résultat sort le fichier de la file.
        self.file = [f for f in self.file if int(f["id"]) != int(file_id)]


def _fichier(fid, nom="notes.txt", mime="text/plain"):
    return {"id": fid, "project_id": 1, "s3_key": f"p/{fid}", "filename": nom,
            "mime": mime, "size_bytes": 1000}


@pytest.fixture
def store(monkeypatch):
    """Deux fichiers lisibles et une image — le lot mixte du régime nominal.

    ⚠️ Les noms sont COHÉRENTS avec les octets que le stub sert (du texte). Un premier
    jet nommait ces fichiers `.pdf` tout en leur servant du texte brut : le lot rendait
    alors des `failed` parfaitement légitimes, et le test accusait le worker d'un
    défaut qui était le sien."""
    faux = _FauxDb([_fichier(1), _fichier(2, "brief.md", "text/markdown"),
                    _fichier(3, "capture.png", "image/png")])
    monkeypatch.setattr(w, "db", faux)
    return faux


def _octets(monkeypatch, par_cle=None, leve=None):
    """Stub du stockage — le worker l'importe paresseusement, on patche le module."""
    from oto_mcp import media_store

    def _fetch(key, **k):
        if leve is not None:
            raise leve
        return (par_cle or {}).get(key, b"du texte parfaitement lisible et assez long")

    monkeypatch.setattr(media_store, "fetch_object", _fetch)


# ── le tour nominal ──────────────────────────────────────────────────────────

def test_a_batch_empties_the_queue(store, monkeypatch):
    _octets(monkeypatch)
    traites, extraits = w._extract_batch()

    assert traites == 3
    assert {e["file_id"] for e in store.ecrits} == {1, 2, 3}
    assert store.file == []


def test_refusals_are_written_too_or_the_file_never_leaves(store, monkeypatch):
    """Le point de conception du barreau 2, exercé par le worker : un refus s'écrit,
    sinon le fichier revient à chaque tour pour un travail qui ne réussira jamais."""
    _octets(monkeypatch, {"p/3": b"\x89PNG\r\n\x1a\n" + b"0" * 100})
    w._extract_batch()

    png = next(e for e in store.ecrits if e["file_id"] == 3)
    assert png["status"] == file_extract.UNSUPPORTED
    assert store.file == [], "même refusé, un fichier sort de la file"


def test_extracted_count_is_not_the_processed_count(store, monkeypatch):
    """« 40 fichiers traités » ne veut pas dire « 40 fichiers devenus cherchables » —
    les deux chiffres sont distincts pour que le journal ne mente pas."""
    _octets(monkeypatch, {"p/3": b"\x89PNG\r\n\x1a\n" + b"0" * 100})
    traites, extraits = w._extract_batch()

    assert traites == 3 and extraits == 2


# ── un fichier ne bloque pas la file ─────────────────────────────────────────

def test_a_storage_failure_is_retryable_not_terminal(store, monkeypatch):
    """Une erreur de TÉLÉCHARGEMENT n'est pas un format non supporté : le stockage
    peut revenir. C'est donc `failed`, reprenable — et borné par `attempts` côté
    base, pas par une boucle infinie."""
    _octets(monkeypatch, leve=RuntimeError("stockage indisponible"))
    traites, extraits = w._extract_batch()

    assert traites == 3 and extraits == 0
    assert {e["status"] for e in store.ecrits} == {file_extract.FAILED}
    assert file_extract.is_retryable(file_extract.FAILED)


def test_one_exploding_file_does_not_stop_the_others(store, monkeypatch):
    """La ceinture : même si le traitement d'un fichier lève de façon imprévue, les
    suivants passent. Un fichier qui bloque la file la bloque pour tout le monde."""
    _octets(monkeypatch)
    vrai = w._extract_one

    def _explose(f):
        if int(f["id"]) == 1:
            raise RuntimeError("imprévu")
        return vrai(f)

    monkeypatch.setattr(w, "_extract_one", _explose)
    traites, extraits = w._extract_batch()

    assert traites == 3, "le lot est compté en entier"
    assert {e["file_id"] for e in store.ecrits} == {2, 3}, "les autres ont été traités"


def test_a_3_mb_file_is_read_under_the_project_file_cap(monkeypatch):
    """La lecture passait `fetch_object` SANS `max_bytes` : elle retombait sur le
    plafond d'une IMAGE (2 Mo), si bien que tout fichier de projet plus gros finissait
    `failed / object_too_large` — alors que le dépôt en accepte 25. Le vrai
    `fetch_object` est exercé ici, seul le client S3 est un faux."""
    import io

    from oto_mcp import media_store

    texte = b"une ligne de texte lisible\n" * (3 * 1024 * 1024 // 27 + 1)
    assert len(texte) > 3 * 1024 * 1024

    class _S3:
        def head_object(self, **kw):
            return {"ContentLength": len(texte)}

        def get_object(self, **kw):
            return {"Body": io.BytesIO(texte)}

    monkeypatch.setattr(media_store, "_get_client", lambda: _S3())
    monkeypatch.setattr(media_store, "_bucket", lambda: "media-test")
    monkeypatch.delenv("OTO_MCP_S3_MAX_IMAGE_BYTES", raising=False)
    monkeypatch.delenv("OTO_MCP_UPLOAD_MAX_BYTES", raising=False)
    faux = _FauxDb([_fichier(1)])
    monkeypatch.setattr(w, "db", faux)

    assert w._extract_one(_fichier(1)) == file_extract.OK, faux.ecrits
    assert faux.ecrits[0]["detail"] != "object_too_large"


def test_an_empty_queue_is_a_cheap_no_op(monkeypatch):
    monkeypatch.setattr(w, "db", _FauxDb([]))
    assert w._extract_batch() == (0, 0)


# ── la boucle est bien séparée de l'indexation sémantique ────────────────────

def test_the_loop_is_registered_independently_of_the_embed_worker(monkeypatch):
    """⚠️ L'invariant qui motive tout ce module. `embed_worker.run_embed_loop` sort
    d'emblée sans `MISTRAL_API_KEY` ; l'extraction, elle, ne dépend d'aucun service
    tiers. Les deux boucles doivent donc être montées séparément — sinon un
    déploiement sans clé perd la recherche de fichiers en silence.

    On le vérifie sur la COMPOSITION (`boucles_de_fond`, où le montage vit depuis le
    10/09/2026) plutôt que sur un texte : chaque boucle y a son entrée et son
    interrupteur, et éteindre l'une ne doit pas éteindre l'autre. Un futur
    « factorisons les deux boucles » doit faire rougir quelque chose."""
    from oto_mcp import boucles_de_fond, embed_worker

    monkeypatch.setenv("OTO_ENV", "prod")
    monkeypatch.delenv("OTO_SENTRY_ENV", raising=False)
    monkeypatch.delenv("OTO_FILE_EXTRACT_WORKER_ENABLED", raising=False)
    monkeypatch.setenv("OTO_EMBED_WORKER_ENABLED", "0")
    composees = boucles_de_fond.composer()
    assert w.run_extract_loop in composees
    assert embed_worker.run_embed_loop not in composees

    monkeypatch.setenv("OTO_EMBED_WORKER_ENABLED", "1")
    monkeypatch.setenv("OTO_FILE_EXTRACT_WORKER_ENABLED", "0")
    composees = boucles_de_fond.composer()
    assert embed_worker.run_embed_loop in composees
    assert w.run_extract_loop not in composees, (
        "la boucle doit avoir SON opt-out, pas partager celui de l'embedding")


def test_the_worker_never_imports_embeddings():
    """Corollaire vérifiable : rien dans ce module ne doit dépendre du sémantique —
    sans quoi le couplage reviendrait par l'import plutôt que par le montage."""
    from pathlib import Path
    src = Path(w.__file__).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "embeddings" not in code.split('"""')[-1]
