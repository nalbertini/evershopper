# Evershopper

Ogni settimana scarica le offerte Everli del supermercato abituale, le incrocia con la lista della spesa (una To-do di
Home Assistant) e manda il riepilogo come notifica. Sempre acceso, senza il Mac. Il login a Everli resta sul Mac.

## Prima installazione

1. Sul Mac, nella cartella del progetto: `discovery/discover.py --login` (se la sessione è scaduta) e `autoconfig`, come
   nel README, finché `python -m evershopper run --dry-run` funziona.
2. Copia `config.yaml` del Mac in `share/evershopper/config.yaml` (condivisione Samba). Controlla
   `reminders.todo_entity` (predefinita `todo.spesa`) e l'orario in `schedule`.
3. Sul Mac: `python -m evershopper export-session` copia la sessione negli appunti. Incollala nell'opzione
   `everli_session` dell'add-on e salva.
4. Avvia l'add-on.

## Opzioni

| Opzione | Cosa mettere |
|---|---|
| `everli_session` | l'output di `export-session` (sessione e user agent del browser). Obbligatoria per scaricare le offerte |
| `notify_service` | facoltativo: il servizio per la push sul telefono, es. `notify.mobile_app_iphone_di_paola`. Vuoto: solo notifica nella campanella di HA |
| `anthropic_api_key` | facoltativo: serve solo se in `config.yaml` c'è `matching.llm.enabled: true` |

## Lista della spesa

Le voci non spuntate della To-do indicata in `reminders.todo_entity` (Impostazioni → Dispositivi e servizi → Elenco della
spesa, oppure Local To-do). Promemoria di Apple non è leggibile da Home Assistant.

## Cosa si vede

- Notifica "Offerte Everli" nella campanella (e sul telefono con `notify_service`).
- `sensor.evershopper_offerte`: numero di voci in offerta; negli attributi voce, sconto, prezzo e prodotto.
- Pagina "Spesa" nella barra laterale con l'ultimo riepilogo.

## Sessione scaduta

Arriva una notifica: sul Mac rifai `discovery/discover.py --login`, poi `export-session` e incolla di nuovo
nell'opzione `everli_session`. L'add-on non prova mai a rifare il login da solo.

## Dove sono i file

`/share/evershopper/`: `config.yaml`, `cache/` (offerte, abbinamenti, riepiloghi) e `logs/`. La cartella finisce nei
backup di Home Assistant se includi `share`: i backup contengono anche le opzioni, quindi la sessione Everli.
