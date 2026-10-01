# API interna Everli – note di discovery

> Da compilare dopo aver lanciato `discovery/discover.py` e `discovery/summarize.py`.
> Nessun cookie, token o dato personale in questo file.
> Quanto emerge va poi riportato in `config.yaml` → `everli.endpoint` (vedi `config.example.yaml`).

## Data della discovery
- 

## Pagina offerte del supermercato
- URL pagina:
- ID/slug del supermercato:

## Endpoint delle promozioni
- Metodo e URL:
- Parametri (store, categoria, ordinamento, lingua…):
- Header necessari (nomi soltanto, valori oscurati):
- Autenticazione: cookie di sessione / bearer in localStorage (nome chiave) / nessuna → `endpoint.auth`
- Paginazione: (page/offset/cursor, dimensione pagina, come si capisce che è l'ultima)

## Mappatura dei campi (→ `endpoint.fields`, percorsi a punti)
- Percorso della lista prodotti (→ `endpoint.items_path`):
- Prezzi in euro o in centesimi (→ `price_divisor`):

| Campo spec       | Campo JSON |
|------------------|------------|
| nome prodotto    |            |
| marca            |            |
| formato          |            |
| prezzo pieno     |            |
| prezzo scontato  |            |
| % sconto         |            |
| validità         |            |
| ID / URL prodotto|            |

## Stima delle richieste per esecuzione
- Offerte totali:  · per pagina:  → richieste:

## Fallback DOM (solo se manca un endpoint JSON)
- Selettori:

## Durata della sessione / segni di scadenza
- Come si presenta una sessione scaduta (status 401/403, redirect al login…):
