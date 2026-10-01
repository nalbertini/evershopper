# evershopper

Incrocia le offerte Everli del supermercato abituale con la lista della spesa in Promemoria.
Gira solo in locale, su macOS. Requisiti e fasi sono in [SPEC.md](SPEC.md).

## Stato
- [x] Fase 1 – strumenti di discovery (`discovery/`)
- [ ] Fase 1 – esito documentato in `docs/everli-api.md`
- [x] Fase 2 – fetch offerte + cache JSON (endpoint da definire in `config.yaml`)
- [x] Fase 3 – lettura Promemoria (helper Swift da compilare sul Mac)
- [x] Fase 4 – matching (locale + seconda passata opzionale con Claude)
- [x] Fase 5 – output e notifiche (comando `run`)
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
- Più liste con lo stesso nome (account diversi) → vengono unite, con un avviso; per sceglierne una
  copia l'id mostrato da `reminders --lists` in `reminders.list_id`.
- Senza attivare l'ambiente virtuale si usa `.venv/bin/python -m evershopper …`.

## Fase 4: abbinamento lista × offerte

```bash
.venv/bin/python -m evershopper match            # offerte dall'ultima cache, lista da Promemoria
.venv/bin/python -m evershopper match --offers-json cache/offers-AAAA-MM-GG.json --reminders-json lista.json
.venv/bin/python -m evershopper match --llm      # fa decidere i dubbi a Claude
```

Prima passata, locale:
- normalizzazione (minuscole, accenti, unità e quantità tolte) e radici singolare/plurale;
- tutte le parole della voce devono comparire nel prodotto (tollerati piccoli errori di battitura);
- "latte" in "Cioccolato **al** latte" o "caffè" in "Gelato **gusto** caffè" → **dubbio**;
- sinonimi ed esclusioni di base in `evershopper/data/sinonimi.yaml`, ampliabili in
  `config.yaml` → `matching.synonyms` / `matching.exclude` (si aggiungono, non sostituiscono);
- le note del promemoria ("intero") ordinano le offerte, non le escludono.

Seconda passata, opzionale (`matching.llm.enabled: true` oppure `--llm`): una sola richiesta all'API
Claude con le voci in dubbio e i loro candidati, risposta in JSON vincolato da uno schema. Si inviano solo
i nomi di voci e prodotti; le risposte sono in cache, quindi rilanciare non costa. Se la chiave manca o
l'API fallisce restano i risultati della prima passata.

La chiave API va nel Portachiavi (la chiede senza mostrarla):
```bash
security add-generic-password -s evershopper -a anthropic-api-key -w
```

Il risultato è salvato in `cache/match-AAAA-MM-GG.json` per la fase 5.

## Fase 5: riepilogo e notifiche

```bash
.venv/bin/python -m evershopper run --offline --dry-run   # prova: ultima cache, niente inviato
.venv/bin/python -m evershopper run --offers-json examples/offerte-esempio.json   # offerte inventate, lista vera
.venv/bin/python -m evershopper run --offline             # ultima cache, invia sui canali configurati
.venv/bin/python -m evershopper run                       # flusso completo (è il comando della fase 6)
```

`run` scarica le offerte solo se non c'è già la cache di oggi (`--refresh` per forzare), legge la lista,
abbina, salva `cache/report-DATA.txt/.html` e invia il riepilogo sui canali di `output.channels`:
- `notification`: notifica macOS con le voci in offerta, ordinate per sconto;
- `note`: nota «Offerte Everli» in Note, aggiornata a ogni esecuzione (cartella in `output.note_folder`);
- `email`: email con Mail a `output.email_to` (disattivata di default).

Con `output.mark_reminders: true` le note dei promemoria in offerta ricevono una riga
`🏷️ In offerta: -33% Granarolo Latte Intero 1 L 1,19 € fino al 08/10`; a ogni esecuzione le righe
vecchie vengono tolte, e lo script non tocca nient'altro. Serve l'helper ricompilato (`sh helpers/build.sh`).

La prima volta macOS chiede il permesso di Automazione per Note (e Mail): concedilo.
Sessione Everli scaduta → notifica, codice di uscita 2, nessun nuovo tentativo.

## Privacy e termini d'uso
- La password non passa mai dallo script: il login si fa nel browser.
- `.state/` (cookie di sessione) e `discovery/captures/` sono in `.gitignore` e restano sul Mac.
  Nelle catture gli header con cookie/token sono oscurati.
- Uso personale, poche richieste, a ritmo umano e senza parallelismo, come richiesto dai termini Everli.
