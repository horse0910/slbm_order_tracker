/**
 * Controllable fake WebSocket for tests.
 *
 * Records every instance and exposes manual triggers (`_open`, `_message`,
 * `_serverClose`) so a test can drive the connection lifecycle deterministically
 * without any real socket. Assign it to `globalThis.WebSocket` in a `beforeEach`
 * and restore the previous value in `afterEach`.
 */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static reset() {
    FakeWebSocket.instances = [];
  }

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];

  onopen: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({});
  }

  // --- test triggers -------------------------------------------------------
  _open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  _message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  _serverClose() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({});
  }
}
