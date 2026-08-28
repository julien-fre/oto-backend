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
- flux avoir supervisé : `pennylane_ref(kind="products")` (résoudre le `product_id`, jamais le deviner) → `pennylane_invoice(op="find")` (anti-doublon) → `pennylane_invoice(op="credit_note")` (brouillon **standalone**, lignes en positif — la négativation « avoir » est appliquée côté serveur) → `pennylane_invoice(op="finalize")` puis `op="send"` **après validation humaine**
