"""Le datastore — spine PG de records typés (ADR 0016/0030/0046).

Package sans surface propre : chaque module s'importe par son nom.
`core` compose (le store), `schema` décrit, `schema_ops` fait évoluer, `columns`
projette, `errors` nomme les refus, `journal` trace.
"""
