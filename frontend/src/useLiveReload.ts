import { useEffect, useRef } from "react";
import type { ChatWsMessage, TaskMessage } from "./api";

/**
 * Subscribe to the server's /ws push channel: invoke `onChange` whenever the
 * blueprint file changes on disk, `onTaskMessage` for background-task events
 * (task_started / task_progress / task_done), and `onChatMessage` for chat
 * session events (chat_status / chat_message). Reconnects with a small backoff
 * so a server restart (or dev-server proxy hiccup) recovers without a page
 * reload.
 */
export function useLiveReload(
  onChange: () => void,
  onTaskMessage?: (message: TaskMessage) => void,
  onChatMessage?: (message: ChatWsMessage) => void,
): void {
  const changeHandler = useRef(onChange);
  changeHandler.current = onChange;
  const taskHandler = useRef(onTaskMessage);
  taskHandler.current = onTaskMessage;
  const chatHandler = useRef(onChatMessage);
  chatHandler.current = onChatMessage;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let disposed = false;

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as { type?: string };
        if (message.type === "file_changed") changeHandler.current();
        else if (message.type?.startsWith("task_"))
          taskHandler.current?.(message as TaskMessage);
        else if (message.type?.startsWith("chat_"))
          chatHandler.current?.(message as ChatWsMessage);
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
