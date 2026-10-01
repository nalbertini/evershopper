# API interna Everli – note di discovery

> Da compilare dopo aver lanciato `discovery/discover.py` e `discovery/summarize.py`.
> Nessun cookie, token o dato personale in questo file.

## Data della discovery
- 

## Pagina offerte del supermercato
- URL pagina:
- ID/slug del supermercato:

## Endpoint delle promozioni
- Metodo e URL:
- Parametri (store, categoria, ordinamento, lingua…):
- Header necessari (nomi soltanto, valori oscurati):
- Autenticazione: cookie di sessione / bearer / nessuna
- Paginazione: (page/offset/cursor, dimensione pagina, come si capisce che è l'ultima)

## Mappatura dei campi
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
