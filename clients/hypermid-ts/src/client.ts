export const PROTOCOL = "hypermid.v1";
export const MAX_FRAME_BYTES = 8 * 1024 * 1024;
export const MAX_CHUNK_BYTES = 64 * 1024;

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type EffectKind = "query" | "mutation";
export type EffectState = "not_started" | "committed" | "unknown";
export type ConnectionClass = "client" | "module" | "device" | "service";

export interface Scope {
  owner_id: string;
  project_id: string;
  workspace_id?: string;
}

export interface Cursor {
  epoch: number;
  sequence: number;
}

export interface Trace {
  trace_id: string;
  request_id: string;
}

export interface WireError {
  code: string;
  message: string;
  retryable: boolean;
  retry_after_ms?: number;
  effect_state?: EffectState;
}

export interface ConnectionLimits {
  max_frame_bytes: number;
  event_chunk_bytes: number;
  max_routes: number;
  max_inflight_requests: number;
}

export interface SessionAccepted {
  kind: "accepted";
  protocol: typeof PROTOCOL;
  session_id: string;
  principal: {
    id: string;
    kind: "local_user" | "supervised_module" | "device" | "service";
    scopes: string[];
    module_id?: string;
    spawn_generation?: number;
  };
  limits: ConnectionLimits;
  server_time_ms: number;
}

export interface EventChunk {
  event_id: string;
  chunk_index: number;
  chunk_count: number;
  digest: string;
  data: string;
}

export interface ClientEvent {
  message_id: string;
  operation?: string;
  payload?: Json;
  error?: WireError;
}

export interface Principal {
  id: string;
  kind: "local_user" | "supervised_module" | "device" | "service";
  scopes: string[];
  module_id?: string;
  spawn_generation?: number;
}

export interface ScopedEvent {
  event_id: string;
  topic: string;
  producer: Principal;
  scope: Scope;
  at_ms: number;
  schema_name: string;
  schema_version: number;
  payload_digest: string;
  trace?: Trace;
  cursor: Cursor;
  payload: Json;
  delivery_count: number;
}

export interface SubscriptionSnapshot {
  subscription_id: string;
  cursor: Cursor;
  replay: ScopedEvent[];
}

export interface ResumePoint {
  consumer_id: string;
  scope: Scope;
  topic_filter: string;
  cursor: Cursor;
}

export interface ResumeStore {
  load(): Promise<ResumePoint | null> | ResumePoint | null;
  save(point: ResumePoint): Promise<void> | void;
}

export interface SubscriptionOptions {
  consumerId: string;
  scope: Scope;
  topicFilter: string;
  after?: Cursor;
  resumeStore?: ResumeStore;
}

export interface ResumeSubscriptionOptions extends SubscriptionOptions {
  after: Cursor;
}

export interface AcknowledgeReceipt {
  acknowledged: true;
}

export interface ByteTransport {
  readExactly(length: number): Promise<Uint8Array>;
  write(bytes: Uint8Array): Promise<void>;
  close(): Promise<void>;
}

type EnvelopeKind = "request" | "response" | "event" | "credit" | "cancel" | "ping" | "pong" | "close";

interface Envelope {
  protocol: typeof PROTOCOL;
  kind: EnvelopeKind;
  sequence: number;
  message_id: string;
  reply_to?: string;
  route_id?: string;
  route_epoch?: number;
  operation?: string;
  scope?: Scope;
  trace?: Trace;
  deadline_ms?: number;
  payload?: Json;
  error?: WireError;
}

interface Pending {
  effect: EffectKind;
  resolve(value: Json): void;
  reject(error: Error): void;
}

export class HypermidError extends Error {}

export class HypermidRemoteError extends HypermidError {
  constructor(readonly detail: WireError) {
    super(`${detail.code}: ${detail.message}`);
  }
}

export class HypermidOutcomeUnknown extends HypermidRemoteError {
  constructor() {
    super({
      code: "OUTCOME_UNKNOWN",
      message: "connection ended after mutation dispatch",
      retryable: false,
      effect_state: "unknown",
    });
  }
}

export class HypermidClient {
  private outboundSequence = 1;
  private inboundSequence = 1;
  private idSequence = 0;
  private pending = new Map<string, Pending>();
  private listeners = new Set<(event: ClientEvent) => void>();
  private writeTail: Promise<void> = Promise.resolve();
  private closed = false;
  private reader?: Promise<void>;
  private _session?: SessionAccepted;

  constructor(
    private readonly transport: ByteTransport,
    private readonly credential: Uint8Array,
    private readonly scope: Scope,
    private readonly connectionClass: ConnectionClass = "client",
  ) {
    if (credential.byteLength !== 32) throw new HypermidError("connection credential must contain 32 bytes");
    validateScope(scope);
  }

  get session(): SessionAccepted {
    if (!this._session) throw new HypermidError("client is not connected");
    return this._session;
  }

  async connect(): Promise<SessionAccepted> {
    if (this._session) throw new HypermidError("client is already connected");
    const nonce = crypto.getRandomValues(new Uint8Array(32));
    const hello = {
      kind: "hello",
      protocols: [PROTOCOL],
      client_nonce: hex(nonce),
      connection_class: this.connectionClass,
      scope: this.scope,
    };
    await writeFrame(this.transport, hello);
    const challenge = object(await readFrame(this.transport), "challenge");
    if (challenge.kind !== "challenge" || challenge.protocol !== PROTOCOL || challenge.auth_method !== "hmac_sha256") {
      throw new HypermidError("daemon returned an invalid authentication challenge");
    }
    const serverNonce = decodeHex(string(challenge.server_nonce, "server_nonce"), 32);
    const daemonId = identifier(challenge.daemon_instance_id, "daemon_instance_id");
    const proof = await authenticationProof(
      this.credential,
      nonce,
      serverNonce,
      daemonId,
      this.connectionClass,
      this.scope,
    );
    await writeFrame(this.transport, { kind: "authenticate", proof });
    this._session = decodeSession(await readFrame(this.transport));
    this.reader = this.readLoop();
    void this.reader.catch(() => undefined);
    return this._session;
  }

  onEvent(listener: (event: ClientEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async request(
    operation: string,
    payload: Json,
    options: { effect?: EffectKind; deadlineMs?: number; signal?: AbortSignal } = {},
  ): Promise<Json> {
    if (!this._session || this.closed) throw new HypermidError("client is not connected");
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(operation)) throw new HypermidError("invalid operation");
    const effect = options.effect ?? "query";
    const messageId = this.newId("request");
    const deadline = options.deadlineMs ?? Date.now() + 30_000;
    const envelope: Envelope = {
      protocol: PROTOCOL,
      kind: "request",
      sequence: this.outboundSequence++,
      message_id: messageId,
      route_id: "control",
      route_epoch: 1,
      operation,
      scope: this.scope,
      trace: { trace_id: this.newId("trace"), request_id: messageId },
      deadline_ms: deadline,
      payload,
    };
    let resolve!: (value: Json) => void;
    let reject!: (error: Error) => void;
    const response = new Promise<Json>((ok, fail) => { resolve = ok; reject = fail; });
    void response.catch(() => undefined);
    this.pending.set(messageId, { effect, resolve, reject });
    try {
      await this.write(envelope);
    } catch (error) {
      this.pending.delete(messageId);
      if (effect === "mutation") throw new HypermidOutcomeUnknown();
      throw error;
    }
    const remaining = Math.max(1, deadline - Date.now());
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadlineFailure = new Promise<never>((_, fail) => {
      timer = setTimeout(() => fail(new HypermidError("request timed out")), remaining);
    });
    const abortFailure = options.signal
      ? new Promise<never>((_, fail) => {
          if (options.signal!.aborted) fail(new HypermidError("request cancelled"));
          else options.signal!.addEventListener("abort", () => fail(new HypermidError("request cancelled")), { once: true });
        })
      : new Promise<never>(() => undefined);
    try {
      return await Promise.race([response, deadlineFailure, abortFailure]);
    } catch (error) {
      if (this.pending.delete(messageId)) await this.cancel(messageId).catch(() => undefined);
      if (effect === "mutation") throw new HypermidOutcomeUnknown();
      throw error;
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  describe(): Promise<Json> {
    return this.request("server.describe", {});
  }

  passthrough(payload: Json): Promise<Json> {
    return this.request("passthrough", payload).then((response) => {
      const value = object(response, "passthrough response");
      if (value.mode !== "passthrough") throw new HypermidError("daemon returned an invalid passthrough response");
      return value.payload ?? null;
    });
  }

  effectStatus(effectId: string): Promise<Json> {
    return this.request("effects.status", { effect_id: identifier(effectId, "effect_id") });
  }

  async subscribeEvents(options: SubscriptionOptions): Promise<DurableSubscription> {
    const consumerId = identifier(options.consumerId, "consumer_id");
    validateScope(options.scope);
    if (!scopesEqual(options.scope, this.scope)) {
      throw new HypermidError("subscription scope does not match the authenticated session");
    }
    validateTopicFilter(options.topicFilter);
    if (options.after) validateCursor(options.after);
    const stored = options.resumeStore ? await options.resumeStore.load() : null;
    if (stored) {
      validateResumePoint(stored);
      if (stored.consumer_id !== consumerId
        || !scopesEqual(stored.scope, options.scope)
        || stored.topic_filter !== options.topicFilter) {
        throw new HypermidError("stored subscription identity does not match this request");
      }
      if (options.after && !cursorsEqual(options.after, stored.cursor)) {
        throw new HypermidError("explicit cursor conflicts with durable resume state");
      }
    }
    const after = stored?.cursor ?? options.after;
    const subscription = new DurableSubscription(
      this,
      stored ?? {
        consumer_id: consumerId,
        scope: copyScope(options.scope),
        topic_filter: options.topicFilter,
        cursor: after ?? { epoch: 1, sequence: 0 },
      },
      options.resumeStore,
    );
    const payload: { [key: string]: Json } = {
      consumer_id: consumerId,
      scope: scopeToJson(options.scope),
      topic_filter: options.topicFilter,
    };
    if (after) payload.after = { epoch: after.epoch, sequence: after.sequence };
    try {
      const raw = await this.request("events.subscribe", payload);
      const snapshot = await decodeSubscriptionSnapshot(raw, consumerId);
      subscription.initialize(snapshot, after ?? { epoch: snapshot.cursor.epoch, sequence: 0 });
      return subscription;
    } catch (error) {
      subscription.detach();
      throw error;
    }
  }

  resumeEvents(options: ResumeSubscriptionOptions): Promise<DurableSubscription> {
    validateCursor(options.after);
    return this.subscribeEvents(options);
  }

  async cancel(replyTo: string): Promise<void> {
    await this.write({
      protocol: PROTOCOL,
      kind: "cancel",
      sequence: this.outboundSequence++,
      message_id: this.newId("cancel"),
      reply_to: identifier(replyTo, "reply_to"),
    });
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this._session) {
      await this.write({
        protocol: PROTOCOL,
        kind: "close",
        sequence: this.outboundSequence++,
        message_id: this.newId("close"),
      }).catch(() => undefined);
    }
    await this.transport.close();
    await this.reader?.catch(() => undefined);
    this.failPending();
  }

  private write(envelope: Envelope): Promise<void> {
    const operation = this.writeTail.then(() => writeFrame(this.transport, envelope));
    this.writeTail = operation.catch(() => undefined);
    return operation;
  }

  private async readLoop(): Promise<void> {
    try {
      while (!this.closed) {
        const envelope = decodeEnvelope(await readFrame(this.transport));
        if (envelope.sequence !== this.inboundSequence++) throw new HypermidError("replayed or reordered frame");
        if (envelope.kind === "response") {
          const correlation = envelope.reply_to!;
          const pending = this.pending.get(correlation);
          if (!pending) continue;
          this.pending.delete(correlation);
          if (envelope.error) pending.reject(new HypermidRemoteError(envelope.error));
          else pending.resolve(envelope.payload ?? null);
        } else if (envelope.kind === "event") {
          const event = {
            message_id: envelope.message_id,
            operation: envelope.operation,
            payload: envelope.payload,
            error: envelope.error,
          };
          for (const listener of this.listeners) listener(event);
        } else if (envelope.kind === "close") {
          break;
        } else if (envelope.kind !== "ping") {
          throw new HypermidError(`unexpected inbound ${envelope.kind} frame`);
        }
      }
    } finally {
      this.closed = true;
      this.failPending();
    }
  }

  private failPending(): void {
    for (const pending of this.pending.values()) {
      pending.reject(pending.effect === "mutation" ? new HypermidOutcomeUnknown() : new HypermidError("connection ended before response"));
    }
    this.pending.clear();
  }

  private newId(prefix: string): string {
    return `${prefix}-${Date.now().toString(16)}-${(this.idSequence++).toString(16).padStart(8, "0")}`;
  }
}

export class DurableSubscription {
  private snapshotValue?: SubscriptionSnapshot;
  private replay: ScopedEvent[] = [];
  private live: ScopedEvent[] = [];
  private waiters: Array<{
    resolve(event: ScopedEvent): void;
    reject(error: Error): void;
  }> = [];
  private failure?: Error;
  private closed = false;
  private initialized = false;
  private lastSeen: Cursor;
  private deliveryTail: Promise<void> = Promise.resolve();
  private readonly removeListener: () => void;

  constructor(
    private readonly client: HypermidClient,
    private pointValue: ResumePoint,
    private readonly resumeStore?: ResumeStore,
  ) {
    this.lastSeen = { ...pointValue.cursor };
    this.removeListener = client.onEvent((event) => this.capture(event));
  }

  get snapshot(): SubscriptionSnapshot {
    if (!this.snapshotValue) throw new HypermidError("durable subscription is not initialized");
    return this.snapshotValue;
  }

  get resumePoint(): ResumePoint {
    return copyResumePoint(this.pointValue);
  }

  initialize(snapshot: SubscriptionSnapshot, initialCursor: Cursor): void {
    if (this.initialized) throw new HypermidError("durable subscription is already initialized");
    this.snapshotValue = snapshot;
    this.replay = [...snapshot.replay];
    this.lastSeen = { ...initialCursor };
    this.pointValue = { ...this.pointValue, cursor: { ...initialCursor } };
    this.initialized = true;
  }

  async nextEvent(): Promise<ScopedEvent> {
    if (!this.initialized) throw new HypermidError("durable subscription is not initialized");
    if (this.failure) throw this.failure;
    const event = this.replay.shift() ?? this.live.shift();
    if (event) return this.accept(event);
    if (this.closed) throw new HypermidError("durable subscription is closed");
    return new Promise<ScopedEvent>((resolve, reject) => {
      this.waiters.push({
        resolve: (delivered) => {
          try { resolve(this.accept(delivered)); }
          catch (error) { reject(asError(error)); }
        },
        reject,
      });
    });
  }

  async acknowledge(event: ScopedEvent): Promise<AcknowledgeReceipt> {
    if (!scopesEqual(event.scope, this.pointValue.scope)) {
      throw new HypermidError("cannot acknowledge an event from another scope");
    }
    const raw = await this.client.request(
      "events.ack",
      { consumer_id: this.pointValue.consumer_id, event_id: event.event_id },
      { effect: "mutation" },
    );
    const receipt = object(raw, "event acknowledgement");
    if (receipt.acknowledged !== true || Object.keys(receipt).length !== 1) {
      throw new HypermidError("daemon returned an invalid event acknowledgement");
    }
    this.pointValue = { ...this.pointValue, cursor: { ...event.cursor } };
    if (this.resumeStore) await this.resumeStore.save(copyResumePoint(this.pointValue));
    return { acknowledged: true };
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.detach();
    try {
      const raw = await this.client.request(
        "events.unsubscribe",
        { consumer_id: this.pointValue.consumer_id },
        { effect: "mutation" },
      );
      const receipt = object(raw, "event unsubscribe receipt");
      if (receipt.unsubscribed !== true || Object.keys(receipt).length !== 1) {
        throw new HypermidError("daemon returned an invalid event unsubscribe receipt");
      }
    } finally {
      const error = new HypermidError("durable subscription is closed");
      for (const waiter of this.waiters.splice(0)) waiter.reject(error);
    }
  }

  detach(): void {
    this.removeListener();
  }

  private capture(delivery: ClientEvent): void {
    if (this.closed || delivery.operation !== "events.deliver") return;
    this.deliveryTail = this.deliveryTail.then(async () => {
      if (delivery.error) throw new HypermidRemoteError(delivery.error);
      const payload = object(delivery.payload, "event delivery");
      const subscriptionId = identifier(payload.subscription_id, "subscription_id");
      if (subscriptionId !== this.pointValue.consumer_id) return;
      const event = await decodeScopedEvent(payload.event);
      const waiter = this.waiters.shift();
      if (waiter) waiter.resolve(event);
      else this.live.push(event);
    }).catch((error) => this.fail(asError(error)));
  }

  private accept(event: ScopedEvent): ScopedEvent {
    if (!scopesEqual(event.scope, this.pointValue.scope)) {
      throw new HypermidError("daemon delivered an event outside the subscription scope");
    }
    if (event.cursor.epoch !== this.lastSeen.epoch || event.cursor.sequence <= this.lastSeen.sequence) {
      throw new HypermidError("daemon delivered a replayed, reordered, or foreign-epoch event");
    }
    this.lastSeen = { ...event.cursor };
    return event;
  }

  private fail(error: Error): void {
    this.failure = error;
    this.closed = true;
    this.detach();
    for (const waiter of this.waiters.splice(0)) waiter.reject(error);
  }
}

export async function readFrame(transport: ByteTransport): Promise<Json> {
  const header = await transport.readExactly(4);
  if (header.byteLength !== 4) throw new HypermidError("truncated frame header");
  const length = new DataView(header.buffer, header.byteOffset, 4).getUint32(0, false);
  if (length === 0) throw new HypermidError("empty frame");
  if (length > MAX_FRAME_BYTES) throw new HypermidError("declared frame exceeds 8 MiB");
  const body = await transport.readExactly(length);
  if (body.byteLength !== length) throw new HypermidError("truncated frame body");
  return parseCanonicalJson(new TextDecoder("utf-8", { fatal: true }).decode(body));
}

export async function writeFrame(transport: ByteTransport, value: unknown): Promise<void> {
  const body = new TextEncoder().encode(canonicalJson(value));
  if (body.byteLength === 0 || body.byteLength > MAX_FRAME_BYTES) throw new HypermidError("frame size is invalid");
  const frame = new Uint8Array(4 + body.byteLength);
  new DataView(frame.buffer).setUint32(0, body.byteLength, false);
  frame.set(body, 4);
  await transport.write(frame);
}

export function canonicalJson(value: unknown): string {
  validateJson(value);
  return JSON.stringify(sortJson(value as Json));
}

export function decodeEventChunk(value: unknown): EventChunk {
  const raw = object(value, "event chunk");
  const result = {
    event_id: identifier(raw.event_id, "event_id"),
    chunk_index: integer(raw.chunk_index, "chunk_index", 0),
    chunk_count: integer(raw.chunk_count, "chunk_count", 1),
    digest: string(raw.digest, "digest"),
    data: string(raw.data, "data"),
  };
  if (result.chunk_index >= result.chunk_count) throw new HypermidError("invalid event chunk index");
  const decoded = decodeBase64(result.data);
  if (decoded.byteLength > MAX_CHUNK_BYTES) throw new HypermidError("event chunk exceeds 64 KiB");
  return result;
}

async function authenticationProof(
  credential: Uint8Array,
  clientNonce: Uint8Array,
  serverNonce: Uint8Array,
  daemonId: string,
  connectionClass: ConnectionClass,
  scope: Scope,
): Promise<string> {
  const encoder = new TextEncoder();
  const fields = [
    clientNonce,
    serverNonce,
    encoder.encode(PROTOCOL),
    encoder.encode(daemonId),
    encoder.encode(connectionClass),
    encoder.encode(scope.owner_id),
    encoder.encode(scope.project_id),
    encoder.encode(scope.workspace_id ?? ""),
  ];
  const size = 17 + fields.reduce((total, field) => total + 4 + field.byteLength, 0);
  const transcript = new Uint8Array(size);
  transcript.set(encoder.encode("hypermid-auth-v1\0"), 0);
  let offset = 17;
  for (const field of fields) {
    new DataView(transcript.buffer).setUint32(offset, field.byteLength, false);
    offset += 4;
    transcript.set(field, offset);
    offset += field.byteLength;
  }
  const key = await crypto.subtle.importKey("raw", new Uint8Array(credential).buffer, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, new Uint8Array(transcript).buffer));
  return base64Url(signature);
}

function decodeSession(value: Json): SessionAccepted {
  const raw = object(value, "accepted session");
  if (raw.kind !== "accepted" || raw.protocol !== PROTOCOL) throw new HypermidError("server did not accept hypermid.v1");
  const limits = object(raw.limits, "limits");
  if (limits.max_frame_bytes !== MAX_FRAME_BYTES || limits.event_chunk_bytes !== MAX_CHUNK_BYTES) {
    throw new HypermidError("server selected unsupported frame limits");
  }
  return raw as unknown as SessionAccepted;
}

function decodeEnvelope(value: Json): Envelope {
  const raw = object(value, "envelope");
  if (raw.protocol !== PROTOCOL) throw new HypermidError("unsupported envelope protocol");
  const kind = raw.kind;
  if (!["request", "response", "event", "credit", "cancel", "ping", "pong", "close"].includes(String(kind))) {
    throw new HypermidError("invalid envelope kind");
  }
  const result = raw as unknown as Envelope;
  integer(result.sequence, "sequence", 1);
  identifier(result.message_id, "message_id");
  if ((kind === "response" || kind === "cancel") && !result.reply_to) throw new HypermidError("missing correlation id");
  return result;
}

function parseCanonicalJson(text: string): Json {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new HypermidError("invalid JSON frame"); }
  validateJson(value);
  return value as Json;
}

function validateJson(value: unknown): void {
  if (value === null || typeof value === "boolean" || typeof value === "string") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new HypermidError("wire number is not interoperable");
    }
    return;
  }
  if (Array.isArray(value)) { for (const item of value) validateJson(item); return; }
  if (typeof value === "object") { for (const item of Object.values(value as object)) validateJson(item); return; }
  throw new HypermidError("value is not JSON");
}

function sortJson(value: Json): Json {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortJson(value[key])]));
  }
  return value;
}

function validateScope(scope: Scope): void {
  identifier(scope.owner_id, "owner_id");
  identifier(scope.project_id, "project_id");
  if (scope.workspace_id !== undefined) identifier(scope.workspace_id, "workspace_id");
}

function scopeToJson(scope: Scope): { [key: string]: Json } {
  const result: { [key: string]: Json } = {
    owner_id: scope.owner_id,
    project_id: scope.project_id,
  };
  if (scope.workspace_id !== undefined) result.workspace_id = scope.workspace_id;
  return result;
}

function copyScope(scope: Scope): Scope {
  return {
    owner_id: scope.owner_id,
    project_id: scope.project_id,
    ...(scope.workspace_id === undefined ? {} : { workspace_id: scope.workspace_id }),
  };
}

function scopesEqual(left: Scope, right: Scope): boolean {
  return left.owner_id === right.owner_id
    && left.project_id === right.project_id
    && left.workspace_id === right.workspace_id;
}

function validateCursor(cursor: Cursor): void {
  integer(cursor.epoch, "cursor.epoch", 1);
  integer(cursor.sequence, "cursor.sequence", 0);
}

function cursorsEqual(left: Cursor, right: Cursor): boolean {
  return left.epoch === right.epoch && left.sequence === right.sequence;
}

function validateTopicFilter(filter: string): void {
  const topic = filter.endsWith(".*") ? filter.slice(0, -2) : filter;
  if (topic.length === 0 || topic.length > 256 || !/^[a-z][a-z0-9._-]*$/.test(topic)) {
    throw new HypermidError("subscription topic filter is invalid");
  }
}

function validateResumePoint(point: ResumePoint): void {
  identifier(point.consumer_id, "consumer_id");
  validateScope(point.scope);
  validateTopicFilter(point.topic_filter);
  validateCursor(point.cursor);
}

function copyResumePoint(point: ResumePoint): ResumePoint {
  return {
    consumer_id: point.consumer_id,
    scope: copyScope(point.scope),
    topic_filter: point.topic_filter,
    cursor: { ...point.cursor },
  };
}

async function decodeSubscriptionSnapshot(value: Json, consumerId: string): Promise<SubscriptionSnapshot> {
  const raw = exactObject(value, "subscription snapshot", ["subscription_id", "cursor", "replay"]);
  const subscriptionId = identifier(raw.subscription_id, "subscription_id");
  if (subscriptionId !== consumerId) throw new HypermidError("daemon changed the durable consumer identity");
  const cursor = decodeCursor(raw.cursor, "subscription cursor");
  if (!Array.isArray(raw.replay)) throw new HypermidError("subscription replay must be an array");
  const replay = await Promise.all(raw.replay.map((event) => decodeScopedEvent(event)));
  return { subscription_id: subscriptionId, cursor, replay };
}

async function decodeScopedEvent(value: unknown): Promise<ScopedEvent> {
  const raw = exactObject(value, "scoped event", [
    "event_id", "topic", "producer", "scope", "at_ms", "schema_name", "schema_version",
    "payload_digest", "cursor", "payload",
  ], ["trace", "delivery_count", "subscription_id"]);
  const topic = string(raw.topic, "event topic");
  if (topic.length === 0 || topic.length > 256) throw new HypermidError("event topic is invalid");
  const schemaName = string(raw.schema_name, "schema_name");
  if (schemaName.length === 0 || schemaName.length > 160) throw new HypermidError("event schema name is invalid");
  const payloadDigest = string(raw.payload_digest, "payload_digest");
  if (!/^[0-9a-f]{64}$/.test(payloadDigest)) throw new HypermidError("event payload digest is invalid");
  const payload = raw.payload as Json;
  const actualDigest = await sha256Hex(new TextEncoder().encode(canonicalJson(payload)));
  if (actualDigest !== payloadDigest) throw new HypermidError("event payload digest does not match its payload");
  const deliveryCount = raw.delivery_count === undefined ? 1 : integer(raw.delivery_count, "delivery_count", 1);
  return {
    event_id: identifier(raw.event_id, "event_id"),
    topic,
    producer: decodePrincipal(raw.producer),
    scope: decodeScope(raw.scope),
    at_ms: integer(raw.at_ms, "at_ms", 0),
    schema_name: schemaName,
    schema_version: integer(raw.schema_version, "schema_version", 0),
    payload_digest: payloadDigest,
    ...(raw.trace === undefined || raw.trace === null ? {} : { trace: decodeTrace(raw.trace) }),
    cursor: decodeCursor(raw.cursor, "event cursor"),
    payload,
    delivery_count: deliveryCount,
  };
}

function decodePrincipal(value: unknown): Principal {
  const raw = exactObject(value, "principal", ["id", "kind", "scopes"], ["module_id", "spawn_generation"]);
  const kind = string(raw.kind, "principal.kind");
  if (!["local_user", "supervised_module", "device", "service"].includes(kind)) {
    throw new HypermidError("principal kind is invalid");
  }
  if (!Array.isArray(raw.scopes) || raw.scopes.some((scope) => typeof scope !== "string")) {
    throw new HypermidError("principal scopes are invalid");
  }
  return {
    id: identifier(raw.id, "principal.id"),
    kind: kind as Principal["kind"],
    scopes: raw.scopes as string[],
    ...(raw.module_id === undefined || raw.module_id === null ? {} : { module_id: identifier(raw.module_id, "module_id") }),
    ...(raw.spawn_generation === undefined || raw.spawn_generation === null
      ? {}
      : { spawn_generation: integer(raw.spawn_generation, "spawn_generation", 0) }),
  };
}

function decodeScope(value: unknown): Scope {
  const raw = exactObject(value, "scope", ["owner_id", "project_id"], ["workspace_id"]);
  return {
    owner_id: identifier(raw.owner_id, "owner_id"),
    project_id: identifier(raw.project_id, "project_id"),
    ...(raw.workspace_id === undefined || raw.workspace_id === null
      ? {}
      : { workspace_id: identifier(raw.workspace_id, "workspace_id") }),
  };
}

function decodeCursor(value: unknown, name: string): Cursor {
  const raw = exactObject(value, name, ["epoch", "sequence"]);
  return {
    epoch: integer(raw.epoch, `${name}.epoch`, 1),
    sequence: integer(raw.sequence, `${name}.sequence`, 0),
  };
}

function decodeTrace(value: unknown): Trace {
  const raw = exactObject(value, "trace", ["trace_id", "request_id"]);
  return {
    trace_id: identifier(raw.trace_id, "trace_id"),
    request_id: identifier(raw.request_id, "request_id"),
  };
}

function exactObject(
  value: unknown,
  name: string,
  required: string[],
  optional: string[] = [],
): Record<string, Json> {
  const raw = object(value, name);
  const keys = new Set(Object.keys(raw));
  if (required.some((key) => !keys.has(key)) || [...keys].some((key) => !required.includes(key) && !optional.includes(key))) {
    throw new HypermidError(`${name} fields do not match hypermid.v1`);
  }
  return raw;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer),
  );
  return hex(digest);
}

function asError(value: unknown): Error {
  return value instanceof Error ? value : new HypermidError(String(value));
}

function object(value: unknown, name: string): Record<string, Json> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new HypermidError(`${name} must be an object`);
  return value as Record<string, Json>;
}

function string(value: unknown, name: string): string {
  if (typeof value !== "string") throw new HypermidError(`${name} must be a string`);
  return value;
}

function identifier(value: unknown, name: string): string {
  const result = string(value, name);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(result)) throw new HypermidError(`${name} is invalid`);
  return result;
}

function integer(value: unknown, name: string, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) throw new HypermidError(`${name} is invalid`);
  return value;
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function decodeHex(value: string, bytes: number): Uint8Array {
  if (value.length !== bytes * 2 || !/^[0-9a-f]+$/.test(value)) throw new HypermidError("invalid lowercase hexadecimal value");
  return Uint8Array.from({ length: bytes }, (_, index) => Number.parseInt(value.slice(index * 2, index * 2 + 2), 16));
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeBase64(value: string): Uint8Array {
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new HypermidError("invalid base64 chunk data");
  }
}
