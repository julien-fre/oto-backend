## prerequisite — ta clé api pennylane

chaque utilisateur pose sa propre clé pennylane — ta compta n'est visible que par toi.
- connecte-toi sur [app.pennylane.com](https://app.pennylane.com)
- va dans les paramètres, section api / intégrations, et crée une clé api (token personnel)
- colle-la dans tes clés de connecteur oto sous `pennylane`

## usage — lire et lettrer ta compta

interroge factures, transactions et balance, et solde les paiements non rapprochés.
- `pennylane_trial_balance` la balance comptable sur une période, `pennylane_ref(kind="ledger_accounts")` le plan comptable
- `pennylane_invoice(op="list")` / `pennylane_supplier_invoice(op="list")` les factures, `pennylane_transactions` les mouvements bancaires
- `pennylane_match` lettre une transaction avec sa facture (réversible) pour ne pas laisser une facture payée en `late`
- devis : `pennylane_quote(op="create")` (pas de brouillon : il naît `pending`) → `op="pdf"` pour le lien du PDF à joindre à un mail (lien qui expire, à relire juste avant) → `op="set_status"` (`accepted` à la signature) → `op="to_invoice"` crée la facture en **brouillon**, que `pennylane_invoice(op="finalize")` puis `op="send"` émettent **après validation humaine**
- plusieurs instances pennylane dans une org (perso et société) : sans `_instance`, la clé personnelle répond d'abord — pour deviser ou facturer au nom de la société, passer `_instance="org:<id>:pennylane"`
- flux avoir supervisé : `pennylane_ref(kind="products")` (résoudre le `product_id`, jamais le deviner) → `pennylane_invoice(op="find")` (anti-doublon) → `pennylane_invoice(op="credit_note")` (brouillon **standalone**, lignes en positif — la négativation « avoir » est appliquée côté serveur) → `pennylane_invoice(op="finalize")` puis `op="send"` **après validation humaine**

## note — périmètre de projet (#605, 2026-08-29)

`pennylane_upload_file` avec une source `{kind: "url"}` lit cette url côté serveur : sous un projet à `excluded_url_prefixes`, une url correspondante est refusée en nommant le motif (seam `file_source`). détail : `docs/projects.md`.
