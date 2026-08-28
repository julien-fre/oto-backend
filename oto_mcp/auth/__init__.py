"""L'authentification : qui parle au serveur, et comment un credential s'ACQUIERT.

Package sans surface propre. Deux sujets qui se répondent :
- **entrant** — `hooks` (le `sub` du jeton courant), `facade` (la façade DCR devant
  Logto), `token_scopes` (la portée d'un jeton `oto_…`), `anon` (le shim OAuth des
  endpoints publics) ;
- **sortant** — `flow` (la danse `authorization_code`, écrite UNE fois), `pkce` (les
  helpers state/PKCE), puis un module par fournisseur : `atlassian`, `folk`,
  `google`, `salesforce`, `zoho`.

Une famille se reconnaît ici au SUFFIXE (`*_oauth`) autant qu'au préfixe — cf.
`docs/conventions.md` §« Où vit un fichier ».
"""
