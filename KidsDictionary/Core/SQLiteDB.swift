import Foundation
import SQLite3

/// A small read-only wrapper over the SQLite C API (part of iOS and macOS, so the
/// app needs no third-party package). Only what the dictionary lookups need.
final class SQLiteDB {
    enum Failure: Error, CustomStringConvertible {
        case open(String), prepare(String)
        var description: String {
            switch self {
            case .open(let m): return "Could not open the dictionary: \(m)"
            case .prepare(let m): return "Dictionary query failed: \(m)"
            }
        }
    }

    private var handle: OpaquePointer?
    private var cache: [String: OpaquePointer] = [:]
    private let lock = NSLock()

    /// Opens the file read-only. `immutable=1` tells SQLite the file never changes,
    /// which suits a database shipped inside the app bundle.
    init(path: String) throws {
        let uri = "file:\(path)?immutable=1"
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI | SQLITE_OPEN_NOMUTEX
        if sqlite3_open_v2(uri, &handle, flags, nil) != SQLITE_OK {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown error"
            sqlite3_close(handle)
            handle = nil
            throw Failure.open(message)
        }
    }

    /// Creates an empty in-memory database, used by the tests to build fixtures.
    init(memory: Void) throws {
        if sqlite3_open(":memory:", &handle) != SQLITE_OK {
            throw Failure.open("in-memory database")
        }
    }

    deinit {
        for statement in cache.values { sqlite3_finalize(statement) }
        sqlite3_close(handle)
    }

    /// Runs statements that return no rows (tests only).
    func execute(_ sql: String) throws {
        lock.lock(); defer { lock.unlock() }
        var error: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(handle, sql, nil, nil, &error) != SQLITE_OK {
            let message = error.map { String(cString: $0) } ?? "unknown error"
            sqlite3_free(error)
            throw Failure.prepare(message)
        }
    }

    /// Runs a query with text or number arguments and maps each row.
    func rows<T>(_ sql: String, _ args: [Any] = [], _ map: (Row) -> T) -> [T] {
        lock.lock(); defer { lock.unlock() }
        guard let statement = prepared(sql) else { return [] }
        defer { sqlite3_reset(statement); sqlite3_clear_bindings(statement) }
        for (i, arg) in args.enumerated() {
            let index = Int32(i + 1)
            switch arg {
            case let text as String:
                sqlite3_bind_text(statement, index, text, -1, SQLiteDB.transient)
            case let number as Int:
                sqlite3_bind_int64(statement, index, Int64(number))
            case let number as Double:
                sqlite3_bind_double(statement, index, number)
            default:
                sqlite3_bind_null(statement, index)
            }
        }
        var out: [T] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            out.append(map(Row(statement: statement)))
        }
        return out
    }

    struct Row {
        let statement: OpaquePointer
        func text(_ column: Int32) -> String {
            guard let c = sqlite3_column_text(statement, column) else { return "" }
            return String(cString: c)
        }
        func optionalText(_ column: Int32) -> String? {
            sqlite3_column_type(statement, column) == SQLITE_NULL ? nil : text(column)
        }
        func int(_ column: Int32) -> Int { Int(sqlite3_column_int64(statement, column)) }
        func double(_ column: Int32) -> Double { sqlite3_column_double(statement, column) }
    }

    private func prepared(_ sql: String) -> OpaquePointer? {
        if let statement = cache[sql] { return statement }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            return nil
        }
        cache[sql] = statement
        return statement
    }

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
}
