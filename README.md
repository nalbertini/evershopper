# evershopper

Incrocia le offerte Everli del supermercato abituale con la lista della spesa in Promemoria.
Gira solo in locale, su macOS. Requisiti e fasi sono in [SPEC.md](SPEC.md).

## Stato
- [x] Fase 1 – strumenti di discovery (`discovery/`)
- [ ] Fase 1 – esito documentato in `docs/everli-api.md`
- [ ] Fase 2 – fetch offerte + cache JSON
- [ ] Fase 3 – lettura Promemoria
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

## Privacy e termini d'uso
- La password non passa mai dallo script: il login si fa nel browser.
- `.state/` (cookie di sessione) e `discovery/captures/` sono in `.gitignore` e restano sul Mac.
  Nelle catture gli header con cookie/token sono oscurati.
- Uso personale, poche richieste, a ritmo umano e senza parallelismo, come richiesto dai termini Everli.
