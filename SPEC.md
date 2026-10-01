# Spec – Offerte Everli × lista Promemoria

## Obiettivo
Script locale per macOS che una volta a settimana legge le offerte del supermercato abituale su Everli (it.everli.com), le incrocia con la lista della spesa in Promemoria (Apple Reminders) e segnala quali prodotti della lista sono in offerta. Scopo: eliminare il confronto manuale offerta per offerta.

## Contesto e vincoli
- Uso personale e domestico, account di Paola.
- I termini Everli consentono l'uso dei contenuti solo per uso personale e non commerciale e vietano di far usare le credenziali a terzi: lo script gira SOLO in locale, nessun servizio esterno riceve la password.
- Everli può sospendere l'accesso in qualsiasi momento: poche richieste, ritmo "umano", nessun parallelismo, pause casuali tra le chiamate.
- La lista della spesa resta in Promemoria (si compra anche altrove, Everli è solo una delle fonti).
- La pagina del supermercato su Everli ordina già le offerte per sconto decrescente.

## Architettura
1. Discovery (una tantum)
   - Playwright (headed) per aprire it.everli.com, login manuale la prima volta, salvataggio della sessione (storageState).
   - Registrare le chiamate di rete della pagina offerte/promozioni del supermercato scelto.
   - Obiettivo: individuare l'endpoint JSON interno delle promozioni (URL, header, parametri, paginazione). Se non esiste, fallback a parsing del DOM.
   - Documentare quanto trovato in docs/everli-api.md.

2. Fetch offerte (settimanale)
   - Riutilizzare la sessione salvata; se scaduta, notificare l'utente per un nuovo login manuale (niente gestione automatica di codici di verifica).
   - Scaricare le offerte del supermercato configurato: nome prodotto, marca, formato, prezzo pieno, prezzo scontato, % sconto, validità, URL/ID prodotto.
   - Cache locale in JSON con data, per confronti e debug.

3. Lettura lista Promemoria
   - Leggere gli elementi non completati della lista configurata (default: "Spesa").
   - Opzione preferita: piccolo helper Swift con EventKit. Alternative: AppleScript/osascript o export via Comandi Rapidi.

4. Matching
   - Le voci in lista sono generiche ("latte", "pasta"), quelle Everli specifiche ("Granarolo Latte Intero 1L").
   - Prima passata fuzzy (normalizzazione, sinonimi base); seconda passata opzionale via API Claude per abbinamenti ambigui, restituendo JSON strutturato.
   - Output per voce: nessuna offerta / una o più offerte con prezzo e sconto.

5. Output
   - Riepilogo leggibile (es. notifica macOS + nota o email) con le voci in offerta, ordinate per sconto.
   - Opzionale: aggiungere un tag/nota "🏷️ in offerta" ai promemoria corrispondenti.

6. Pianificazione e segreti
   - launchd (LaunchAgent) settimanale, giorno e ora configurabili.
   - Credenziali ed eventuale chiave API nel Portachiavi macOS, mai in chiaro nel repo.
   - Log su file con rotazione semplice.

## Configurazione (config.yaml)
- supermercato Everli (ID/URL)
- nome lista Promemoria
- giorno/ora esecuzione
- canale di output
- soglia minima di sconto da segnalare

## Fasi di sviluppo
1. Discovery API Everli con Playwright → docs/everli-api.md
2. Fetch offerte + cache JSON
3. Lettura Promemoria
4. Matching fuzzy, poi eventuale LLM
5. Output e notifiche
6. launchd + Portachiavi

## Criteri di accettazione
- Un'esecuzione manuale produce l'elenco delle voci in lista attualmente in offerta, con prezzo e sconto.
- Nessuna credenziale nel codice o nei log.
- Sessione scaduta → notifica chiara, nessun tentativo di login ripetuto.
- Meno di qualche decina di richieste per esecuzione.

## Prompt di avvio per Claude Code
Leggi SPEC.md. Partiamo dalla fase 1: usa Playwright in modalità visibile per aprire it.everli.com, lasciami fare il login a mano, salva la sessione e registra le chiamate di rete della pagina offerte del supermercato, così capiamo se c'è un'API JSON da usare.
