"""`apps_html.avertissements` — ce que le HTML d'une app demande hors de son manifeste.

Lecture du texte, jamais une exécution : un appel écrit en clair est jugé, un appel
passé par variable ne l'est pas. Les assertions portent sur le champ et la ligne
nommés, pas sur la phrase entière.
"""
from oto_mcp.capabilities.apps_html import MAX_APPELS, MAX_WARNINGS, avertissements

M = {"tables": {
    "leads": {"table": "12", "fields": ["name", "status", "tags"]},
    "all": {"table": "13", "fields": None},
}}


def _w(html, manifest=M):
    return avertissements(manifest, html)


def test_rien_a_dire():
    html = """<script>
      oto.rows("leads", {orderBy: "name", filter: {status: "new", _created_at: {gte: d}}});
      oto.rows("leads", {orderBy: "_created_at", orderDir: "desc"});
      oto.rows('all', {orderBy: 'anything', q: 'acme'});
      oto.aggregate("leads", {groupBy: "status", metrics: [{op: "count_rows"}]});
      oto.aggregate("leads", {groupBy: "tags[]"});
    </script>"""
    assert _w(html) == []


def test_tri_non_declare_nomme_champ_et_ligne():
    html = '<p>x</p>\n<script>\noto.rows("leads", {limit: 50, orderBy: "score"})\n</script>'
    (w,) = _w(html)
    assert w.startswith("Line 3:") and '"score"' in w and "tables.leads.fields" in w
    assert "_created_at" in w


def test_filtre_regroupement_mesure_recherche():
    html = """
      oto.rows("leads", {filter: {"owner": "me", status: "x", city}});
      oto.rows("leads", {q: "acme"});
      oto.aggregate("leads", {groupBy: "stage"});
      oto.aggregate("leads", {metrics: [{op: "sum", field: "amount"}, {op: "count_rows"}]});
    """
    w = _w(html)
    assert len(w) == 4
    assert "owner, city" in w[0]
    assert "`q`" in w[1]
    assert '"stage"' in w[2]
    assert '"amount"' in w[3]


def test_table_non_declaree():
    (w,) = _w('oto.rows("contacts")')
    assert '"contacts"' in w and "leads, all" in w


def test_appel_par_variable_non_juge():
    assert _w("oto.rows(t, opts); oto.rows('leads', opts)") == []


def test_tri_hors_appel_juge_contre_tous_les_tableaux():
    # Une fonction d'aide : la table ne se lit pas, le tri si.
    m = {"tables": {"leads": {"table": "12", "fields": ["name"]}}}
    w = _w('const OPTS = {orderBy: "score"}; load("leads", OPTS);', m)
    assert len(w) == 1 and '"score"' in w[0]
    assert _w('const OPTS = {orderBy: "name"};', m) == []
    assert _w('const OPTS = {orderBy: "_updated_at"};', m) == []
    # Un tableau sans liste de champs lit tout : rien à dire.
    assert _w('const OPTS = {orderBy: "score"};') == []


def test_chaines_et_commentaires_ne_trompent_pas_l_equilibrage():
    html = """oto.rows("leads", {
      // a ) in a comment, and one in a string: ")"
      filter: {status: "a) b"},
      orderBy: "score",
    })"""
    (w,) = _w(html)
    assert '"score"' in w


def test_borne_et_sans_html():
    html = "\n".join(f'oto.rows("leads", {{orderBy: "f{i}"}})' for i in range(50))
    assert len(_w(html)) == MAX_WARNINGS
    assert _w(None) == [] and _w("") == []


def test_meme_phrase_une_fois():
    assert len(_w('oto.rows("leads", {orderBy: "x"}); oto.rows("leads", {orderBy: "x"})')) == 1


def test_seul_le_script_est_lu():
    """Le texte de la page (« don't », un `oto.rows` cité en exemple) n'est pas du
    code ; un gabarit sur plusieurs lignes ne décale rien ; la ligne reste juste."""
    html = """<p>Don't sort by oto.rows("leads", {orderBy: "nope"}) here.</p>
<script>
  const row = r => `<li>
    ${r.name}</li>`;
  oto.rows("leads", {orderBy: "score"});
</script>
<p>It's fine.</p>"""
    (w,) = _w(html)
    assert w.startswith("Line 5:") and '"score"' in w


def test_le_pont_sous_un_autre_nom():
    html = """<script>
const T = window.oto;
T.rows("leads", {orderBy: "score"});
window.oto.aggregate("leads", {groupBy: "stage"});
</script>"""
    w = _w(html)
    assert len(w) == 2 and w[0].startswith("Line 3:") and '"stage"' in w[1]


def test_au_plus_max_appels_examines():
    """La lecture est bornée : au-delà de `MAX_APPELS` appels, plus rien n'est jugé."""
    sages = "\n".join('oto.rows("leads", {orderBy: "name"})' for _ in range(MAX_APPELS))
    assert _w(sages + '\noto.rows("leads", {orderBy: "score"})') == []
    assert len(_w('oto.rows("leads", {orderBy: "score"})\n' + sages)) == 1


def test_balises_et_parentheses_jamais_fermees_ne_font_pas_tout_relire():
    """Un `<script` sans `>` et un `(` jamais fermé, répétés : chacun s'arrête vite au
    lieu de relire le reste du texte (sinon ce test ne finit pas)."""
    html = "<script" * 50_000 + '\noto.rows("leads", {orderBy: "score"})'
    (w,) = _w(html)
    assert '"score"' in w
    assert _w('oto.rows("leads", {' * 20_000) == []
