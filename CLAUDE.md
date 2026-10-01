# CLAUDE.md

Progetto personale descritto in SPEC.md: offerte Everli × lista "Spesa" di Promemoria, solo su macOS e in locale.

Regole:
- Mai committare `.state/`, `discovery/captures/`, `cache/`, `logs/`, `config.yaml`: contengono cookie o dati personali.
- Mai scrivere credenziali, cookie o token in codice, log o docs. I segreti vanno nel Portachiavi (`security` / `keyring`).
- Richieste a Everli: sequenziali, pause casuali (2–6 s), qualche decina al massimo per esecuzione.
  Se la sessione è scaduta, notificare e fermarsi: niente login automatici e niente retry a raffica.
- Python 3.11+, dipendenze in requirements.txt. Documentazione e messaggi per l'utente in italiano.
