import Foundation
#if canImport(Glibc)
import Glibc
private let hypermidEffectiveUserId = Glibc.geteuid()
#elseif canImport(Darwin)
import Darwin
private let hypermidEffectiveUserId = Darwin.geteuid()
#endif
#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif

public let hypermidProtocol = "hypermid.v1"
public let hypermidMaximumFrameBytes = 8 * 1024 * 1024
public let hypermidMaximumChunkBytes = 64 * 1024
private let maximumSafeInteger: UInt64 = 9_007_199_254_740_991

public enum JSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case integer(Int64)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Int64.self) {
            guard value >= -Int64(maximumSafeInteger), value <= Int64(maximumSafeInteger) else {
                throw HypermidClientError.protocolViolation("wire integer exceeds the interoperable range")
            }
            self = .integer(value)
        }
        else if let value = try? container.decode(Double.self), value.isFinite {
            guard value.rounded() != value || abs(value) <= Double(maximumSafeInteger) else {
                throw HypermidClientError.protocolViolation("wire integer exceeds the interoperable range")
            }
            self = .number(value)
        }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([JSONValue].self) { self = .array(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { throw HypermidClientError.protocolViolation("wire numbers must be integers") }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .number(let value):
            guard value.isFinite,
                  value.rounded() != value || abs(value) <= Double(maximumSafeInteger) else {
                throw HypermidClientError.protocolViolation("wire number is not interoperable")
            }
            try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }
}

public struct Scope: Codable, Equatable, Sendable {
    public let ownerId: String
    public let projectId: String
    public let workspaceId: String?

    enum CodingKeys: String, CodingKey {
        case ownerId = "owner_id"
        case projectId = "project_id"
        case workspaceId = "workspace_id"
    }

    public init(ownerId: String, projectId: String, workspaceId: String? = nil) throws {
        self.ownerId = try validIdentifier(ownerId, "owner_id")
        self.projectId = try validIdentifier(projectId, "project_id")
        self.workspaceId = try workspaceId.map { try validIdentifier($0, "workspace_id") }
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            ownerId: container.decode(String.self, forKey: .ownerId),
            projectId: container.decode(String.self, forKey: .projectId),
            workspaceId: container.decodeIfPresent(String.self, forKey: .workspaceId)
        )
    }
}

public struct Cursor: Codable, Equatable, Sendable {
    public let epoch: UInt64
    public let sequence: UInt64

    enum CodingKeys: String, CodingKey { case epoch, sequence }

    public init(epoch: UInt64, sequence: UInt64) throws {
        guard epoch > 0, epoch <= maximumSafeInteger, sequence <= maximumSafeInteger else {
            throw HypermidClientError.protocolViolation("invalid cursor")
        }
        self.epoch = epoch
        self.sequence = sequence
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            epoch: container.decode(UInt64.self, forKey: .epoch),
            sequence: container.decode(UInt64.self, forKey: .sequence)
        )
    }
}

private func scopeWire(_ scope: Scope) -> JSONValue {
    var fields: [String: JSONValue] = [
        "owner_id": .string(scope.ownerId),
        "project_id": .string(scope.projectId),
    ]
    if let workspaceId = scope.workspaceId { fields["workspace_id"] = .string(workspaceId) }
    return .object(fields)
}

private func cursorWire(_ cursor: Cursor) -> JSONValue {
    .object([
        "epoch": .integer(Int64(cursor.epoch)),
        "sequence": .integer(Int64(cursor.sequence)),
    ])
}

public enum PrincipalKind: String, Codable, Sendable {
    case localUser = "local_user"
    case supervisedModule = "supervised_module"
    case device
    case service
}

public struct Principal: Codable, Equatable, Sendable {
    public let id: String
    public let kind: PrincipalKind
    public let scopes: [String]
    public let moduleId: String?
    public let spawnGeneration: UInt64?

    enum CodingKeys: String, CodingKey {
        case id, kind, scopes
        case moduleId = "module_id"
        case spawnGeneration = "spawn_generation"
    }
}

public struct EventTrace: Codable, Equatable, Sendable {
    public let traceId: String
    public let requestId: String

    enum CodingKeys: String, CodingKey {
        case traceId = "trace_id"
        case requestId = "request_id"
    }
}

public struct ScopedEvent: Equatable, Sendable {
    public let eventId: String
    public let topic: String
    public let producer: Principal
    public let scope: Scope
    public let atMilliseconds: UInt64
    public let schemaName: String
    public let schemaVersion: UInt64
    public let payloadDigest: String
    public let trace: EventTrace?
    public let cursor: Cursor
    public let payload: JSONValue
    public let deliveryCount: UInt64

    public static func fromWire(_ value: JSONValue) throws -> ScopedEvent {
        guard case .object(let fields) = value else {
            throw HypermidClientError.protocolViolation("event record must be an object")
        }
        let required = Set([
            "event_id", "topic", "producer", "scope", "at_ms", "schema_name",
            "schema_version", "payload_digest", "cursor", "payload",
        ])
        let optional = Set(["trace", "delivery_count", "subscription_id"])
        guard required.isSubset(of: fields.keys), Set(fields.keys).subtracting(required).isSubset(of: optional) else {
            throw HypermidClientError.protocolViolation("event record fields do not match hypermid.v1")
        }
        let eventId = try wireIdentifier(fields["event_id"], "event_id")
        let topic = try wireString(fields["topic"], "topic", maximum: 256)
        try requireWireFields(
            fields["producer"], required: ["id", "kind", "scopes"],
            optional: ["module_id", "spawn_generation"], field: "producer"
        )
        let producer = try decodeWire(Principal.self, fields["producer"], "producer")
        try validatePrincipal(producer)
        try requireWireFields(
            fields["scope"], required: ["owner_id", "project_id"],
            optional: ["workspace_id"], field: "scope"
        )
        let scope = try decodeWire(Scope.self, fields["scope"], "scope")
        try validateScopeWire(scope)
        let atMilliseconds = try wireUnsigned(fields["at_ms"], "at_ms", minimum: 0)
        let schemaName = try wireString(fields["schema_name"], "schema_name", maximum: 160)
        let schemaVersion = try wireUnsigned(fields["schema_version"], "schema_version", minimum: 0)
        let payloadDigest = try wireDigest(fields["payload_digest"])
        try requireWireFields(fields["cursor"], required: ["epoch", "sequence"], field: "cursor")
        let cursor = try decodeWire(Cursor.self, fields["cursor"], "cursor")
        guard let payload = fields["payload"] else {
            throw HypermidClientError.protocolViolation("event payload is missing")
        }
        let computed = SHA256.hash(data: try canonicalJSON(payload)).map { String(format: "%02x", $0) }.joined()
        guard payloadDigest == computed else {
            throw HypermidClientError.protocolViolation("event payload digest does not match its payload")
        }
        let trace: EventTrace?
        if let traceValue = fields["trace"], traceValue != .null {
            try requireWireFields(traceValue, required: ["trace_id", "request_id"], field: "trace")
            trace = try decodeWire(EventTrace.self, traceValue, "trace")
            _ = try validIdentifier(trace!.traceId, "trace_id")
            _ = try validIdentifier(trace!.requestId, "request_id")
        } else {
            trace = nil
        }
        let deliveryCount = try fields["delivery_count"].map {
            try wireUnsigned($0, "delivery_count", minimum: 1)
        } ?? 1
        return ScopedEvent(
            eventId: eventId, topic: topic, producer: producer, scope: scope,
            atMilliseconds: atMilliseconds, schemaName: schemaName,
            schemaVersion: schemaVersion, payloadDigest: payloadDigest, trace: trace,
            cursor: cursor, payload: payload, deliveryCount: deliveryCount
        )
    }
}

public struct SubscriptionResumePoint: Codable, Equatable, Sendable {
    public let consumerId: String
    public let scope: Scope
    public let topicFilter: String
    public let cursor: Cursor

    enum CodingKeys: String, CodingKey {
        case consumerId = "consumer_id"
        case scope
        case topicFilter = "topic_filter"
        case cursor
    }

    public init(consumerId: String, scope: Scope, topicFilter: String, cursor: Cursor) throws {
        self.consumerId = try validIdentifier(consumerId, "consumer_id")
        self.scope = scope
        self.topicFilter = try validTopicFilter(topicFilter)
        self.cursor = cursor
    }
}

public final class SubscriptionResumeStore: @unchecked Sendable {
    private let url: URL
    private let lock = NSLock()

    public init(path: String) { self.url = URL(fileURLWithPath: path) }

    public func load() throws -> SubscriptionResumePoint? {
        lock.lock()
        defer { lock.unlock() }
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard let permissions = attributes[.posixPermissions] as? NSNumber,
              permissions.intValue & 0o077 == 0 else {
            throw HypermidClientError.protocolViolation("subscription resume state must be owner-only")
        }
#if canImport(Glibc) || canImport(Darwin)
        guard let owner = attributes[.ownerAccountID] as? NSNumber,
              owner.uint32Value == hypermidEffectiveUserId else {
            throw HypermidClientError.protocolViolation("subscription resume state has another owner")
        }
#endif
        let data = try Data(contentsOf: url)
        let object = try JSONSerialization.jsonObject(with: data)
        guard let fields = object as? [String: Any],
              Set(fields.keys) == Set(["consumer_id", "scope", "topic_filter", "cursor"]) else {
            throw HypermidClientError.protocolViolation("subscription resume state is invalid")
        }
        let point = try JSONDecoder().decode(SubscriptionResumePoint.self, from: data)
        return try SubscriptionResumePoint(
            consumerId: point.consumerId, scope: point.scope,
            topicFilter: point.topicFilter, cursor: point.cursor
        )
    }

    public func save(_ point: SubscriptionResumePoint) throws {
        lock.lock()
        defer { lock.unlock() }
        let manager = FileManager.default
        let directory = url.deletingLastPathComponent()
        try manager.createDirectory(
            at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: NSNumber(value: 0o700)]
        )
        let temporary = directory.appendingPathComponent(".\(url.lastPathComponent).tmp-\(ProcessInfo.processInfo.processIdentifier)-\(UUID().uuidString)")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(point)
        guard manager.createFile(
            atPath: temporary.path, contents: data,
            attributes: [.posixPermissions: NSNumber(value: 0o600)]
        ) else {
            throw HypermidClientError.protocolViolation("subscription resume state could not be created")
        }
        defer { try? manager.removeItem(at: temporary) }
        let handle = try FileHandle(forWritingTo: temporary)
        try handle.synchronize()
        try handle.close()
#if canImport(Glibc)
        guard Glibc.rename(temporary.path, url.path) == 0 else {
            throw HypermidClientError.protocolViolation("subscription resume state could not be replaced")
        }
#elseif canImport(Darwin)
        guard Darwin.rename(temporary.path, url.path) == 0 else {
            throw HypermidClientError.protocolViolation("subscription resume state could not be replaced")
        }
#else
        if manager.fileExists(atPath: url.path) { try manager.removeItem(at: url) }
        try manager.moveItem(at: temporary, to: url)
#endif
    }
}

public struct SubscriptionSnapshot: Equatable, Sendable {
    public let subscriptionId: String
    public let cursor: Cursor
    public let replay: [ScopedEvent]
}

public struct AcknowledgeReceipt: Equatable, Sendable {
    public let acknowledged: Bool
}

public struct ConnectionRecord: Codable, Sendable {
    public let endpoint: String
    public let `protocol`: String
    public let credentialId: String
    public let secretBase64: String
    public let serverName: String
    public let caCertificate: String
    public let clientCertificate: String
    public let clientPrivateKey: String
    public let expiresMilliseconds: UInt64

    enum CodingKeys: String, CodingKey {
        case endpoint, `protocol`, serverName = "server_name"
        case credentialId = "credential_id"
        case secretBase64 = "secret_b64"
        case caCertificate = "ca_certificate"
        case clientCertificate = "client_certificate"
        case clientPrivateKey = "client_private_key"
        case expiresMilliseconds = "expires_ms"
    }

    public static func load(_ path: String) throws -> ConnectionRecord {
        let attributes = try FileManager.default.attributesOfItem(atPath: path)
        guard let permissions = attributes[.posixPermissions] as? NSNumber,
              permissions.intValue & 0o077 == 0 else {
            throw HypermidClientError.protocolViolation("connection record must be owner-only")
        }
#if canImport(Glibc) || canImport(Darwin)
        guard let owner = attributes[.ownerAccountID] as? NSNumber,
              owner.uint32Value == hypermidEffectiveUserId else {
            throw HypermidClientError.protocolViolation("connection record has another owner")
        }
#endif
        let data = try Data(contentsOf: URL(fileURLWithPath: path))
        let object = try JSONSerialization.jsonObject(with: data)
        let expected = Set([
            "endpoint", "protocol", "credential_id", "secret_b64", "server_name",
            "ca_certificate", "client_certificate", "client_private_key", "expires_ms",
        ])
        guard let fields = object as? [String: Any], Set(fields.keys) == expected else {
            throw HypermidClientError.protocolViolation("connection record fields are invalid")
        }
        let value = try JSONDecoder().decode(ConnectionRecord.self, from: data)
        guard value.protocol == hypermidProtocol else {
            throw HypermidClientError.protocolViolation("unsupported connection-record protocol \(value.protocol)")
        }
        guard value.expiresMilliseconds > currentMilliseconds() else {
            throw HypermidClientError.protocolViolation("connection record has expired")
        }
        guard let secret = Data(base64Encoded: value.secretBase64), secret.count == 32,
              secret.base64EncodedString() == value.secretBase64 else {
            throw HypermidClientError.protocolViolation("connection credential is invalid")
        }
        return value
    }

    public var credential: Data {
        get throws {
            guard let value = Data(base64Encoded: secretBase64), value.count == 32 else {
                throw HypermidClientError.protocolViolation("connection credential is invalid")
            }
            return value
        }
    }
}

public enum EffectKind: Sendable { case query, mutation }
public enum EffectState: String, Codable, Sendable { case notStarted = "not_started", committed, unknown }
public enum ConnectionClass: String, Codable, Sendable { case client, module, device, service }

public struct WireError: Codable, Error, Equatable, Sendable {
    public let code: String
    public let message: String
    public let retryable: Bool
    public let retryAfterMilliseconds: UInt64?
    public let effectState: EffectState?

    enum CodingKeys: String, CodingKey {
        case code, message, retryable
        case retryAfterMilliseconds = "retry_after_ms"
        case effectState = "effect_state"
    }
}

public struct ClientEvent: Sendable {
    public let messageId: String
    public let operation: String?
    public let payload: JSONValue?
    public let error: WireError?
}

public struct EventChunk: Codable, Equatable, Sendable {
    public let eventId: String
    public let chunkIndex: UInt32
    public let chunkCount: UInt32
    public let digest: String
    public let data: String

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case chunkIndex = "chunk_index"
        case chunkCount = "chunk_count"
        case digest, data
    }

    public func decoded() throws -> Data {
        guard chunkCount > 0, chunkIndex < chunkCount,
              let bytes = Data(base64Encoded: data), bytes.count <= hypermidMaximumChunkBytes else {
            throw HypermidClientError.protocolViolation("invalid event chunk")
        }
        return bytes
    }
}

public protocol HypermidTransport: Sendable {
    func readExactly(_ count: Int) async throws -> Data
    func write(_ data: Data) async throws
    func close() async
}

public enum HypermidClientError: Error, Equatable {
    case protocolViolation(String)
    case connectionEnded
    case timedOut
    case cancelled
    case outcomeUnknown
    case remote(WireError)
}

public actor DurableSubscription {
    public nonisolated let snapshot: SubscriptionSnapshot
    public nonisolated let consumerId: String
    public nonisolated let scope: Scope
    public nonisolated let topicFilter: String

    private let client: HypermidClient
    private let resumeStore: SubscriptionResumeStore?
    private var point: SubscriptionResumePoint
    private var replay: [ScopedEvent]
    private var stream: AsyncThrowingStream<ScopedEvent, Error>.Iterator
    private var lastSeen: Cursor
    private var closed = false

    fileprivate init(
        client: HypermidClient,
        snapshot: SubscriptionSnapshot,
        point: SubscriptionResumePoint,
        resumeStore: SubscriptionResumeStore?,
        stream: AsyncThrowingStream<ScopedEvent, Error>
    ) {
        self.client = client
        self.snapshot = snapshot
        self.consumerId = point.consumerId
        self.scope = point.scope
        self.topicFilter = point.topicFilter
        self.point = point
        self.resumeStore = resumeStore
        self.replay = snapshot.replay
        self.stream = stream.makeAsyncIterator()
        self.lastSeen = point.cursor
    }

    public func nextEvent() async throws -> ScopedEvent? {
        guard !closed else { return nil }
        let event: ScopedEvent?
        if replay.isEmpty {
            var iterator = stream
            event = try await iterator.next()
            stream = iterator
        } else {
            event = replay.removeFirst()
        }
        guard let event else { return nil }
        guard event.scope == scope else {
            throw HypermidClientError.protocolViolation("daemon delivered an event outside the subscription scope")
        }
        guard event.cursor.epoch == lastSeen.epoch, event.cursor.sequence > lastSeen.sequence else {
            throw HypermidClientError.protocolViolation("daemon delivered a replayed, reordered, or foreign-epoch event")
        }
        lastSeen = event.cursor
        return event
    }

    public func acknowledge(_ event: ScopedEvent) async throws -> AcknowledgeReceipt {
        guard !closed else { throw HypermidClientError.connectionEnded }
        guard event.scope == scope else {
            throw HypermidClientError.protocolViolation("cannot acknowledge an event from another scope")
        }
        let acknowledgement = try await client.acknowledgeEvent(
            consumerId: consumerId, eventId: event.eventId, scope: scope
        )
        point = try SubscriptionResumePoint(
            consumerId: consumerId, scope: scope, topicFilter: topicFilter, cursor: event.cursor
        )
        try resumeStore?.save(point)
        return acknowledgement
    }

    public func resumePoint() -> SubscriptionResumePoint { point }

    public func close() async throws {
        guard !closed else { return }
        closed = true
        try await client.unsubscribeEvents(consumerId: consumerId, scope: scope)
    }
}

private struct ClientHello: Codable {
    let kind = "hello"
    let protocols = [hypermidProtocol]
    let clientNonce: String
    let connectionClass: ConnectionClass
    let scope: Scope

    enum CodingKeys: String, CodingKey {
        case kind, protocols, scope
        case clientNonce = "client_nonce"
        case connectionClass = "connection_class"
    }
}

private struct ServerChallenge: Decodable {
    let kind: String
    let `protocol`: String
    let serverNonce: String
    let daemonInstanceId: String
    let authMethod: String

    enum CodingKeys: String, CodingKey {
        case kind, `protocol`
        case serverNonce = "server_nonce"
        case daemonInstanceId = "daemon_instance_id"
        case authMethod = "auth_method"
    }
}

public struct SessionAccepted: Decodable, Sendable {
    public let kind: String
    public let `protocol`: String
    public let sessionId: String
    public let serverTimeMilliseconds: UInt64

    enum CodingKeys: String, CodingKey {
        case kind, `protocol`
        case sessionId = "session_id"
        case serverTimeMilliseconds = "server_time_ms"
    }
}

private enum EnvelopeKind: String, Codable { case request, response, event, credit, cancel, ping, pong, close }

private struct Trace: Codable {
    let traceId: String
    let requestId: String
    enum CodingKeys: String, CodingKey { case traceId = "trace_id", requestId = "request_id" }
}

private struct Envelope: Codable {
    let `protocol`: String
    let kind: EnvelopeKind
    let sequence: UInt64
    let messageId: String
    let replyTo: String?
    let routeId: String?
    let routeEpoch: UInt64?
    let operation: String?
    let scope: Scope?
    let trace: Trace?
    let deadlineMilliseconds: UInt64?
    let payload: JSONValue?
    let error: WireError?

    enum CodingKeys: String, CodingKey {
        case `protocol`, kind, sequence, operation, scope, trace, payload, error
        case messageId = "message_id"
        case replyTo = "reply_to"
        case routeId = "route_id"
        case routeEpoch = "route_epoch"
        case deadlineMilliseconds = "deadline_ms"
    }
}

private struct Pending {
    let effect: EffectKind
    let continuation: CheckedContinuation<JSONValue, Error>
}

public actor HypermidClient {
    private let transport: any HypermidTransport
    private let credential: Data
    private let scope: Scope
    private let connectionClass: ConnectionClass
    private var outboundSequence: UInt64 = 1
    private var inboundSequence: UInt64 = 1
    private var identifierSequence: UInt64 = 0
    private var pending: [String: Pending] = [:]
    private var eventContinuations: [UUID: AsyncStream<ClientEvent>.Continuation] = [:]
    private var subscriptionContinuations: [String: AsyncThrowingStream<ScopedEvent, Error>.Continuation] = [:]
    private var reader: Task<Void, Never>?
    private var writeTail: Task<Void, Error>?
    private var accepted: SessionAccepted?
    private var closed = false

    public init(
        transport: any HypermidTransport,
        credential: Data,
        scope: Scope,
        connectionClass: ConnectionClass = .client
    ) throws {
        guard credential.count == 32 else {
            throw HypermidClientError.protocolViolation("connection credential must contain 32 bytes")
        }
        self.transport = transport
        self.credential = credential
        self.scope = scope
        self.connectionClass = connectionClass
    }

    public func connect() async throws -> SessionAccepted {
        guard accepted == nil else { throw HypermidClientError.protocolViolation("client is already connected") }
        var random = SystemRandomNumberGenerator()
        let nonce = Data((0..<32).map { _ in UInt8.random(in: .min ... .max, using: &random) })
        let hello = ClientHello(
            clientNonce: nonce.map { String(format: "%02x", $0) }.joined(),
            connectionClass: connectionClass,
            scope: scope
        )
        try await writeFrame(transport, hello)
        let challenge: ServerChallenge = try await readFrame(transport, ServerChallenge.self)
        guard challenge.kind == "challenge", challenge.protocol == hypermidProtocol,
              challenge.authMethod == "hmac_sha256",
              let serverNonce = Data(lowercaseHex: challenge.serverNonce, count: 32) else {
            throw HypermidClientError.protocolViolation("invalid authentication challenge")
        }
        let proof = authenticationProof(
            credential: credential,
            clientNonce: nonce,
            serverNonce: serverNonce,
            daemonId: try validIdentifier(challenge.daemonInstanceId, "daemon_instance_id"),
            connectionClass: connectionClass,
            scope: scope
        )
        try await writeFrame(transport, ["kind": "authenticate", "proof": proof])
        let session: SessionAccepted = try await readFrame(transport, SessionAccepted.self)
        guard session.kind == "accepted", session.protocol == hypermidProtocol else {
            throw HypermidClientError.protocolViolation("daemon did not accept hypermid.v1")
        }
        accepted = session
        reader = Task { await self.readLoop() }
        return session
    }

    public func events() -> AsyncStream<ClientEvent> {
        let id = UUID()
        return AsyncStream { continuation in
            eventContinuations[id] = continuation
            continuation.onTermination = { _ in Task { await self.removeEventContinuation(id) } }
        }
    }

    public func request(
        operation: String,
        payload: JSONValue,
        effect: EffectKind = .query,
        deadlineMilliseconds: UInt64? = nil
    ) async throws -> JSONValue {
        guard accepted != nil, !closed else { throw HypermidClientError.connectionEnded }
        let messageId = try nextId("request")
        let deadline = deadlineMilliseconds ?? currentMilliseconds() + 30_000
        let envelope = Envelope(
            protocol: hypermidProtocol,
            kind: .request,
            sequence: try nextSequence(),
            messageId: messageId,
            replyTo: nil,
            routeId: "control",
            routeEpoch: 1,
            operation: operation,
            scope: scope,
            trace: Trace(traceId: try nextId("trace"), requestId: messageId),
            deadlineMilliseconds: deadline,
            payload: payload,
            error: nil
        )
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                pending[messageId] = Pending(effect: effect, continuation: continuation)
                Task {
                    do {
                        try await self.send(envelope)
                        let remaining = deadline > currentMilliseconds() ? deadline - currentMilliseconds() : 0
                        try? await Task.sleep(nanoseconds: min(remaining, 86_400_000) * 1_000_000)
                        await self.deadlineExpired(messageId)
                    }
                    catch { await self.writeFailed(messageId, effect: effect) }
                }
            }
        } onCancel: {
            Task { await self.cancelPending(messageId) }
        }
    }

    public func describe() async throws -> JSONValue { try await request(operation: "server.describe", payload: .object([:])) }
    public func passthrough(_ payload: JSONValue) async throws -> JSONValue {
        let response = try await request(operation: "passthrough", payload: payload)
        guard case .object(let value) = response, value["mode"] == .string("passthrough") else {
            throw HypermidClientError.protocolViolation("daemon returned an invalid passthrough response")
        }
        return value["payload"] ?? .null
    }

    public func effectStatus(_ effectId: String) async throws -> JSONValue {
        try await request(operation: "effects.status", payload: .object(["effect_id": .string(try validIdentifier(effectId, "effect_id"))]))
    }

    public func publishEvent(
        eventId: String,
        topic: String,
        scope requestedScope: Scope,
        atMilliseconds: UInt64,
        schemaName: String,
        schemaVersion: UInt64,
        payload: JSONValue
    ) async throws -> ScopedEvent {
        guard requestedScope == scope else {
            throw HypermidClientError.protocolViolation("event scope does not match the authenticated session")
        }
        let result = try await request(
            operation: "events.publish",
            payload: .object([
                "event_id": .string(try validIdentifier(eventId, "event_id")),
                "topic": .string(try validTopic(topic)),
                "scope": scopeWire(requestedScope),
                "at_ms": .integer(try wireInteger(atMilliseconds, "at_ms")),
                "schema_name": .string(try wireStringValue(schemaName, "schema_name", maximum: 160)),
                "schema_version": .integer(try wireInteger(schemaVersion, "schema_version")),
                "trace": .null,
                "payload": payload,
            ]),
            effect: .mutation
        )
        return try ScopedEvent.fromWire(result)
    }

    public func subscribeEvents(
        consumerId: String,
        scope requestedScope: Scope,
        topicFilter: String,
        after: Cursor? = nil,
        resumeStore: SubscriptionResumeStore? = nil
    ) async throws -> DurableSubscription {
        guard requestedScope == scope else {
            throw HypermidClientError.protocolViolation("subscription scope does not match the authenticated session")
        }
        let consumerId = try validIdentifier(consumerId, "consumer_id")
        let topicFilter = try validTopicFilter(topicFilter)
        let stored = try resumeStore?.load()
        if let stored, stored.consumerId != consumerId || stored.scope != requestedScope || stored.topicFilter != topicFilter {
            throw HypermidClientError.protocolViolation("stored subscription identity does not match this request")
        }
        if let stored, let after, stored.cursor != after {
            throw HypermidClientError.protocolViolation("explicit cursor conflicts with durable resume state")
        }
        guard subscriptionContinuations[consumerId] == nil else {
            throw HypermidClientError.protocolViolation("consumer already has an active subscription")
        }
        let effectiveAfter = stored?.cursor ?? after
        var continuation: AsyncThrowingStream<ScopedEvent, Error>.Continuation?
        let stream = AsyncThrowingStream<ScopedEvent, Error> { continuation = $0 }
        guard let continuation else {
            throw HypermidClientError.protocolViolation("subscription stream could not be created")
        }
        subscriptionContinuations[consumerId] = continuation

        var requestFields: [String: JSONValue] = [
            "consumer_id": .string(consumerId),
            "scope": scopeWire(requestedScope),
            "topic_filter": .string(topicFilter),
        ]
        if let effectiveAfter { requestFields["after"] = cursorWire(effectiveAfter) }
        let result: JSONValue
        do {
            result = try await request(operation: "events.subscribe", payload: .object(requestFields))
        } catch {
            finishSubscription(consumerId, error: error)
            throw error
        }
        do {
            guard case .object(let fields) = result,
                  case .string(let returnedId)? = fields["subscription_id"],
                  returnedId == consumerId,
                  let cursorValue = fields["cursor"],
                  case .array(let replayValues)? = fields["replay"] else {
                throw HypermidClientError.protocolViolation("daemon returned an invalid subscription snapshot")
            }
            let snapshotCursor = try decodeWire(Cursor.self, cursorValue, "cursor")
            let replay = try replayValues.map(ScopedEvent.fromWire)
            let initialCursor = try effectiveAfter ?? Cursor(epoch: snapshotCursor.epoch, sequence: 0)
            let point = try stored ?? SubscriptionResumePoint(
                consumerId: consumerId, scope: requestedScope,
                topicFilter: topicFilter, cursor: initialCursor
            )
            return DurableSubscription(
                client: self,
                snapshot: SubscriptionSnapshot(
                    subscriptionId: consumerId, cursor: snapshotCursor, replay: replay
                ),
                point: point,
                resumeStore: resumeStore,
                stream: stream
            )
        } catch {
            finishSubscription(consumerId, error: error)
            throw error
        }
    }

    public func resumeEvents(
        consumerId: String,
        scope: Scope,
        topicFilter: String,
        after: Cursor,
        resumeStore: SubscriptionResumeStore? = nil
    ) async throws -> DurableSubscription {
        try await subscribeEvents(
            consumerId: consumerId, scope: scope, topicFilter: topicFilter,
            after: after, resumeStore: resumeStore
        )
    }

    public func acknowledgeEvent(
        consumerId: String,
        eventId: String,
        scope requestedScope: Scope
    ) async throws -> AcknowledgeReceipt {
        guard requestedScope == scope else {
            throw HypermidClientError.protocolViolation("acknowledgement scope does not match the authenticated session")
        }
        let result = try await request(
            operation: "events.ack",
            payload: .object([
                "consumer_id": .string(try validIdentifier(consumerId, "consumer_id")),
                "event_id": .string(try validIdentifier(eventId, "event_id")),
            ])
        )
        guard case .object(let fields) = result, fields["acknowledged"] == .bool(true) else {
            throw HypermidClientError.protocolViolation("daemon returned an invalid acknowledgement")
        }
        return AcknowledgeReceipt(acknowledged: true)
    }

    public func unsubscribeEvents(consumerId: String, scope requestedScope: Scope) async throws {
        guard requestedScope == scope else {
            throw HypermidClientError.protocolViolation("unsubscribe scope does not match the authenticated session")
        }
        let consumerId = try validIdentifier(consumerId, "consumer_id")
        defer { finishSubscription(consumerId, error: nil) }
        let result = try await request(
            operation: "events.unsubscribe",
            payload: .object(["consumer_id": .string(consumerId)])
        )
        guard case .object(let fields) = result, fields["unsubscribed"] == .bool(true) else {
            throw HypermidClientError.protocolViolation("daemon returned an invalid unsubscribe response")
        }
    }

    public func cancel(_ target: String) async throws {
        let envelope = Envelope(
            protocol: hypermidProtocol, kind: .cancel, sequence: try nextSequence(),
            messageId: try nextId("cancel"), replyTo: try validIdentifier(target, "reply_to"),
            routeId: nil, routeEpoch: nil, operation: nil, scope: nil, trace: nil,
            deadlineMilliseconds: nil, payload: nil, error: nil
        )
        try await send(envelope)
    }

    public func close() async {
        guard !closed else { return }
        closed = true
        if accepted != nil, let sequence = try? nextSequence(), let identifier = try? nextId("close") {
            let envelope = Envelope(
                protocol: hypermidProtocol, kind: .close, sequence: sequence, messageId: identifier,
                replyTo: nil, routeId: nil, routeEpoch: nil, operation: nil, scope: nil,
                trace: nil, deadlineMilliseconds: nil, payload: nil, error: nil
            )
            try? await send(envelope)
        }
        await transport.close()
        reader?.cancel()
        failPending()
    }

    private func readLoop() async {
        do {
            while !closed {
                let envelope: Envelope = try await readFrame(transport, Envelope.self)
                guard envelope.protocol == hypermidProtocol, envelope.sequence == inboundSequence,
                      envelope.sequence <= maximumSafeInteger else {
                    throw HypermidClientError.protocolViolation("replayed or reordered frame")
                }
                inboundSequence += 1
                switch envelope.kind {
                case .response:
                    guard let correlation = envelope.replyTo, let request = pending.removeValue(forKey: correlation) else { continue }
                    if let error = envelope.error { request.continuation.resume(throwing: HypermidClientError.remote(error)) }
                    else { request.continuation.resume(returning: envelope.payload ?? .null) }
                case .event:
                    let event = ClientEvent(messageId: envelope.messageId, operation: envelope.operation, payload: envelope.payload, error: envelope.error)
                    for continuation in eventContinuations.values { continuation.yield(event) }
                    if envelope.operation == "events.deliver",
                       case .object(let delivery)? = envelope.payload,
                       case .string(let subscriptionId)? = delivery["subscription_id"],
                       let eventValue = delivery["event"],
                       let continuation = subscriptionContinuations[subscriptionId] {
                        do { continuation.yield(try ScopedEvent.fromWire(eventValue)) }
                        catch { finishSubscription(subscriptionId, error: error) }
                    }
                case .ping: continue
                case .close: throw HypermidClientError.connectionEnded
                default: throw HypermidClientError.protocolViolation("unexpected inbound envelope")
                }
            }
        } catch {
            closed = true
            failPending()
        }
    }

    private func writeFailed(_ messageId: String, effect: EffectKind) {
        guard let request = pending.removeValue(forKey: messageId) else { return }
        request.continuation.resume(throwing: effect == .mutation ? HypermidClientError.outcomeUnknown : HypermidClientError.connectionEnded)
    }

    private func cancelPending(_ messageId: String) async {
        guard let request = pending.removeValue(forKey: messageId) else { return }
        try? await cancel(messageId)
        request.continuation.resume(throwing: request.effect == .mutation ? HypermidClientError.outcomeUnknown : HypermidClientError.cancelled)
    }

    private func deadlineExpired(_ messageId: String) async {
        guard let request = pending.removeValue(forKey: messageId) else { return }
        try? await cancel(messageId)
        request.continuation.resume(throwing: request.effect == .mutation ? HypermidClientError.outcomeUnknown : HypermidClientError.timedOut)
    }

    private func send<T: Encodable>(_ value: T) async throws {
        let frame = try encodedFrame(value)
        let previous = writeTail
        let transport = self.transport
        let current = Task {
            if let previous { try await previous.value }
            try await transport.write(frame)
        }
        writeTail = current
        try await current.value
    }

    private func failPending() {
        let current = pending
        pending.removeAll()
        for request in current.values {
            request.continuation.resume(throwing: request.effect == .mutation ? HypermidClientError.outcomeUnknown : HypermidClientError.connectionEnded)
        }
        for continuation in eventContinuations.values { continuation.finish() }
        eventContinuations.removeAll()
        for continuation in subscriptionContinuations.values {
            continuation.finish(throwing: HypermidClientError.connectionEnded)
        }
        subscriptionContinuations.removeAll()
    }

    private func nextSequence() throws -> UInt64 {
        guard outboundSequence <= maximumSafeInteger else { throw HypermidClientError.protocolViolation("sequence exhausted") }
        defer { outboundSequence += 1 }
        return outboundSequence
    }

    private func nextId(_ prefix: String) throws -> String {
        defer { identifierSequence += 1 }
        return try validIdentifier("\(prefix)-\(String(currentMilliseconds(), radix: 16))-\(String(identifierSequence, radix: 16))", "message_id")
    }

    private func removeEventContinuation(_ id: UUID) { eventContinuations.removeValue(forKey: id) }

    private func finishSubscription(_ consumerId: String, error: Error?) {
        guard let continuation = subscriptionContinuations.removeValue(forKey: consumerId) else { return }
        if let error { continuation.finish(throwing: error) }
        else { continuation.finish() }
    }
}

private func canonicalJSON(_ value: JSONValue) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    return try encoder.encode(value)
}

private func decodeWire<T: Decodable>(_ type: T.Type, _ value: JSONValue?, _ field: String) throws -> T {
    guard let value else { throw HypermidClientError.protocolViolation("\(field) is missing") }
    do { return try JSONDecoder().decode(type, from: canonicalJSON(value)) }
    catch let error as HypermidClientError { throw error }
    catch { throw HypermidClientError.protocolViolation("\(field) is invalid") }
}

private func requireWireFields(
    _ value: JSONValue?,
    required: Set<String>,
    optional: Set<String> = [],
    field: String
) throws {
    guard case .object(let fields)? = value,
          required.isSubset(of: fields.keys),
          Set(fields.keys).subtracting(required).isSubset(of: optional) else {
        throw HypermidClientError.protocolViolation("\(field) fields do not match hypermid.v1")
    }
}

private func wireString(_ value: JSONValue?, _ field: String, maximum: Int) throws -> String {
    guard case .string(let result)? = value, !result.isEmpty, result.utf8.count <= maximum else {
        throw HypermidClientError.protocolViolation("\(field) is invalid")
    }
    return result
}

private func wireStringValue(_ value: String, _ field: String, maximum: Int) throws -> String {
    guard !value.isEmpty, value.utf8.count <= maximum else {
        throw HypermidClientError.protocolViolation("\(field) is invalid")
    }
    return value
}

private func wireIdentifier(_ value: JSONValue?, _ field: String) throws -> String {
    try validIdentifier(wireString(value, field, maximum: 160), field)
}

private func wireUnsigned(_ value: JSONValue?, _ field: String, minimum: UInt64) throws -> UInt64 {
    guard case .integer(let integer)? = value, integer >= 0 else {
        throw HypermidClientError.protocolViolation("\(field) is invalid")
    }
    let result = UInt64(integer)
    guard result >= minimum, result <= maximumSafeInteger else {
        throw HypermidClientError.protocolViolation("\(field) is outside the interoperable range")
    }
    return result
}

private func wireInteger(_ value: UInt64, _ field: String) throws -> Int64 {
    guard value <= maximumSafeInteger else {
        throw HypermidClientError.protocolViolation("\(field) is outside the interoperable range")
    }
    return Int64(value)
}

private func wireDigest(_ value: JSONValue?) throws -> String {
    let digest = try wireString(value, "payload_digest", maximum: 64)
    guard digest.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
        throw HypermidClientError.protocolViolation("payload_digest is invalid")
    }
    return digest
}

private func validateScopeWire(_ scope: Scope) throws {
    _ = try validIdentifier(scope.ownerId, "owner_id")
    _ = try validIdentifier(scope.projectId, "project_id")
    if let workspaceId = scope.workspaceId { _ = try validIdentifier(workspaceId, "workspace_id") }
}

private func validatePrincipal(_ principal: Principal) throws {
    _ = try validIdentifier(principal.id, "principal.id")
    guard principal.scopes.count <= 256,
          Set(principal.scopes).count == principal.scopes.count,
          principal.scopes.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 160 }) else {
        throw HypermidClientError.protocolViolation("principal scopes are invalid")
    }
    if let moduleId = principal.moduleId { _ = try validIdentifier(moduleId, "principal.module_id") }
    if let generation = principal.spawnGeneration {
        guard generation > 0, generation <= maximumSafeInteger else {
            throw HypermidClientError.protocolViolation("principal spawn_generation is invalid")
        }
    }
}

private func validTopic(_ value: String) throws -> String {
    guard !value.isEmpty, value.utf8.count <= 256,
          value.range(of: "^[a-z][a-z0-9._-]*$", options: .regularExpression) != nil else {
        throw HypermidClientError.protocolViolation("invalid event topic")
    }
    return value
}

private func validTopicFilter(_ value: String) throws -> String {
    if value.hasSuffix(".*") {
        _ = try validTopic(String(value.dropLast(2)))
    } else {
        _ = try validTopic(value)
    }
    return value
}

public func readFrame<T: Decodable>(_ transport: any HypermidTransport, _ type: T.Type) async throws -> T {
    let header = try await transport.readExactly(4)
    guard header.count == 4 else { throw HypermidClientError.protocolViolation("truncated frame header") }
    let length = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
    guard length > 0 else { throw HypermidClientError.protocolViolation("empty frame") }
    guard length <= UInt32(hypermidMaximumFrameBytes) else { throw HypermidClientError.protocolViolation("declared frame exceeds 8 MiB") }
    let body = try await transport.readExactly(Int(length))
    guard body.count == Int(length) else { throw HypermidClientError.protocolViolation("truncated frame body") }
    return try JSONDecoder().decode(type, from: body)
}

public func writeFrame<T: Encodable>(_ transport: any HypermidTransport, _ value: T) async throws {
    try await transport.write(encodedFrame(value))
}

private func encodedFrame<T: Encodable>(_ value: T) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    let body = try encoder.encode(value)
    guard !body.isEmpty, body.count <= hypermidMaximumFrameBytes else { throw HypermidClientError.protocolViolation("frame size is invalid") }
    var length = UInt32(body.count).bigEndian
    var frame = Data(bytes: &length, count: 4)
    frame.append(body)
    return frame
}

private func authenticationProof(
    credential: Data,
    clientNonce: Data,
    serverNonce: Data,
    daemonId: String,
    connectionClass: ConnectionClass,
    scope: Scope
) -> String {
    var transcript = Data("hypermid-auth-v1\0".utf8)
    for field in [
        clientNonce, serverNonce, Data(hypermidProtocol.utf8), Data(daemonId.utf8),
        Data(connectionClass.rawValue.utf8), Data(scope.ownerId.utf8), Data(scope.projectId.utf8),
        Data((scope.workspaceId ?? "").utf8),
    ] {
        var length = UInt32(field.count).bigEndian
        transcript.append(Data(bytes: &length, count: 4))
        transcript.append(field)
    }
    let signature = HMAC<SHA256>.authenticationCode(for: transcript, using: SymmetricKey(data: credential))
    return Data(signature).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

private func validIdentifier(_ value: String, _ field: String) throws -> String {
    guard value.utf8.count <= 160, value.range(of: #"^[A-Za-z0-9][A-Za-z0-9._:-]*$"#, options: .regularExpression) != nil else {
        throw HypermidClientError.protocolViolation("invalid \(field)")
    }
    return value
}

private func currentMilliseconds() -> UInt64 { UInt64(Date().timeIntervalSince1970 * 1_000) }

private extension Data {
    init?(lowercaseHex value: String, count: Int) {
        guard value.count == count * 2, value.range(of: #"^[0-9a-f]+$"#, options: .regularExpression) != nil else { return nil }
        self.init(capacity: count)
        var index = value.startIndex
        for _ in 0..<count {
            let next = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index..<next], radix: 16) else { return nil }
            append(byte)
            index = next
        }
    }
}
