// Legge da Promemoria (EventKit) gli elementi non completati di una lista
// e li restituisce in JSON. Solo lettura.
//
// Uso:
//   reminders-helper --list Spesa [--out FILE]
//   reminders-helper --lists      [--out FILE]   # elenca le liste disponibili
//
// Con --out il risultato (o l'errore, come {"error", "code"}) va nel file invece
// che su stdout: serve quando l'helper gira come app lanciata da `open`, che non
// restituisce né stdout né codice di uscita.
//
// Codici di uscita: 0 ok, 2 uso errato, 3 accesso negato, 4 lista non trovata, 5 errore EventKit.

import EventKit
import Foundation

struct Item: Codable {
    let id: String
    let title: String
    let notes: String?
    let priority: Int
    let due: String?
    let created: String?
}

struct Output: Codable {
    let list: String
    let items: [Item]
}

struct Lists: Codable {
    let lists: [String]
}

struct Failure: Codable {
    let error: String
    let code: Int32
}

var outPath: String?

func emit<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    guard let data = try? encoder.encode(value) else {
        FileHandle.standardError.write("Impossibile serializzare il risultato in JSON\n".data(using: .utf8)!)
        exit(5)
    }
    if let path = outPath {
        do {
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
        } catch {
            FileHandle.standardError.write("Impossibile scrivere \(path): \(error)\n".data(using: .utf8)!)
            exit(5)
        }
    } else {
        print(String(data: data, encoding: .utf8)!)
    }
}

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    if outPath != nil { emit(Failure(error: message, code: code)) }
    exit(code)
}

// Confronto sul valore numerico: .authorized e .fullAccess condividono il 3.
func statusDescription() -> String {
    switch EKEventStore.authorizationStatus(for: .reminder).rawValue {
    case 0: return "non ancora richiesto"
    case 1: return "limitato da un profilo o da Tempo di utilizzo"
    case 2: return "negato"
    case 3: return "concesso"
    case 4: return "solo scrittura"
    default: return "sconosciuto"
    }
}

// --- argomenti ---
var listName: String?
var listOnly = false
var args = CommandLine.arguments.dropFirst()
while let arg = args.popFirst() {
    switch arg {
    case "--list":
        guard let value = args.popFirst() else { fail("--list richiede il nome della lista", 2) }
        listName = value
    case "--lists":
        listOnly = true
    case "--out":
        guard let value = args.popFirst() else { fail("--out richiede un percorso", 2) }
        outPath = value
    default:
        fail("Argomento sconosciuto: \(arg)\nUso: reminders-helper --list NOME | --lists [--out FILE]", 2)
    }
}
if !listOnly && listName == nil { fail("Uso: reminders-helper --list NOME | --lists [--out FILE]", 2) }

// --- permesso ---
let store = EKEventStore()
let semaphore = DispatchSemaphore(value: 0)
var granted = false
var accessError: Error?
let completion: (Bool, Error?) -> Void = { ok, error in
    granted = ok
    accessError = error
    semaphore.signal()
}
if #available(macOS 14.0, *) {
    store.requestFullAccessToReminders(completion: completion)
} else {
    store.requestAccess(to: .reminder, completion: completion)
}
semaphore.wait()
if !granted {
    let detail = accessError.map { ", \($0.localizedDescription)" } ?? ""
    fail("Accesso a Promemoria negato (stato: \(statusDescription())\(detail)).", 3)
}

let calendars = store.calendars(for: .reminder)
if listOnly {
    emit(Lists(lists: calendars.map { $0.title }.sorted()))
    exit(0)
}

let matching = calendars.filter { $0.title == listName! }
if matching.isEmpty {
    let available = calendars.map { $0.title }.sorted().joined(separator: ", ")
    fail("Lista \"\(listName!)\" non trovata. Liste disponibili: \(available)", 4)
}

// --- lettura ---
let iso = ISO8601DateFormatter()
var reminders: [EKReminder]?
let predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: matching)
store.fetchReminders(matching: predicate) { result in
    reminders = result
    semaphore.signal()
}
semaphore.wait()
guard let fetched = reminders else { fail("EventKit non ha restituito i promemoria", 5) }

let items = fetched
    .sorted { ($0.creationDate ?? .distantPast) < ($1.creationDate ?? .distantPast) }
    .map { r -> Item in
        let due = r.dueDateComponents.flatMap { Calendar.current.date(from: $0) }.map { iso.string(from: $0) }
        let notes = r.notes?.trimmingCharacters(in: .whitespacesAndNewlines)
        return Item(
            id: r.calendarItemIdentifier,
            title: (r.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            notes: (notes?.isEmpty ?? true) ? nil : notes,
            priority: r.priority,
            due: due,
            created: r.creationDate.map { iso.string(from: $0) }
        )
    }
    .filter { !$0.title.isEmpty }

emit(Output(list: listName!, items: items))
