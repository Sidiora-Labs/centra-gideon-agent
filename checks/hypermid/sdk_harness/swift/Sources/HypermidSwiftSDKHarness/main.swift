import Foundation
import HypermidClient

final class OpenSSLUnixTransport: HypermidTransport, @unchecked Sendable {
    private let process: Process
    private let input: FileHandle
    private let output: FileHandle
    private let lock = NSLock()
    private var disconnectNextWrite = false
    private var discardInbound = false
    private var closed = false

    init(record: ConnectionRecord) throws {
        guard !record.endpoint.hasPrefix("tcp://") else {
            throw HypermidClientError.protocolViolation("Swift harness requires the Unix endpoint")
        }
        let process = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
        process.arguments = [
            "s_client", "-quiet", "-tls1_3", "-unix", record.endpoint,
            "-servername", record.serverName, "-verify_hostname", record.serverName,
            "-verify_return_error", "-CAfile", record.caCertificate,
            "-cert", record.clientCertificate, "-key", record.clientPrivateKey,
        ]
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        self.process = process
        self.input = stdin.fileHandleForWriting
        self.output = stdout.fileHandleForReading
        Task.detached {
            while true {
                do {
                    guard let data = try stderr.fileHandleForReading.read(upToCount: 4096),
                          !data.isEmpty else { return }
                } catch { return }
            }
        }
    }

    func readExactly(_ count: Int) async throws -> Data {
        try await Task.detached { [self] in try blockingReadExactly(count) }.value
    }

    private func blockingReadExactly(_ count: Int) throws -> Data {
        var result = Data()
        while result.count < count {
            lock.lock()
            let refuse = discardInbound || closed
            lock.unlock()
            if refuse { throw HypermidClientError.connectionEnded }
            guard let data = try output.read(upToCount: count - result.count), !data.isEmpty else {
                throw HypermidClientError.connectionEnded
            }
            lock.lock()
            let discarded = discardInbound || closed
            lock.unlock()
            if discarded { throw HypermidClientError.connectionEnded }
            result.append(data)
        }
        return result
    }

    func write(_ data: Data) async throws {
        let disconnect = try prepareWrite()
        try input.write(contentsOf: data)
        if disconnect { process.terminate() }
    }

    private func prepareWrite() throws -> Bool {
        lock.lock()
        guard !closed else {
            lock.unlock()
            throw HypermidClientError.connectionEnded
        }
        let disconnect = disconnectNextWrite
        disconnectNextWrite = false
        if disconnect { discardInbound = true }
        lock.unlock()
        return disconnect
    }

    func close() async {
        guard markClosed() else { return }
        try? input.close()
        if process.isRunning { process.terminate() }
    }

    private func markClosed() -> Bool {
        lock.lock()
        guard !closed else {
            lock.unlock()
            return false
        }
        closed = true
        lock.unlock()
        return true
    }

    func disconnectAfterNextWrite() {
        lock.lock()
        disconnectNextWrite = true
        lock.unlock()
    }
}

@main
struct Harness {
    static func main() async {
        do {
            try await run()
        } catch {
            FileHandle.standardError.write(Data("\(error)\n".utf8))
            exit(1)
        }
    }

    static func run() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count == 3 else { throw HypermidClientError.protocolViolation("record paths are required") }
        let recordPath = arguments[1]
        let invalidRecordPath = arguments[2]
        let scope = try Scope(ownerId: "sdk-owner", projectId: "sdk-project", workspaceId: "sdk-workspace")

        var invalidVersionRefused = false
        do { _ = try ConnectionRecord.load(invalidRecordPath) }
        catch { invalidVersionRefused = true }
        guard invalidVersionRefused else { throw HypermidClientError.protocolViolation("invalid protocol record was accepted") }

        let record = try ConnectionRecord.load(recordPath)
        let firstTransport = try OpenSSLUnixTransport(record: record)
        let first = try HypermidClient(transport: firstTransport, credential: record.credential, scope: scope)
        _ = try await first.connect()
        let description = try await first.describe()
        guard case .object(let described) = description, described["protocol"] == .string(hypermidProtocol) else {
            throw HypermidClientError.protocolViolation("describe did not report hypermid.v1")
        }
        let marker = JSONValue.object(["language": .string("swift"), "value": .integer(7)])
        guard try await first.passthrough(marker) == marker else {
            throw HypermidClientError.protocolViolation("passthrough payload changed")
        }
        _ = try await first.publishEvent(
            eventId: "sdk-swift-event",
            topic: "events.swift.sdk",
            scope: scope,
            atMilliseconds: 3,
            schemaName: "sdk.client",
            schemaVersion: 1,
            payload: marker
        )
        let consumerId = "sdk-swift-consumer"
        let subscribed = try await first.subscribeEvents(
            consumerId: consumerId,
            scope: scope,
            topicFilter: "events.swift.*"
        )
        guard let event = try await subscribed.nextEvent(),
              event.eventId == "sdk-swift-event" else {
            throw HypermidClientError.protocolViolation("durable event was not acknowledged")
        }
        guard (try await subscribed.acknowledge(event)).acknowledged else {
            throw HypermidClientError.protocolViolation("durable event was not acknowledged")
        }
        let subscriptionCursor = await subscribed.resumePoint().cursor
        try await subscribed.close()
        await first.close()

        let secondTransport = try OpenSSLUnixTransport(record: record)
        let second = try HypermidClient(transport: secondTransport, credential: record.credential, scope: scope)
        _ = try await second.connect()
        let resumed = try await second.resumeEvents(
            consumerId: consumerId,
            scope: scope,
            topicFilter: "events.swift.*",
            after: subscriptionCursor
        )
        guard await resumed.resumePoint().cursor == subscriptionCursor else {
            throw HypermidClientError.protocolViolation("cursor resume changed")
        }
        try await resumed.close()
        var wrongEpochRefused = false
        do {
            _ = try await second.resumeEvents(
                consumerId: consumerId,
                scope: scope,
                topicFilter: "events.swift.*",
                after: Cursor(
                    epoch: subscriptionCursor.epoch + 1,
                    sequence: subscriptionCursor.sequence
                )
            )
        }
        catch HypermidClientError.remote(let error)
            where error.code == "CURSOR_GAP" || error.code == "CONSUMER_RESUME_MISMATCH" {
            wrongEpochRefused = true
        }
        guard wrongEpochRefused else { throw HypermidClientError.protocolViolation("foreign cursor epoch was not refused") }
        await second.close()

        let mutationTransport = try OpenSSLUnixTransport(record: record)
        let mutation = try HypermidClient(transport: mutationTransport, credential: record.credential, scope: scope)
        _ = try await mutation.connect()
        mutationTransport.disconnectAfterNextWrite()
        var outcomeUnknown = false
        do {
            _ = try await mutation.request(
                operation: "diagnostics.rerun",
                payload: .object([:]),
                effect: .mutation
            )
        } catch HypermidClientError.outcomeUnknown {
            outcomeUnknown = true
        }
        guard outcomeUnknown else { throw HypermidClientError.protocolViolation("mutation disconnect was not outcome unknown") }

        let result: [String: Any] = [
            "language": "swift", "tls": true, "describe": true, "passthrough": true,
            "durable_ack": true,
            "reconnect_resume": true, "wrong_epoch_refused": true, "invalid_version_refused": true,
            "mutation_outcome_unknown": true,
        ]
        let encoded = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        FileHandle.standardOutput.write(encoded + Data("\n".utf8))
    }

}
