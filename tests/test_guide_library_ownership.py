"""Publier dans la bibliothèque est BORNÉ À L'AUTEUR (oto-backend#292).

Le nom public EST l'adresse (`UNIQUE (slug)` ; toute l'API adresse par slug, et
un `unlisted` se lit par slug exact). Il est donc possédé : `publish_guide`
faisait un `ON CONFLICT (slug) DO UPDATE` qui réécrivait corps ET auteur sans
regarder à qui appartenait l'entrée en conflit — une org devenait silencieusement
propriétaire de l'entrée d'une autre, qui perdait jusqu'au droit de la dépublier
(`_unpublish` autorise sur l'auteur COURANT).

Ce que ces tests figent :
- republier LA SIENNE marche comme avant (version + corps remplacé) ;
- publier sous le nom d'une AUTRE org est refusé, et le refus est
  **non-disclosant** (ne nomme ni l'org, ni le titre, ni le fait que ce soit
  « la doctrine de quelqu'un ») ;
- l'upsert ne peut pas transférer l'appartenance même si le garde sautait
  (`author_kind`/`author_org_id` absents du `DO UPDATE SET`) ;
- une org active est exigée AVANT l'escalade plateforme (sinon : 500 au fork,
  message faux à la publication) et l'entrée ne part jamais sans auteur affichable.
"""
from __future__ import annotations

import pytest

from oto_mcp import org_store
from oto_mcp.capabilities import guide_library as lib
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


class _R:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Library:
    """Un faux `doctrine_library` en mémoire — juste assez de SQL pour exercer le
    VRAI `publish_guide` (verrou advisory, lecture d'appartenance, upsert)."""

    def __init__(self, rows=None):
        self.rows: dict[str, dict] = dict(rows or {})
        self._next_id = 100
        self.seen: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.seen.append(s)
        if s.startswith("SELECT pg_advisory_xact_lock"):
            return _R(None)
        if s.startswith("SELECT version") and "FROM doctrine_library WHERE slug" in s:
            assert "author_kind" in s and "author_org_id" in s, \
                "sans lire l'appartenance, l'upsert écrase l'entrée d'une autre org"
            return _R(self.rows.get(params[0]))
        if s.startswith("INSERT INTO doctrine_library"):
            (slug, title, description, body_md, slots, author_kind, author_org_id,
             author_display, category, tags, visibility, source_org_id, source_slug,
             forked_from, version, published_by) = params
            prev = self.rows.get(slug)
            row = {
                "id": prev["id"] if prev else self._next_id,
                "slug": slug, "title": title, "description": description,
                "body_md": body_md, "author_display": author_display,
                "visibility": visibility, "version": version,
                # Le `DO UPDATE SET` ne touche PAS l'appartenance : une ligne
                # existante garde son auteur, quoi que demande l'appelant.
                "author_kind": prev["author_kind"] if prev else author_kind,
                "author_org_id": prev["author_org_id"] if prev else author_org_id,
            }
            if not prev:
                self._next_id += 1
            self.rows[slug] = row
            return _R(dict(row))
        raise AssertionError(f"SQL inattendu : {s}")


def _org_ctx(sub: str, org_id: int) -> ResolvedCtx:
    return ResolvedCtx(sub=sub, org_id=org_id, role="member")


@pytest.fixture
def surface(monkeypatch):
    """La surface `library.publish` avec ses seuls voisins stubbés : le store de
    doctrines d'org, les orgs, les rôles. `publish_guide` reste le vrai."""
    store = _Library()
    monkeypatch.setattr(org_store, "_connect", lambda: store)
    monkeypatch.setattr(org_store, "get_instruction",
                        lambda org_id, slug: {"body_md": f"# corps org {org_id}",
                                              "title": f"T{org_id}", "slots": []})
    monkeypatch.setattr(org_store, "get_org",
                        lambda org_id: {"id": org_id, "name": f"Org {org_id}"})
    monkeypatch.setattr(lib.access, "is_platform_operator", lambda sub: False)
    monkeypatch.setattr(lib.roles, "is_org_admin", lambda sub, org_id: True)
    return store


def _publish(sub, org_id, slug="veille-concurrence", **kw):
    return lib._publish(_org_ctx(sub, org_id), lib.PublishInput(slug=slug, **kw))


def test_une_autre_org_ne_peut_pas_publier_sous_un_nom_pris(surface):
    first = _publish("admin-a", 1)
    assert (first["version"], first["slug"]) == (1, "veille-concurrence")

    with pytest.raises(AuthzDenied) as e:
        _publish("admin-b", 2)
    assert (e.value.status, e.value.code) == (409, "slug_taken")

    # L'entrée de l'org 1 est INTACTE : corps, auteur, version.
    row = surface.rows["veille-concurrence"]
    assert (row["author_kind"], row["author_org_id"]) == ("org", 1)
    assert row["body_md"] == "# corps org 1" and row["version"] == 1


def test_le_refus_ne_dit_pas_a_qui_est_le_nom(surface):
    """Un slug `unlisted` tient lieu de lien secret : le refus ne doit pas
    confirmer l'existence d'une entrée, ni nommer son propriétaire (ADR 0023)."""
    _publish("admin-a", 1, visibility="unlisted", title="Plan de bataille")
    with pytest.raises(AuthzDenied) as e:
        _publish("admin-b", 2)
    msg = e.value.message.lower()
    assert "n'est pas disponible" in msg
    for divulgation in ("org 1", "appartient", "plan de bataille", "existe", "auteur"):
        assert divulgation not in msg, f"le refus divulgue : {e.value.message!r}"


def test_republier_la_sienne_marche_comme_avant(surface):
    assert _publish("admin-a", 1)["version"] == 1
    again = _publish("admin-a", 1, title="Titre v2", description="d2")
    assert again["version"] == 2
    row = surface.rows["veille-concurrence"]
    assert (row["title"], row["description"]) == ("Titre v2", "d2")
    assert (row["author_kind"], row["author_org_id"]) == ("org", 1)


def test_une_org_ne_reprend_pas_une_entree_de_la_plateforme(surface):
    surface.rows["socle-prospection"] = {
        "id": 7, "version": 3, "author_kind": "otomata", "author_org_id": None,
        "body_md": "# officiel", "slug": "socle-prospection",
    }
    with pytest.raises(AuthzDenied) as e:
        _publish("admin-a", 1, slug="socle-prospection")
    assert e.value.code == "slug_taken"
    assert surface.rows["socle-prospection"]["body_md"] == "# officiel"


def test_le_store_refuse_une_entree_org_sans_proprietaire(surface):
    """Sans `author_org_id`, l'entrée naît hors de portée du contrôle
    d'appartenance ET indépublicable par son auteur."""
    with pytest.raises(ValueError, match="author_org_id"):
        org_store.publish_guide(slug="orpheline", body_md="# x",
                                   author_kind="org", author_org_id=None)


def test_operateur_plateforme_sans_org_active_recoit_un_refus_lisible(monkeypatch):
    """Le gate exigeait l'org APRÈS l'escalade plateforme : `org_id=None`
    atteignait `fork_into_org` (colonne NOT NULL → 500) et rendait à la
    publication un 404 « absente de ton org active » — alors qu'il n'y a
    justement pas d'org active."""
    monkeypatch.setattr(lib.access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(org_store, "fork_into_org",
                        lambda **k: (_ for _ in ()).throw(AssertionError("ne doit PAS écrire")))
    ctx = ResolvedCtx(sub="ops", org_id=None, role="super_admin")
    for handler, inp in ((lib._publish, lib.PublishInput(slug="x")),
                         (lib._fork, lib.ForkInput(slug="x"))):
        with pytest.raises(AuthzDenied) as e:
            handler(ctx, inp)
        assert (e.value.status, e.value.code) == (400, "no_active_org")


def test_pas_de_publication_sans_auteur_affichable(monkeypatch, surface):
    """`author_display` est le seul axe de confiance du catalogue : une org sans
    nom (ou introuvable) ne publie pas d'entrée anonyme."""
    monkeypatch.setattr(org_store, "get_org", lambda org_id: {"id": org_id, "name": "  "})
    with pytest.raises(AuthzDenied) as e:
        _publish("admin-a", 1)
    assert e.value.code == "unnamed_org"

    monkeypatch.setattr(org_store, "get_org", lambda org_id: None)
    with pytest.raises(AuthzDenied) as e:
        _publish("admin-a", 1)
    assert e.value.code == "unknown_org"
    assert surface.rows == {}, "rien ne doit être publié"
