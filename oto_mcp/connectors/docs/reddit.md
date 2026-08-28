## usage — recherche & lecture reddit

lecture reddit avec **métriques d'engagement** (score, nb de commentaires, ratio de votes, date de publication) via la passerelle redditapis.com — clé plateforme partagée par défaut (BYO possible).
- `reddit_subreddit` — posts d'un subreddit (sort hot/new/top/rising), triables par traction ; pagination via `after`
- `reddit_search` — recherche de posts, globale ou dans un subreddit
- `reddit_search_subreddits` — trouve les subreddits pertinents pour un sujet (avec nb d'abonnés)
- `reddit_post` — lit un post et son **arbre de commentaires imbriqué**
