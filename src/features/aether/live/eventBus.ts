type Handler<T = unknown> = (payload: T) => void;

export class EventBus {
  private listeners = new Map<string, Set<Handler>>();

  on<T>(eventName: string, handler: Handler<T>): () => void {
    if (!this.listeners.has(eventName)) this.listeners.set(eventName, new Set());
    this.listeners.get(eventName)!.add(handler as Handler);
    return () => this.off(eventName, handler);
  }

  off<T>(eventName: string, handler: Handler<T>): void {
    this.listeners.get(eventName)?.delete(handler as Handler);
  }

  emit<T>(eventName: string, payload?: T): void {
    const handlers = this.listeners.get(eventName);
    if (!handlers) return;
    for (const handler of [...handlers]) handler(payload);
  }
}
