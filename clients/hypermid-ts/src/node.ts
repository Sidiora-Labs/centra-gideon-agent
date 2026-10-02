import { readFileSync, statSync } from "node:fs";
import { connect as tlsConnect, type TLSSocket } from "node:tls";
import { PROTOCOL, HypermidError, type ByteTransport, type Scope, HypermidClient } from "./client.js";

export interface NodeConnectionRecord {
  endpoint: string;
  protocol: string;
  credential_id: string;
  secret_b64: string;
  server_name: string;
  ca_certificate: string;
  client_certificate: string;
  client_private_key: string;
  expires_ms: number;
}

export class NodeTlsTransport implements ByteTransport {
  private chunks: Uint8Array[] = [];
  private available = 0;
  private waiters = new Set<() => void>();
  private failure?: Error;
  private discardInbound = false;

  private constructor(private readonly socket: TLSSocket) {
    socket.on("data", (chunk: Buffer) => {
      const bytes = new Uint8Array(chunk);
      this.chunks.push(bytes);
      this.available += bytes.byteLength;
      this.wake();
    });
    socket.on("error", (error) => { this.failure = error; this.wake(); });
    socket.on("end", () => { this.failure = new HypermidError("TLS connection ended"); this.wake(); });
    socket.on("close", () => { this.failure ??= new HypermidError("TLS connection closed"); this.wake(); });
  }

  static async open(record: NodeConnectionRecord): Promise<NodeTlsTransport> {
    if (record.protocol !== PROTOCOL) throw new HypermidError(`unsupported connection-record protocol ${record.protocol}`);
    if (!Number.isSafeInteger(record.expires_ms) || record.expires_ms <= Date.now()) throw new HypermidError("connection record has expired");
    const common = {
      ca: readFileSync(record.ca_certificate),
      cert: readFileSync(record.client_certificate),
      key: readFileSync(record.client_private_key),
      servername: record.server_name,
      minVersion: "TLSv1.3" as const,
      maxVersion: "TLSv1.3" as const,
      rejectUnauthorized: true,
    };
    const options = record.endpoint.startsWith("tcp://")
      ? (() => {
          const url = new URL(record.endpoint);
          if (url.protocol !== "tcp:" || !["127.0.0.1", "::1", "localhost"].includes(url.hostname)) {
            throw new HypermidError("connection record TCP endpoint must be loopback");
          }
          return { ...common, host: url.hostname, port: Number(url.port) };
        })()
      : { ...common, path: record.endpoint };
    const socket = tlsConnect(options);
    await new Promise<void>((resolve, reject) => {
      socket.once("secureConnect", resolve);
      socket.once("error", reject);
    });
    if (socket.getProtocol() !== "TLSv1.3" || !socket.authorized) {
      socket.destroy();
      throw new HypermidError("mutual TLS did not authenticate the daemon");
    }
    return new NodeTlsTransport(socket);
  }

  async readExactly(length: number): Promise<Uint8Array> {
    if (this.discardInbound) throw new HypermidError("TLS connection ended after mutation dispatch");
    while (this.available < length) {
      if (this.discardInbound) throw new HypermidError("TLS connection ended after mutation dispatch");
      if (this.failure) throw this.failure;
      await new Promise<void>((resolve) => this.waiters.add(resolve));
    }
    const result = new Uint8Array(length);
    let offset = 0;
    while (offset < length) {
      const chunk = this.chunks[0];
      const copied = Math.min(chunk.byteLength, length - offset);
      result.set(chunk.subarray(0, copied), offset);
      offset += copied;
      this.available -= copied;
      if (copied === chunk.byteLength) this.chunks.shift();
      else this.chunks[0] = chunk.subarray(copied);
    }
    return result;
  }

  async write(bytes: Uint8Array): Promise<void> {
    if (this.failure) throw this.failure;
    await new Promise<void>((resolve, reject) => {
      this.socket.write(bytes, (error) => error ? reject(error) : resolve());
    });
  }

  async close(): Promise<void> {
    if (this.socket.destroyed) return;
    this.socket.end();
    await new Promise<void>((resolve) => {
      this.socket.once("close", resolve);
      setTimeout(() => { this.socket.destroy(); resolve(); }, 1000).unref();
    });
  }

  disconnectAfterNextWrite(): void {
    const original = this.write.bind(this);
    this.write = async (bytes: Uint8Array) => {
      this.write = original;
      this.discardInbound = true;
      this.wake();
      await original(bytes);
      this.socket.destroy();
    };
  }

  private wake(): void {
    for (const waiter of this.waiters) waiter();
    this.waiters.clear();
  }
}

export function loadConnectionRecord(path: string): NodeConnectionRecord {
  const stat = statSync(path);
  if (!stat.isFile() || (stat.mode & 0o077) !== 0) throw new HypermidError("connection record must be an owner-only regular file");
  if (typeof process.geteuid === "function" && stat.uid !== process.geteuid()) throw new HypermidError("connection record has another owner");
  const value = JSON.parse(readFileSync(path, "utf8")) as NodeConnectionRecord;
  const required = ["endpoint", "protocol", "credential_id", "secret_b64", "server_name", "ca_certificate", "client_certificate", "client_private_key", "expires_ms"];
  if (Object.keys(value).sort().join("\0") !== required.sort().join("\0")) throw new HypermidError("connection record fields are invalid");
  if (value.protocol !== PROTOCOL) throw new HypermidError(`unsupported connection-record protocol ${value.protocol}`);
  return value;
}

export async function connectNode(recordPath: string, scope: Scope): Promise<{ client: HypermidClient; transport: NodeTlsTransport }> {
  const record = loadConnectionRecord(recordPath);
  const secret = Buffer.from(record.secret_b64, "base64");
  if (secret.byteLength !== 32 || secret.toString("base64") !== record.secret_b64) throw new HypermidError("connection credential is invalid");
  const transport = await NodeTlsTransport.open(record);
  const client = new HypermidClient(transport, new Uint8Array(secret), scope);
  await client.connect();
  return { client, transport };
}
