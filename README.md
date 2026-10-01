# evershopper

Incrocia le offerte Everli del supermercato abituale con la lista della spesa in Promemoria.
Gira solo in locale, su macOS. Requisiti e fasi sono in [SPEC.md](SPEC.md).

## Stato
- [x] Fase 1 – strumenti di discovery (`discovery/`)
- [ ] Fase 1 – esito documentato in `docs/everli-api.md`
- [x] Fase 2 – fetch offerte + cache JSON (endpoint da definire in `config.yaml`)
- [x] Fase 3 – lettura Promemoria (helper Swift da compilare sul Mac)
- [ ] Fase 4 – matching
- [ ] Fase 5 – output e notifiche
- [ ] Fase 6 – launchd + Portachiavi

## Fase 1: discovery (sul Mac)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python discovery/discover.py     # browser visibile: login a mano, poi pagina offerte
python discovery/summarize.py    # elenco endpoint, ★ = candidati offerte
```

Durante `discover.py`:
1. fai login a mano (solo la prima volta; la sessione viene salvata in `.state/everli-session.json`);
2. scegli il supermercato abituale e apri la sezione offerte/promozioni;
3. scorri e passa alla pagina successiva, così da catturare anche la paginazione;
4. premi INVIO nel terminale.

Poi compila `docs/everli-api.md` con quanto emerge da `summarize.py`
(oppure chiedi a Claude Code di farlo leggendo l'ultima cattura).

## Fase 2: fetch offerte

L'endpoint non è scritto nel codice: si descrive in `config.yaml` (sezione `everli.endpoint`:
URL, parametri, paginazione, percorso della lista e mappatura dei campi).

```bash
cp config.example.yaml config.yaml          # e completa le voci DA DEFINIRE
# prova la mappatura sulle risposte salvate dalla discovery, senza contattare Everli:
python -m evershopper fetch --no-cache --from-json discovery/captures/bodies-*/0012.json
python -m evershopper fetch                 # esecuzione vera: cache in cache/offers-AAAA-MM-GG.json
python -m evershopper show                  # rilegge l'ultima cache
python -m pytest -q                         # test senza rete
```

Comportamento:
- richieste in sequenza, pausa casuale di 2–6 s tra una e l'altra, al massimo `limits.max_requests` (30);
  se si arriva al tetto si tengono le pagine già scaricate;
- 401/403, redirect o pagina HTML → sessione scaduta: notifica macOS, uscita con codice 2, nessun nuovo tentativo;
- 429 o errori del server → ci si ferma subito, nessun nuovo tentativo;
- cache: `cache/offers-DATA.json` (offerte normalizzate, ordinate per sconto) + `cache/raw-DATA/` (risposte grezze);
- log in `logs/evershopper.log` (rotazione 3 × 500 KB), senza header né cookie.

## Fase 3: lista della spesa da Promemoria

Legge, in sola lettura, le voci non completate della lista `reminders.list` (default "Spesa").

```bash
sh helpers/build.sh                    # compila bin/reminders-helper e bin/EvershopperReminders.app
python -m evershopper reminders --lists   # la prima volta macOS chiede l'accesso per «Evershopper Promemoria»
python -m evershopper reminders        # elenco delle voci da comprare
python -m evershopper reminders --json
```

- Backend `auto`, nell'ordine: `app` (helper dentro un'app invisibile lanciata con `open`, che chiede
  il permesso a nome proprio: funziona da qualsiasi terminale e da launchd), `eventkit` (helper
  lanciato direttamente: il permesso è quello del terminale, e molti terminali vengono rifiutati
  senza mostrare la richiesta), `jxa` (`osascript`, senza compilazione, permesso di Automazione).
- Permesso negato → notifica; si riabilita in Impostazioni di Sistema → Privacy e sicurezza → Promemoria.
  Per far ricomparire la richiesta: `tccutil reset Reminders it.evershopper.reminders-helper`.
- Lista inesistente → errore con l'elenco delle liste disponibili.

## Privacy e termini d'uso
- La password non passa mai dallo script: il login si fa nel browser.
- `.state/` (cookie di sessione) e `discovery/captures/` sono in `.gitignore` e restano sul Mac.
  Nelle catture gli header con cookie/token sono oscurati.
- Uso personale, poche richieste, a ritmo umano e senza parallelismo, come richiesto dai termini Everli.
