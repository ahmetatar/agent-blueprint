import { useEffect, useRef, useState } from "react";
import {
  ChatNotReadyError,
  sendChat,
  startChat,
  type ChatState,
} from "../api";

interface Props {
  chat: ChatState;
  valid: boolean;
}

/**
 * Persistent chat session (E5.1). Unlike a one-shot Run, the generated project
 * is kept alive, so the conversation actually continues (same thread_id, same
 * in-memory checkpointer). The agent's replies arrive over the WS; this pane
 * owns the start / New session / send controls and renders the history the
 * server keeps (so a reloaded tab resyncs).
 */
export function ChatPane({ chat, valid }: Props) {
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const historyRef = useRef<HTMLDivElement | null>(null);

  // Keep the latest turn in view as messages stream in.
  useEffect(() => {
    const el = historyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.history, chat.status]);

  if (!valid) {
    return (
      <p className="chat-empty muted">
        The blueprint does not validate — fix the errors in Issues first, then start a chat.
      </p>
    );
  }

  const start = () => {
    setSendError(null);
    // The status (starting → ready) and history are driven entirely by the WS
    // chat_status / chat_message events — using the POST response here would
    // race a "ready" that already arrived over the WS and freeze the UI on
    // "starting". So only surface a failed request.
    startChat().catch((e) => setSendError(String(e)));
  };

  const send = () => {
    const message = draft.trim();
    if (!message) return;
    setSendError(null);
    setDraft("");
    sendChat(message).catch((e) =>
      setSendError(e instanceof ChatNotReadyError ? e.message : String(e)),
    );
  };

  const ready = chat.status === "ready";
  const starting = chat.status === "starting";

  return (
    <div className="chat-pane">
      <div className="chat-toolbar">
        {chat.status === "idle" || chat.status === "stopped" ? (
          <button type="button" className="action-button" onClick={start}>
            Start chat
          </button>
        ) : (
          <button
            type="button"
            className="action-button"
            onClick={start}
            disabled={starting}
            title="Restart with a fresh session — picks up any blueprint edits"
          >
            New session
          </button>
        )}
        {chat.thread_id && (
          <span className="chat-thread" title="Active conversation thread">
            thread <code>{chat.thread_id}</code>
          </span>
        )}
        <span className={`chat-status chat-status-${chat.status}`}>
          {starting ? "starting…" : chat.status}
        </span>
      </div>

      <p className="chat-note muted">
        A persistent session: messages share one <code>thread_id</code> and an in-memory
        checkpointer, so the conversation continues until you start a new session (then
        history resets). State is not saved to disk.
      </p>

      {chat.status === "error" && chat.error && <pre className="chat-error">{chat.error}</pre>}

      <div className="chat-history" ref={historyRef}>
        {chat.history.length === 0 && (chat.status === "ready" || chat.status === "starting") && (
          <p className="chat-empty muted">Say something to start the conversation.</p>
        )}
        {chat.history.map((message, index) => (
          <div key={index} className={`chat-bubble chat-bubble-${message.role}`}>
            <span className="chat-role">{message.role}</span>
            <span className="chat-content">{message.content}</span>
          </div>
        ))}
      </div>

      {sendError && <p className="action-start-error">{sendError}</p>}

      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          className="chat-input"
          type="text"
          value={draft}
          placeholder={ready ? "Message…" : "Start a chat to send messages"}
          disabled={!ready}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="action-button" disabled={!ready || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
