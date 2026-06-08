import { useEffect, useRef, useState } from "react";
import {
  ChatNotReadyError,
  deleteChatThread,
  fetchChatThreads,
  sendChat,
  startChat,
  type ChatState,
  type ChatThread,
} from "../api";

interface Props {
  chat: ChatState;
  valid: boolean;
}

/**
 * Persistent chat session (E5.1) with durable threads (E5.5). The generated
 * project is kept alive so the conversation continues; its state is checkpointed
 * to a SQLite file next to the blueprint, so a thread can be *resumed* even
 * after the editor restarts. The agent's replies arrive over the WS; this pane
 * owns start / New session / send and the thread browser (resume / reset).
 */
export function ChatPane({ chat, valid }: Props) {
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [threadsOpen, setThreadsOpen] = useState(false);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const historyRef = useRef<HTMLDivElement | null>(null);

  // Keep the latest turn in view as messages stream in.
  useEffect(() => {
    const el = historyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.history, chat.status]);

  const refreshThreads = () => {
    fetchChatThreads()
      .then(setThreads)
      .catch(() => setThreads([]));
  };

  // Refresh the thread list whenever it's opened or a session goes ready
  // (a finished turn updates a thread's transcript/preview on disk).
  useEffect(() => {
    if (threadsOpen) refreshThreads();
  }, [threadsOpen, chat.status]);

  if (!valid) {
    return (
      <p className="chat-empty muted">
        The blueprint does not validate — fix the errors in Issues first, then start a chat.
      </p>
    );
  }

  // Status/history are driven entirely by the WS (chat_status / chat_message);
  // using the POST response would race a "ready" that already arrived and freeze
  // the UI on "starting". So start helpers only surface a failed request.
  const start = (threadId?: string) => {
    setSendError(null);
    startChat(threadId).catch((e) => setSendError(String(e)));
  };

  const resume = (threadId: string) => {
    setThreadsOpen(false);
    start(threadId);
  };

  const reset = (threadId: string) => {
    if (!window.confirm(`Forget thread ${threadId}? This deletes its saved conversation.`)) {
      return;
    }
    deleteChatThread(threadId)
      .then(setThreads)
      .catch((e) => setSendError(String(e)));
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
  const idle = chat.status === "idle" || chat.status === "stopped";

  return (
    <div className="chat-pane">
      <div className="chat-toolbar">
        <button
          type="button"
          className="action-button"
          onClick={() => start()}
          disabled={starting}
          title={idle ? "Start a chat session" : "Restart fresh — picks up any blueprint edits"}
        >
          {idle ? "Start chat" : "New session"}
        </button>
        <button
          type="button"
          className="action-button"
          onClick={() => setThreadsOpen((open) => !open)}
          title="Browse and resume past conversations"
        >
          Threads ▾
        </button>
        {chat.thread_id && (
          <span className="chat-thread" title="Active conversation thread">
            thread <code>{chat.thread_id}</code>
          </span>
        )}
        <span className={`chat-status chat-status-${chat.status}`}>
          {starting ? "starting…" : chat.status}
        </span>
      </div>

      {threadsOpen && (
        <div className="chat-threads">
          {threads.length === 0 ? (
            <p className="chat-empty muted">No saved conversations yet.</p>
          ) : (
            <ul className="chat-thread-list">
              {threads.map((thread) => (
                <li key={thread.thread_id} className="chat-thread-row">
                  <button
                    type="button"
                    className="chat-thread-resume"
                    onClick={() => resume(thread.thread_id)}
                    disabled={starting}
                    title="Resume this conversation"
                  >
                    <code>{thread.thread_id}</code>
                    <span className="chat-thread-meta">
                      {thread.count} msg{thread.preview ? ` · ${thread.preview}` : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="chat-thread-reset"
                    onClick={() => reset(thread.thread_id)}
                    title="Forget this conversation"
                  >
                    reset
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="chat-note muted">
        A persistent, <strong>durable</strong> session: messages share one{" "}
        <code>thread_id</code> and a SQLite checkpointer next to the blueprint, so a
        conversation can be resumed from <em>Threads</em> even after the editor restarts.
        <em>New session</em> starts a fresh thread.
      </p>

      {chat.status === "error" && chat.error && <pre className="chat-error">{chat.error}</pre>}

      <div className="chat-history" ref={historyRef}>
        {chat.history.length === 0 && (ready || starting) && (
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
