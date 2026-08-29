# Browser automation — substrat hébergé Browserbase (ADR 0026)

> Extrait du CLAUDE.md (refactor 2026-07-02) — domicile du détail ; le CLAUDE.md garde le résumé + pointeur.


⚠️ **Plus de browser in-process sur la box, plus de délégation à un conteneur
o-browser-full** (`OBROWSER_URL`/`RemoteBrowser` = 0 référence — jamais portée). Le
harnais browser server-side = **service navigateur HÉBERGÉ Browserbase**
(`oto_mcp/browserbase.py`, seam à sens unique ADR 0004), réutilisable par tout
connecteur d'**API privée cookie-bound**. État réel (2026-06-24) :

- **LinkedIn** : `tools/linkedin.py` **supprimé** — passe par **Unipile** (hébergé,
  connecteur `unipile`). Le browser LinkedIn local ne survit que dans oto-cli
  (fallback) et oto-core (`oto.tools.browser.linkedin/`).
- **Substrat `browserbase.py`** : Chrome HÉBERGÉ (off-box → anti-OOM) + **Context**
  per-user (profil persistant = la session loguée, = le credential coffre) + **Live
  View** pour le login interactif (l'user gère SSO/captcha/2FA — pas d'export de
  cookie, la session naît native, zéro `li_at` kill). Exécution = `run_fetch(ctx,
  method, path, body, *, base, app)` : ouvre une session éphémère sur le Context, charge
  une page `app` puis exécute un `fetch(base+path)` **same-origin** (la session vivante
  porte les cookies). `base`/`app` sont **propres au connecteur** (le substrat n'en
  hardcode aucun). Creds plateforme env `BROWSERBASE_API_KEY`/`BROWSERBASE_PROJECT_ID`.
- **Connexion = depuis le DASHBOARD** (voie produit, pas MCP) : bouton « Connecter »
  → Live View Browserbase **en iframe** ; l'user se logue ; « vérifier » persiste le
  Context. Servie en REST `POST /api/me/connectors/{name}/session/{start,finalize}`
  ET en MCP (`<name>_connect_start`/`_connect_status`) par **un seul corps de logique**
  (`browser_session.py`, seam : `start()` générique + `finalize()` avec **verify
  par-connecteur enregistré** — brevo=cookie `auth`, crunchbase=sonde API ; les tools
  MCP ne sont que de minces délégations). ⚠️ **Un connecteur browser-session s'enregistre
  avec une `login_url`** (`browser_session.register(name, verify, login_url=…)`) : `start()`
  amène la session **sur cette page** avant d'afficher la Live View (best-effort). Sans elle,
  la Live View reste sur `about:blank` (l'user ne sait pas où se loguer — vécu pennylaneged
  2026-07-01). **Sécu** : la session émise est liée au `sub`
  (`_PENDING`, anti-IDOR — `finalize` refuse un Context tiers) et **aucune exception
  brute n'est renvoyée** (l'URL CDP porte `?apiKey=…` → loggué, message propre). L'état
  (`configured` + `session_set_at`) sort dans `me.providers[name]` via `status_for`
  (les connecteurs `secret_kind="cookie"`) — ADR 0026 avait retiré `me.crunchbase` sans
  jamais câbler ce relais (UX cassée bout-en-bout, corrigé 2026-06-30). Déconnexion =
  DELETE générique `/api/settings/api-keys/{name}` (byo_user, plus de route dédiée).
- **Connecteurs sur le substrat** (tous deux : Live View ci-dessus, Context au coffre,
  family dérivée=`api`, plus aucun browser local) :
  - **`brevo`** (`tools/brevo.py`) — automations marketing via l'API privée
    `workflow-apis.brevo.com/v1` (cookie `auth` httpOnly). **Prouvé 200** le 2026-06-24.
  - **`crunchbase`** (`tools/crunchbase.py`) — fiches société/personne via l'API privée
    du frontend `www.crunchbase.com/v4/data` (schéma v4 sans `user_key` ; lookup
    `entities/organizations|people/{slug}` + cards `founders`/`raised_funding_rounds`,
    recherche via `autocompletes`). **Migré du scraping DOM in-process** (ADR 0026,
    `BROWSER_PROVIDERS` désormais vide, plus de `CrunchbaseClient` o-browser ni de Chrome
    sur la box). ⚠️ **Reste à smoke en live** (Browserbase + login crunchbase réel) :
    confirmer `field_ids`/`card_ids`/`collection_ids` et l'absence de header anti-CSRF.
  - **`pennylaneged`** (`tools/pennylaneged.py`, issue otomata-private#31) — GED (DMS)
    Pennylane via l'API interne de la SPA (`/companies/{cid}/dms/…`, CSRF tournant lu
    in-page à chaque appel via `browserbase.run_page_eval` — l'eval générique, `run_fetch`
    ne suffit pas). Upload = control plane seul (URL S3 présignée), les octets PUT **en
    local** (RGPD). **GED cible (une par client)** : `company_id` optionnel sur tous les
    tools, défaut = la société choisie via le **sélecteur d'identité générique** (ADR
    0024) — backend enregistré par `connector_identities.register()` (patron
    `browser_session`), identités = les sociétés du cabinet (`/crm/flow_companies`),
    sélection validée anti-binding (tree 200 sur LA session) puis mémorisée au `meta` du
    credential (`default_identity_id`/`default_identity_label`, exposés par `status_for`
    → picker de la carte dashboard sans louer de session ; `identities` au catalogue).
    ⚠️ **Reste à smoker en live** (login Pennylane réel + forme exacte de
    `flow_companies`).

## Connecteur `browser` — générique, N sites derrière login (oto-private#79)

Les trois connecteurs ci-dessus sont écrits **en dur pour un site** : ce coût se justifie
quand on exploite une API privée en profondeur (la GED Pennylane), pas pour **lire**
ponctuellement N sources authentifiées (veille : chaque nouveau média payant redemanderait
un cycle de dev). D'où un connecteur **générique** (`tools/browser.py`, registre `browser`)
qui expose le substrat tel quel.

- **Un site = un compte du coffre.** `account` = le host normalisé (`www.` retiré, casse et
  port normalisés) → **un Context Browserbase par site**, sessions isolées, jamais un profil
  fourre-tout mélangeant les credentials de N sites dans un seul secret. Mécanisme : le
  multi-compte existant (`Connector.cardinality="multi"`, ADR 0011/0024) — donc le
  picker d'identités du dashboard marche **sans code dédié** (backend keyed générique de
  `connector_identities`), et un `account` explicite introuvable **lève** côté `access`
  (jamais de repli muet sur le Context d'un autre site).
- **Tools** : `browser_connect_start(url)` (Live View sur l'URL demandée) →
  `browser_connect_status(context_id, session_id, site, force?)` → `browser_sites()` ;
  lecture `browser_fetch(url, as_html?, max_chars?)` ; échappatoire `browser_eval(url, js)`.
- **Périmètre de projet (#605, 2026-08-29)** : sous un projet à `excluded_url_prefixes`,
  `browser_fetch`, `browser_eval` et `web_read` **refusent** une URL correspondante — demandée,
  ou atteinte par redirection (`final_url` observée) — en nommant le motif et le projet. Sans
  projet ou sans option, rien ne change. Détail : `docs/projects.md`.
- **`browser_fetch` ≠ `run_fetch`.** `run_fetch` vise une API JSON et **tronque son repli
  texte à 400 caractères** — inutilisable pour lire une page. `browserbase.fetch_page()`
  charge l'URL et renvoie le contenu **complet** (`innerText` rendu, ou DOM si `as_html`),
  avec `status`/`final_url`/`title` ; la troncature éventuelle est **dite** (`truncated`),
  jamais silencieuse.
- **Vérification du login : générique, donc faillible.** Un connecteur dédié sonde une route
  authentifiée qu'il connaît ; ici on ne sait rien du site. Seul signal lisible partout :
  « la session porte-t-elle des cookies sur ce host ? » (`browserbase.host_cookies`). 0 ⇒
  presque sûrement pas logué ; >0 ne prouve rien. D'où **`force=true`** sur
  `browser_connect_status` (sites dont l'état de login vit en localStorage) — refusé par le
  seam pour un connecteur à site unique, dont le verify est une vraie sonde.
- **Seam étendu, pas dupliqué** : `browser_session.register(..., account_aware=True)` (verify
  `(session_id, account)`), `start(..., login_url=…)` (le site vient de l'appel),
  `finalize(..., account=…, force=…)`. Les trois connecteurs à site unique sont inchangés.
  Côté REST, `POST /api/me/connectors/{name}/session/start?url=` et `…/finalize`
  (`account`/`force` au body) servent la même chose au dashboard. ⚠️ La **carte dashboard**
  (saisir l'URL d'un site à connecter) reste à faire côté `oto-dashboard`.
- **`browser_eval` = JS arbitraire sur une session loguée** : contenu tant qu'il est borné à
  un connecteur écrit en dur, il devient pointable n'importe où sur le générique → **masqué
  par défaut** (`DEFAULT_HIDDEN_TOOLS`, self-activable). Le vrai gate reste le connecteur.
- **Coût** : 1 session navigateur **par appel** (hérité du substrat). Adapté au delta de
  veille (quelques pages) ; un backfill de centaines de pages = autant de sessions →
  réutiliser une session pour N appels d'un même run reste à faire si le volume le justifie.

- **Leçons empiriques (toujours valides)** : (1) un `httpx`/curl brut est **rejeté
  (403)** — transport obligatoirement **browser-driven** (`page.evaluate(fetch())`) ;
  (2) une session **ne se transplante pas** par export de cookie (le faux négatif « auth
  cookie missing » venait d'une extraction sur **profil déconnecté**) → login-en-place
  via Live View ; (3) capter/vérifier sur une session **vivante** (fetch sanity = 200).

## LinkedIn — cookies & isolation de session


⚠️ **Isolation de session (constaté 2026-06-04, issue #5 ouverte)** : injecter le
cookie `li_at` d'un user **côté serveur** (IP datacenter ≠ son IP) **déconnecte sa
propre session LinkedIn** (LinkedIn invalide/rotate le `li_at` partagé). Le vrai
Chrome règle l'empreinte TLS mais PAS ce partage de session. → l'outreach par un
user réel doit passer par une **session dédiée** (profil/VNC côté serveur, ou CLI
local sur son device), pas par son cookie injecté côté serveur. ✅ Le scraping
serveur est désormais **profil-only** (fallback cookie **supprimé**) et délégué au
conteneur (voir §Browser automation). #5 reste ouvert pour le pairing/CLI local.

Le couple `(li_at, user_agent)` est stocké par `sub` en PG. Le UA
matche le browser d'origine (capturé via `navigator.userAgent` au moment du
save) — sinon LinkedIn flag rapidement les sessions cookie/UA mismatch.

Si le user n'a rien configuré, les tools `linkedin_*` lèvent une `McpError`
qui pointe vers `https://app.oto.ninja/`.

Pour les non-tech : extension Chrome Oto Companion (repo `oto-app/extension/`,
MV3) qui capture le couple `(li_at, user_agent)` et le push automatiquement
via `POST /api/settings/linkedin` (auth Logto PKCE). Auto-resync via
`chrome.cookies.onChanged` quand LinkedIn rotate la session.

## Ce que la carte en résumait (migré le 2026-08-27)

Plus AUCUN browser sur la box : les connecteurs d'**API privée cookie-bound**
(`brevo`, `crunchbase`, `pennylaneged`) passent par **Browserbase** (Chrome hébergé,
Context per-user = la session loguée au coffre, Live View pour le login interactif,
`run_fetch` same-origin). Connexion = dashboard (`browser_session.py`, un seul corps
REST+MCP, `login_url` obligatoire au register). LinkedIn = **Unipile** (tools/linkedin
supprimé) ; l'injection de cookie `li_at` côté serveur déconnecte l'user (#5).
S'y ajoute le connecteur **générique `browser`** (oto-private#79) : **lire** N sites
derrière login sans un connecteur par site — **un site = un compte du coffre** (host,
donc un Context par site), `browser_fetch` rend la page **complète** (≠ `run_fetch`,
tronqué à 400 c.), verify générique = cookies sur le host (+ échappatoire `force`),
`browser_eval` masqué par défaut.
