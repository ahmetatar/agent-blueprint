import { useEffect, useRef } from "react";

/**
 * Subscribe to the server's /ws push channel and invoke `onChange` whenever
 * the blueprint file changes on disk. Reconnects with a small backoff so a
 * server restart (or dev-server proxy hiccup) recovers without a page reload.
 */
export function useLiveReload(onChange: () => void): void {
  const handler = useRef(onChange);
  handler.current = onChange;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let disposed = false;

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as { type?: string };
        if (message.type === "file_changed") handler.current();
      };
      socket.onclose = () => {
        if (!disposed) timer = window.setTimeout(connect, 1500);
      };
    };
    connect();

    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);
}
