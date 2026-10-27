import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ChevronDown,
  ChevronRight,
  Wrench,
  MessagesSquare,
  Building2,
  Trash2,
  Send,
  StopCircle,
  AlertTriangle,
  X,
  Loader2,
  Sparkles,
} from "lucide-react";
import { zohoApi } from "../../../../services/api";
import { formatZohoError } from "./components/zohoError";
import { PageHeader } from "../../../ui/page-header";
import { Button } from "../../../ui/button";
import { Card } from "../../../ui/card";
import { Select, Textarea } from "../../../ui/input";
import { cn } from "../../../../lib/utils";

const toPromptHistory = (items) =>
  (items || [])
    .filter((m) => m.role === "user" || m.role === "assistant")
    .filter((m) => m.content)
    .map((m) => ({ role: m.role, content: m.content }));

const toUiMessages = (items) =>
  (items || [])
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      role: m.role,
      content: m.content || "",
      ...(m.role === "assistant" ? { tools: m.metadata?.tools || [] } : {}),
    }));

function ToolTrace({ tool }) {
  const [open, setOpen] = useState(false);
  const argsStr = tool.args ? JSON.stringify(tool.args) : "";
  const summary = (() => {
    if (tool.name === "list_zoho_entities") return "available resources";
    if (tool.name === "zoho_list") {
      const filters = tool.args?.filters || {};
      const tail = Object.entries(filters).map(([k, v]) => `${k}=${v}`).join(", ");
      return `${tool.args?.resource || "resource"}${tail ? ` (${tail})` : ""}`;
    }
    if (tool.name === "zoho_report") {
      const p = tool.args?.params || {};
      const tail = Object.entries(p).map(([k, v]) => `${k}=${v}`).join(", ");
      return `${tool.args?.name || "report"}${tail ? ` (${tail})` : ""}`;
    }
    return tool.name;
  })();
  return (
    <div className="border-l-2 border-gilt-hairline pl-2.5 my-2 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-gilt-soft hover:text-gilt-ink transition-colors duration-150 group"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Wrench className="h-3 w-3 text-gilt-brass" />
        <span className="font-mono text-gilt-soft group-hover:text-gilt-ink transition-colors duration-150">{tool.name}</span>
        <span className="text-gilt-faint truncate max-w-md">— {summary}</span>
        {tool.result === null && (
          <span className="inline-flex items-center gap-1 text-gilt-brass ml-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            running…
          </span>
        )}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {argsStr && (
            <pre className="bg-gilt-sunken border border-gilt-hairline rounded-lg p-2 overflow-auto whitespace-pre-wrap break-all text-gilt-ink font-mono text-2xs">
              {argsStr}
            </pre>
          )}
          {tool.result && (
            <pre className="bg-gilt-sunken border border-gilt-hairline rounded-lg p-2 overflow-auto whitespace-pre-wrap break-all max-h-72 text-gilt-ink font-mono text-2xs">
              {typeof tool.result === "string"
                ? tool.result
                : JSON.stringify(tool.result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Zoho Chat — natural-language Q&A grounded on Zoho Books context.
 *
 * Backend streams NDJSON events:
 *   {type: "status", content}      (transient)
 *   {type: "answer_delta", content} (token chunks)
 *   {type: "error", content}
 *   {type: "done"}
 */
export default function ZohoChat() {
  const [companies, setCompanies] = useState([]);
  const [company, setCompany] = useState("");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [statusLine, setStatusLine] = useState("");
  const [error, setError] = useState("");
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  const historyLoadRef = useRef(0);

  useEffect(() => {
    (async () => {
      try {
        const res = await zohoApi.getCompanies();
        const list = res?.organizations || [];
        setCompanies(list);
        if (list[0]) setCompany(list[0].organization_id || list[0].name);
      } catch (err) {
        setError(formatZohoError(err, "Failed to load companies"));
      }
    })();
  }, []);

  useEffect(() => {
    if (!company) {
      setMessages([]);
      setThreadId("");
      return;
    }

    const loadId = historyLoadRef.current + 1;
    historyLoadRef.current = loadId;
    setLoadingHistory(true);
    setError("");
    setStatusLine("");
    setMessages([]);
    setThreadId("");

    (async () => {
      try {
        const res = await zohoApi.chatHistory({ organizationId: company });
        if (historyLoadRef.current !== loadId) return;
        setThreadId(res?.thread_id || "");
        setMessages(toUiMessages(res?.messages));
      } catch (err) {
        if (historyLoadRef.current !== loadId) return;
        setError(formatZohoError(err, "Failed to load chat history"));
        setMessages([]);
        setThreadId("");
      } finally {
        if (historyLoadRef.current === loadId) {
          setLoadingHistory(false);
        }
      }
    })();
  }, [company]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, statusLine, loadingHistory]);

  const onSend = async () => {
    const text = input.trim();
    if (!text || pending || loadingHistory || !company) return;
    setError("");
    setStatusLine("");
    const priorHistory = messages;
    const activeThreadId = threadId;
    setMessages([
      ...priorHistory,
      { role: "user", content: text },
      { role: "assistant", content: "", tools: [] },
    ]);
    setInput("");
    setPending(true);

    const ctl = new AbortController();
    abortRef.current = ctl;

    const appendToAssistant = (mutator) => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = mutator({ ...last });
        }
        return next;
      });
    };

    try {
      await zohoApi.chatStream({
        message: text,
        organizationId: company,
        history: toPromptHistory(priorHistory),
        threadId: activeThreadId || undefined,
        signal: ctl.signal,
        onEvent: (evt) => {
          if (evt.type === "meta") {
            setThreadId(evt.thread_id || "");
          } else if (evt.type === "status") {
            setStatusLine(evt.content || "");
          } else if (evt.type === "answer_delta") {
            appendToAssistant((m) => ({
              ...m,
              content: (m.content || "") + (evt.content || ""),
            }));
          } else if (evt.type === "tool_call") {
            appendToAssistant((m) => ({
              ...m,
              tools: [
                ...(m.tools || []),
                { name: evt.name, args: evt.arguments, result: null },
              ],
            }));
          } else if (evt.type === "tool_result") {
            appendToAssistant((m) => {
              const tools = [...(m.tools || [])];
              for (let i = tools.length - 1; i >= 0; i--) {
                if (tools[i].name === evt.name && tools[i].result === null) {
                  tools[i] = { ...tools[i], result: evt.content };
                  break;
                }
              }
              return { ...m, tools };
            });
          } else if (evt.type === "error") {
            setError(evt.content || "Chat error");
          } else if (evt.type === "done") {
            setStatusLine("");
          }
        },
      });
    } catch (err) {
      if (err.name !== "AbortError") {
        setError(err.message || "Chat failed");
      }
    } finally {
      setPending(false);
      abortRef.current = null;
    }
  };

  const onStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      setPending(false);
      setStatusLine("");
    }
  };

  const onReset = async () => {
    if (pending || loadingHistory) return;
    setStatusLine("");
    setError("");
    try {
      await zohoApi.clearChat({ organizationId: company, threadId: threadId || undefined });
      setMessages([]);
      setInput("");
      setThreadId("");
    } catch (err) {
      setError(formatZohoError(err, "Failed to reset chat"));
    }
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const samplePrompts = [
    "Top 5 vendors by total spend in 2026",
    "What's the P&L for last quarter?",
    "AR aging — who owes us most?",
    "Any open invoices over 30 days?",
    "Show all bills from this vendor this year",
    "How much did we spend on Office Supplies in March?",
  ];

  return (
    <div className="h-screen flex flex-col">
      <PageHeader
        className="bg-transparent border-gilt-hairline"
        icon={MessagesSquare}
        title={<span className="gl-display text-gilt-ink">Zoho Books Chat</span>}
        description={
          <span className="text-gilt-soft">
            Ask natural-language questions over your Zoho Books data.
          </span>
        }
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 min-w-[220px]">
              <Building2 className="h-3.5 w-3.5 text-gilt-faint shrink-0" />
              <Select
                size="sm"
                className="bg-gilt-card border-gilt-hairline text-gilt-ink focus:border-gilt-brass focus:shadow-none"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
              >
                {companies.map((c) => {
                  const v = c.organization_id || c.name;
                  return (
                    <option key={v} value={v}>
                      {c.name}
                    </option>
                  );
                })}
              </Select>
            </div>
            <Button
              variant="tertiary"
              size="sm"
              className="text-gilt-soft hover:text-gilt-ink hover:bg-gilt-sunken active:bg-gilt-sunken"
              leftIcon={<Trash2 />}
              onClick={onReset}
              disabled={pending || loadingHistory || !company}
            >
              Reset
            </Button>
          </div>
        }
      />

      <div className="flex-1 flex flex-col px-6 lg:px-8 py-4 min-h-0">
        {error && (
          <Card className="mb-3 shadow-none bg-gilt-card border-gilt-hairline">
            <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
              <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
              <span className="flex-1">{error}</span>
              <button
                onClick={() => setError("")}
                className="text-gilt-faint hover:text-gilt-ink transition-colors duration-150"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </Card>
        )}

        <div
          ref={scrollRef}
          className="flex-1 overflow-auto bg-gilt-card rounded-xl border border-gilt-hairline p-5 space-y-4"
        >
          {messages.length === 0 && (
            <div className="text-gilt-soft text-sm flex flex-col items-center text-center py-10 px-4">
              <div className="h-12 w-12 rounded-xl bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center mb-4">
                <Sparkles className="h-5 w-5" />
              </div>
              <p className="max-w-lg leading-relaxed text-gilt-soft">
                Ask anything about your Zoho Books data. The agent uses live Zoho Books
                lists and reports — expand the tool traces under each answer to see what ran.
              </p>
              <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-2 w-full max-w-2xl">
                {samplePrompts.map((prompt, idx) => (
                  <button
                    key={prompt}
                    onClick={() => setInput(prompt)}
                    className="text-left px-3 py-2.5 rounded-lg border border-gilt-hairline bg-gilt-card hover:border-gilt-brass text-gilt-soft hover:text-gilt-ink transition-colors duration-150"
                  >
                    <span
                      aria-hidden="true"
                      className="block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass mb-1"
                    >
                      № {String(idx + 1).padStart(2, "0")}
                    </span>
                    <span className="block text-xs leading-relaxed">{prompt}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}
            >
              <div
                className={cn(
                  "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap transition-colors duration-150",
                  m.role === "user"
                    ? "bg-gilt-sunken text-gilt-ink rounded-br-md"
                    : "bg-transparent text-gilt-ink rounded-bl-md"
                )}
              >
                {m.role === "assistant" ? (
                  <>
                    {(m.tools || []).map((t, ti) => (
                      <ToolTrace key={ti} tool={t} />
                    ))}
                    <div className="markdown-content prose prose-sm max-w-none prose-p:my-1.5 prose-li:my-0">
                      <ReactMarkdown>
                        {m.content || (pending && i === messages.length - 1 ? "…" : "")}
                      </ReactMarkdown>
                    </div>
                  </>
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}
          {statusLine && (
            <div className="inline-flex items-center gap-1.5 text-xs text-gilt-faint italic">
              <Loader2 className="h-3 w-3 animate-spin text-gilt-brass" />
              {statusLine}
            </div>
          )}
          {loadingHistory && !statusLine && (
            <div className="inline-flex items-center gap-1.5 text-xs text-gilt-faint italic">
              <Loader2 className="h-3 w-3 animate-spin text-gilt-brass" />
              Loading chat history…
            </div>
          )}
        </div>

        <div className="mt-3 bg-gilt-card rounded-xl border border-gilt-hairline focus-within:border-gilt-brass transition-colors duration-200 p-3 flex gap-2 items-end">
          <Textarea
            rows={2}
            className="flex-1 resize-none min-h-0 bg-transparent border-transparent text-gilt-ink placeholder:text-gilt-faint focus:border-transparent focus:shadow-none disabled:bg-transparent disabled:text-gilt-faint"
            placeholder="Ask about your Zoho Books data… (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            disabled={pending || loadingHistory}
          />
          {pending ? (
            <Button
              variant="danger"
              className="bg-gilt-debit text-gilt-paper hover:bg-gilt-debit/90 active:bg-gilt-debit rounded-md shadow-none focus-visible:ring-gilt-debit focus-visible:ring-offset-gilt-card"
              leftIcon={<StopCircle />}
              onClick={onStop}
            >
              Stop
            </Button>
          ) : (
            <Button
              variant="primary"
              className="bg-gilt-brass text-gilt-paper hover:bg-gilt-brass/90 active:bg-gilt-ink rounded-md shadow-none focus-visible:ring-gilt-brass focus-visible:ring-offset-gilt-card"
              leftIcon={<Send />}
              onClick={onSend}
              disabled={!input.trim() || !company || loadingHistory}
            >
              Send
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
