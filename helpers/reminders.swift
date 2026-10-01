// Legge da Promemoria (EventKit) gli elementi non completati di una lista
// e li restituisce in JSON. Solo lettura.
//
// Uso:
//   reminders-helper --list Spesa [--list-id ID] [--out FILE]
//   reminders-helper --lists      [--out FILE]   # elenca le liste con account e identificativo
//   reminders-helper --mark FILE  [--out FILE]   # scrive/rimuove l'etichetta "in offerta"
//
// --mark legge {"marker", "list_ids", "marks": {id promemoria: testo}}: nelle liste indicate
// toglie dalle note le righe che iniziano con il marcatore e lo riaggiunge solo ai promemoria
// in `marks`. È l'unica modalità che scrive, e tocca solo quelle righe.
//
// Più liste possono avere lo stesso nome (account diversi): con il solo --list
// vengono unite, con --list-id se ne sceglie una.
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
    let listId: String
}

struct ListInfo: Codable {
    let id: String
    let title: String
    let source: String
}

struct Output: Codable {
    let list: String
    let matched: [ListInfo]
    let items: [Item]
}

struct Lists: Codable {
    let lists: [ListInfo]
}

func info(_ c: EKCalendar) -> ListInfo {
    ListInfo(id: c.calendarIdentifier, title: c.title, source: c.source?.title ?? "")
}

struct MarkRequest: Codable {
    let marker: String
    let listIds: [String]
    let marks: [String: String]

    enum CodingKeys: String, CodingKey {
        case marker
        case listIds = "list_ids"
        case marks
    }
}

struct MarkResult: Codable {
    let updated: Int
    let marked: Int
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
var listId: String?
var listOnly = false
var markPath: String?
var args = CommandLine.arguments.dropFirst()
while let arg = args.popFirst() {
    switch arg {
    case "--list":
        guard let value = args.popFirst() else { fail("--list richiede il nome della lista", 2) }
        listName = value
    case "--list-id":
        guard let value = args.popFirst() else { fail("--list-id richiede un identificativo", 2) }
        listId = value
    case "--lists":
        listOnly = true
    case "--mark":
        guard let value = args.popFirst() else { fail("--mark richiede un file JSON", 2) }
        markPath = value
    case "--out":
        guard let value = args.popFirst() else { fail("--out richiede un percorso", 2) }
        outPath = value
    default:
        fail("Argomento sconosciuto: \(arg)\nUso: reminders-helper --list NOME | --lists [--out FILE]", 2)
    }
}
if !listOnly && markPath == nil && listName == nil { fail("Uso: reminders-helper --list NOME | --lists [--out FILE]", 2) }

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
    emit(Lists(lists: calendars.map(info).sorted { ($0.title, $0.source) < ($1.title, $1.source) }))
    exit(0)
}

func fetchIncomplete(_ cals: [EKCalendar]) -> [EKReminder] {
    var result: [EKReminder]?
    let predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: cals)
    store.fetchReminders(matching: predicate) { fetched in
        result = fetched
        semaphore.signal()
    }
    semaphore.wait()
    guard let reminders = result else { fail("EventKit non ha restituito i promemoria", 5) }
    return reminders
}

if let path = markPath {
    guard let data = FileManager.default.contents(atPath: path),
          let request = try? JSONDecoder().decode(MarkRequest.self, from: data),
          !request.marker.isEmpty
    else { fail("Richiesta --mark non valida: \(path)", 2) }
    let targets = calendars.filter { request.listIds.contains($0.calendarIdentifier) }
    if targets.isEmpty { fail("Nessuna delle liste da aggiornare è stata trovata", 4) }

    var updated = 0
    for r in fetchIncomplete(targets) {
        let original = r.notes ?? ""
        var lines = original.components(separatedBy: "\n")
        let before = lines.count
        lines.removeAll { $0.hasPrefix(request.marker) }
        let mark = request.marks[r.calendarItemIdentifier]
        if lines.count == before && mark == nil { continue }  // niente da togliere né da aggiungere
        while let last = lines.last, last.trimmingCharacters(in: .whitespaces).isEmpty { lines.removeLast() }
        if let text = mark { lines.append("\(request.marker): \(text)") }
        let notes = lines.joined(separator: "\n")
        if notes == original { continue }
        r.notes = notes.isEmpty ? nil : notes
        do {
            try store.save(r, commit: false)
            updated += 1
        } catch {
            fail("Impossibile aggiornare «\(r.title ?? "")»: \(error.localizedDescription)", 5)
        }
    }
    do {
        try store.commit()
    } catch {
        fail("Impossibile salvare le modifiche: \(error.localizedDescription)", 5)
    }
    emit(MarkResult(updated: updated, marked: request.marks.count))
    exit(0)
}

let matching = calendars.filter { c in
    if let id = listId, !id.isEmpty { return c.calendarIdentifier == id }
    return c.title == listName!
}
if matching.isEmpty {
    let available = calendars.map { $0.title }.sorted().joined(separator: ", ")
    let what = (listId?.isEmpty == false) ? "con identificativo \(listId!)" : "\"\(listName!)\""
    fail("Lista \(what) non trovata. Liste disponibili: \(available)", 4)
}

// --- lettura ---
let iso = ISO8601DateFormatter()
let items = fetchIncomplete(matching)
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
            created: r.creationDate.map { iso.string(from: $0) },
            listId: r.calendar.calendarIdentifier
        )
    }
    .filter { !$0.title.isEmpty }

emit(Output(list: listName!, matched: matching.map(info), items: items))
