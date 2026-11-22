import net from 'node:net';
import readline from 'node:readline';

export class Backend {
  constructor({ onEvent, onClose, onRequest, timeout = 60000 }) {
    this.onEvent = onEvent;
    this.onClose = onClose;
    this.onRequest = onRequest;
    this.timeout = timeout;
    this.pending = new Map();
    this.buffer = [];
    this.output = null;
    this.next = 1;
    this.closed = false;
  }

  attach(input, output) {
    this.output = output;
    const lines = readline.createInterface({ input, crlfDelay: Infinity });
    lines.on('line', (line) => this.receive(line));
    lines.on('close', () => this.close());
    for (const line of this.buffer.splice(0)) output.write(line);
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    for (const waiting of this.pending.values()) {
      clearTimeout(waiting.timer);
      waiting.reject(new Error('Jarvis stopped'));
    }
    this.pending.clear();
    this.onClose?.();
  }

  receive(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    if (!message || typeof message !== 'object') return;
    if (message.t === 'event' && typeof message.name === 'string') {
      this.onEvent(message.name, message.data ?? null);
    } else if (message.t === 'request') {
      Promise.resolve().then(() => {
        if (!Number.isFinite(message.deadline) || Date.now() >= message.deadline) throw new Error('The interface request expired');
        if (!this.onRequest || typeof message.method !== 'string') throw new Error('Unknown interface request');
        return this.onRequest(message.method, message.params ?? {});
      }).then((result) => this.write({ t: 'response', id: message.id, ok: true, result }),
              (error) => this.write({ t: 'response', id: message.id, ok: false, error: error.message }));
    } else if (message.t === 'reply') {
      const waiting = this.pending.get(message.id);
      if (!waiting) return;
      this.pending.delete(message.id);
      clearTimeout(waiting.timer);
      if (message.ok) waiting.resolve(message.result ?? null);
      else waiting.reject(new Error(message.error || 'The request failed'));
    }
  }

  call(method, params = {}, timeout = this.timeout) {
    if (this.closed) return Promise.reject(new Error('Jarvis stopped'));
    const id = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('Jarvis did not answer in time'));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.write({ t: 'call', id, method, params });
    });
  }

  ready() {
    this.write({ t: 'ready' });
  }

  write(message) {
    if (this.closed) return;
    const line = JSON.stringify(message) + '\n';
    if (this.output) this.output.write(line);
    else this.buffer.push(line);
  }
}

export function connectBackend({ port, key, onEvent, onClose, onRequest, attempts = 10 }) {
  const backend = new Backend({ onEvent, onClose, onRequest });
  let attempt = 0;
  const open = () => {
    attempt += 1;
    let connected = false;
    const socket = net.createConnection({ host: '127.0.0.1', port });
    socket.setTimeout(2500);
    const retry = (reason) => {
      if (connected) return;
      socket.destroy();
      if (attempt >= attempts) {
        console.error(`Jarvis connection failed after ${attempt} attempts: ${reason}`);
        backend.close();
      } else {
        setTimeout(open, 300);
      }
    };
    socket.once('connect', () => {
      connected = true;
      socket.setTimeout(0);
      socket.setNoDelay(true);
      socket.setEncoding('utf8');
      socket.write(key + '\n');
      backend.attach(socket, socket);
    });
    socket.once('timeout', () => retry('timed out'));
    socket.on('error', (error) => {
      if (!connected) retry(error.message);
      else {
        console.error('Jarvis connection lost:', error.message);
        socket.destroy();
      }
    });
  };
  open();
  return backend;
}
