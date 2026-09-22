"""Post-traitement d'une transcription (`oto_mcp/transcript.py`) — fonctions pures.

Verrouille les deux corrections valables pour tout fournisseur (ADR 0074 D2) :
dédoublonner les segments consécutifs identiques, fusionner les locuteurs parasites
(les 2 ou 3 majoritaires gardés, les autres rattachés au voisin le plus proche) — pesés
à la durée, et au nombre de mots si un segment arrive sans horodatage — puis la forme
de la page : un paragraphe par tour, locuteur en tête.
"""
from oto_mcp import transcript


def _s(text, speaker=None, start=None, end=None):
    return {"text": text, "speaker": speaker, "start": start, "end": end}


# --- dédoublonnage -----------------------------------------------------------------

def test_segments_consecutifs_identiques_n_en_font_qu_un():
    out = transcript.dedupe([
        _s("On regarde la toiture.", "a", 0, 2),
        _s("on regarde  la toiture.", "a", 2, 4),
        _s("Et la charpente.", "b", 4, 6),
    ])
    assert [s["text"] for s in out] == ["On regarde la toiture.", "Et la charpente."]
    assert (out[0]["start"], out[0]["end"]) == (0, 4)   # couvre la durée des deux


def test_une_repetition_non_consecutive_est_gardee():
    out = transcript.dedupe([_s("Oui."), _s("Non."), _s("Oui.")])
    assert [s["text"] for s in out] == ["Oui.", "Non.", "Oui."]


# --- locuteurs parasites -------------------------------------------------------------

def test_parasite_rattache_au_voisin_le_plus_proche_dans_le_temps():
    segs = [
        _s("Bonjour, on commence par le toit.", "a", 0, 30),
        _s("Hum.", "z", 30.5, 31),            # 0,5 s après a, 9 s avant b
        _s("D'accord, et les combles ?", "b", 40, 70),
        _s("Oui.", "y", 69, 70),              # chevauche la fin de b, 10 s avant a
        _s("Les combles sont à isoler.", "a", 80, 110),
    ]
    out = transcript.merge_speakers(segs)
    assert [s["speaker"] for s in out] == ["a", "a", "b", "b", "a"]


def test_trois_locuteurs_au_plus_et_chacun_au_moins_un_dixieme():
    segs = [
        _s("x", "a", 0, 40), _s("x", "b", 40, 70), _s("x", "c", 70, 90),
        _s("x", "d", 90, 99), _s("x", "e", 99, 100),
    ]
    # d = 9 %, e = 1 % : écartés ; a, b, c ≥ 10 % : gardés.
    assert transcript.locuteurs_gardes(segs) == ["a", "b", "c"]
    quatre_lourds = [_s("x", k, i * 25, (i + 1) * 25) for i, k in enumerate("abcd")]
    assert len(transcript.locuteurs_gardes(quatre_lourds)) == 3


def test_un_seul_locuteur_reste_meme_minoritaire_partout():
    segs = [_s("x", k, i, i + 1) for i, k in enumerate("abcdefghijklmnop")]
    assert len(transcript.locuteurs_gardes(segs)) == 1


def test_sans_horodatage_le_poids_est_le_nombre_de_mots_et_le_voisin_le_precedent():
    segs = [
        _s("un deux trois quatre cinq six sept huit neuf dix", "a"),
        _s("ouais", "z"),
        _s("onze douze treize quatorze quinze seize dix-sept dix-huit", "b"),
    ]
    assert transcript.locuteurs_gardes(segs) == ["a", "b"]
    assert [s["speaker"] for s in transcript.merge_speakers(segs)] == ["a", "a", "b"]


def test_parasite_en_tete_passe_au_premier_locuteur_garde():
    segs = [_s("Allô ?", "z", 0, 1), _s("Oui, bonjour, je vous écoute.", "a", 1, 30)]
    assert [s["speaker"] for s in transcript.merge_speakers(segs)] == ["a", "a"]


def test_sans_diarisation_rien_n_est_reaffecte():
    segs = [_s("Premier."), _s("Second.")]
    assert transcript.merge_speakers(segs) == segs


# --- tours et page --------------------------------------------------------------------

def test_tours_regroupes_et_locuteurs_renommes_dans_l_ordre_d_apparition():
    tours = transcript.process([
        _s("Bonjour.", "speaker_3", 0, 5), _s("On y va.", "speaker_3", 5, 10),
        _s("Oui.", "speaker_0", 10, 15), _s("Bonjour.", "speaker_3", 15, 20),
    ])
    assert tours == [
        {"speaker": "Locuteur 1", "start": 0, "text": "Bonjour. On y va."},
        {"speaker": "Locuteur 2", "start": 10, "text": "Oui."},
        {"speaker": "Locuteur 1", "start": 15, "text": "Bonjour."},
    ]


def test_sans_locuteur_chaque_segment_reste_un_paragraphe():
    tours = transcript.process([_s("Premier."), _s("Second.")])
    assert [t["text"] for t in tours] == ["Premier.", "Second."]


def test_page_un_paragraphe_par_tour_locuteur_en_tete():
    tours = [{"speaker": "Locuteur 1", "start": 65, "text": "Bonjour."},
             {"speaker": "Locuteur 2", "start": None, "text": "Oui."}]
    corps = transcript.render(tours, filename="visite.m4a", duration_s=3725)
    assert corps.split("\n\n") == [
        "Transcription automatique de `visite.m4a` (1:02:05).",
        "**Locuteur 1** [01:05] — Bonjour.",
        "**Locuteur 2** — Oui.\n",
    ]
    assert transcript.title("visite.m4a", "2026-09-22") == \
        "Transcription — visite.m4a — 2026-09-22"
    assert transcript.word_count(tours) == 2
