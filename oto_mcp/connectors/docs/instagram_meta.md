## prerequisite — a professional instagram account, and a tester invitation

oto reads the statistics of **your** instagram account: you authorize it once, at instagram, and nothing goes through facebook.

- you need a **professional account** — business or creator (instagram → settings → account type). a personal account gives access to no statistics, anywhere
- ⚠️ **until our application has passed meta's review, only accounts INVITED as testers can authorize it.** this is not a setting on your side: if your account has not been invited, instagram will refuse the authorization, and it will do so with the same message as if you had clicked "deny"
  - ask the operator of this oto instance for the invitation (at otomata: `oto@otomata.tech`), giving your **instagram username**
  - then accept it: **instagram → edit profile → apps and websites → tester invitations**
  - then come back here and click "connect"
- the authorization is valid for **60 days**. oto renews it on its own, every day, as long as it is alive — you have nothing to do. but it is only renewed **while it is alive**: if the connector is removed, or the instance is stopped for more than two months, it will have to be redone
- to revoke: instagram → edit profile → apps and websites → remove access. the card will then show "to reconnect"

## setup — the callback url to declare at meta

reserved for the instance operator. you need a **meta application** with the use case "instagram api with instagram login", the permissions `instagram_business_basic` and `instagram_business_manage_insights`, and this callback url declared to the byte:

{{callback:/api/instagram_meta/oauth/callback}}

the application's two credentials are then set at platform scope, once for the instance:

- `oto_admin_connector_setting(op="set", connector="instagram_meta", key="app_id", value="…")`
- `oto_admin_connector_setting(op="set", connector="instagram_meta", key="app_secret", value="…")`

while they are missing, the connector stays visible and the "connect" button refuses **naming the missing key**: it is then not the user's account that is at fault, and there is nothing to set again on their side.

⚠️ `app_secret` is a **real secret**: it signs the code exchange. it is never returned by an api, never written to a log, and `oto_admin_connector_setting` is reserved for platform administration. preproduction and production do not share the same callback url — both are declared at meta, otherwise a consent started from one fails with a `redirect_uri_mismatch`.

## usage — profile, posts, reach

read-only. nothing is published, modified or deleted — the permissions requested would not allow it.

- `instagram_meta_get_profile` to start: it confirms **which** account is connected (username, followers, number of posts)
- `instagram_meta_get_recent_media` lists the latest posts with their likes and comments — this is where you get the `id` to pass to insights
- `instagram_meta_get_media_insights` details **one** post: reach, views, saves, shares, interactions. the metrics depend on the type (feed post, reel, story) and are chosen for you
- `instagram_meta_get_account_insights` gives the whole account over the last N days (**30 at most** — that is the api's window, not oto's choice)
- `instagram_meta_get_best_hours` ranks the best hours and days to post based on the average engagement of the latest posts

## note — what these figures are, and are not

- **the best hours are a local heuristic**, not an instagram statistic: oto computes it over the average engagement of the posts it has just read. the `sample_size` returned with the result is there for that — a ranking over six posts is not worth a ranking over forty
- the hours are those of instagram's timestamps (**utc**): the api does not say in which timezone the account posts, and converting at random would shift the ranking without anything signaling it
- **`profile_views` and `website_clicks` no longer exist** in this variant of the api. the metric that replaces them is `profile_links_taps` (clicks on the profile links) — a narrower figure, not the same one
- these are the **statistics**, not the messages: dms have their own connector, with its own connection

## note — what oto sees, and what it does not

- oto only sees **what this account sees**: the account, and it alone, defines the scope
- the authorization carries **no write access**: even in case of error, nothing can be published in your name
- you can remove it whenever you want from instagram (apps and websites): oto will notice at the next call and the card will tell you
