import "./llm_trace_formatters.js?v=schema-description-safe";

const EVENT_ICONS = {
  coordinator: "🧠",
  llm_response: "💬",
  user_input:"🧑‍💻",
  skill_load: "📖",
  tool_call: "🔧",
  tool_result: "📦",
  subagent: "🤖",
  output: "✅",
  error: "❌",
  started: "🚀",
  thinking: "💭",
  running: "⚡",
  paused: "⏸️",
  completed: "🏁",
  interrupted: "🔔",
  cancelled: "🚫",
  retrying: "🔁",
  timeout: "⏰",
  file_read: "📄",
  file_write: "📝",
  file_search: "🔍",
  shell: "💻",
  web_fetch: "🌐",
  database: "🗄️",
  vector_search: "🧲",
  memory_read: "🧩",
  memory_write: "🧠",
  mcp_tool: "🔌",
  ask_user: "💬",
  summarize: "🗜️",
  middleware: "🔩",
  middleware_pass: "➖",
  hitl: "🙋",
  checkpoint: "💾",
  context_manage: "🗜️",
  ping: "💓",
  stream_start: "📡",
  stream_end: "🔚",
  data_input: "📥",
  data_output: "📤",
  token_usage: "🪙",
  cache_hit: "⚡",
  cache_miss: "🐢",
  warning: "⚠️",
  info: "ℹ️",
  debug: "🐛",
  intent_parse: "🎯",
  indicator_search: "📊",
  theme_locate: "🗺️",
  template_recommend: "💎",
  filter_confirm: "✔️",
  dimension_confirm: "📐",
  user_confirm: "🙋",
  report_generate: "📑",
};

const EVENT_TYPE_CONFIG = {
  user_message: { label: "User", icon: EVENT_ICONS.user_input, tone: "slate", status: "user_message" },
  assistant_message: { label: "Assistant", icon: EVENT_ICONS.llm_response, tone: "indigo", status: "agent_response" },
  skill_loaded: { label: "Skill", icon: EVENT_ICONS.skill_load, tone: "emerald", status: "ready" },
  middleware: { label: "Middleware", icon: EVENT_ICONS.middleware, tone: "violet", status: "runtime" },
  tool_event_group: { label: "Tool", icon: EVENT_ICONS.tool_call, tone: "cyan", status: "runtime" },
  tool_use: { label: "Tool Use", icon: EVENT_ICONS.tool_call, tone: "cyan", status: "running" },
  tool_result: { label: "Tool Result", icon: EVENT_ICONS.tool_result, tone: "blue", status: "success" },
  interrupt: { label: "Interrupt", icon: EVENT_ICONS.interrupted, tone: "amber", status: "waiting" },
  error: { label: "Error", icon: EVENT_ICONS.error, tone: "rose", status: "error" },
  raw: { label: "Raw", icon: EVENT_ICONS.warning, tone: "slate", status: "debug" },
  done: { label: "Done", icon: EVENT_ICONS.stream_end, tone: "emerald", status: "closed" },
};

function createThreadId() {
  if (globalThis.crypto?.randomUUID) {
    return `thread-${globalThis.crypto.randomUUID()}`;
  }
  return `thread-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const state = {
  agents: [],
  // agentId 和事件筛选是 UI 偏好，继续跨窗口保存在 localStorage。
  agentId: localStorage.getItem("interaction.agent_id") || "theme-template-recommendation-deepagents",
  // threadId/events 是当前窗口会话态：同窗口刷新后保留，另开窗口不共享，避免串用上游 checkpoint。
  threadId: sessionStorage.getItem("interaction.thread_id") || createThreadId(),
  events: JSON.parse(sessionStorage.getItem("interaction.events") || "[]"),
  visibleEventTypes: normalizeVisibleEventTypes(JSON.parse(localStorage.getItem("interaction.visible_event_types") || "null") || defaultVisibleEventTypes()),
  selectedSeq: null,
  detailMode: "event",
  traces: [],
  waitingInterrupt: false,
  busy: false,
};

const els = {
  agentList: document.querySelector("#agent-list"),
  threadId: document.querySelector("#thread-id"),
  newSession: document.querySelector("#new-session"),
  clearEvents: document.querySelector("#clear-events"),
  eventTypeFilters: document.querySelector("#event-type-filters"),
  timeline: document.querySelector("#timeline"),
  detail: document.querySelector("#event-detail"),
  eventMeta: document.querySelector("#event-meta"),
  copyJson: document.querySelector("#copy-json"),
  showEventDetail: document.querySelector("#show-event-detail"),
  showLlmTrace: document.querySelector("#show-llm-trace"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#user-input"),
  status: document.querySelector("#connection-status"),
  eventCount: document.querySelector("#event-count"),
  sessionPill: document.querySelector("#session-pill"),
};

init();

async function init() {
  els.threadId.value = state.threadId;
  bindEvents();
  renderEventTypeFilters();
  renderEvents();
  updateRuntimeMeta();
  await loadAgents();
}

function bindEvents() {
  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = els.input.value.trim();
    if (!text || state.busy) return;
    if (state.waitingInterrupt) {
      addLocalEvent("error", { message: "请先完成当前确认表单" });
      return;
    }
    els.input.value = "";
    await sendMessage(text);
  });

  els.threadId.addEventListener("change", () => {
    state.threadId = els.threadId.value.trim() || createThreadId();
    sessionStorage.setItem("interaction.thread_id", state.threadId);
    updateRuntimeMeta();
  });

  els.newSession.addEventListener("click", () => {
    // 新会话只切换当前窗口的 thread_id；旧 thread 的 checkpoint 留在上游进程中，
    // 但后续请求不再携带旧 ID，因此不会继承旧上下文。
    state.threadId = createThreadId();
    state.events = [];
    state.selectedSeq = null;
    state.waitingInterrupt = false;
    state.traces = [];
    state.detailMode = "event";
    els.threadId.value = state.threadId;
    persist();
    renderEvents();
    setDetail(null);
    updateRuntimeMeta();
  });

  els.clearEvents.addEventListener("click", () => {
    state.events = [];
    state.selectedSeq = null;
    state.waitingInterrupt = false;
    state.detailMode = "event";
    persist();
    renderEvents();
    setDetail(null);
    updateRuntimeMeta();
  });

  els.copyJson.addEventListener("click", async () => {
    await navigator.clipboard.writeText(els.detail.innerText || "");
    els.copyJson.textContent = "已复制";
    setTimeout(() => {
      els.copyJson.textContent = "复制JSON";
    }, 1200);
  });

  els.showEventDetail.addEventListener("click", () => {
    state.detailMode = "event";
    renderDetailPanel();
  });

  els.showLlmTrace.addEventListener("click", async () => {
    state.detailMode = "trace";
    await refreshTraces();
    renderDetailPanel();
  });
}

function defaultVisibleEventTypes() {
  return Object.keys(EVENT_TYPE_CONFIG).filter((type) => type !== "middleware");
}

function normalizeVisibleEventTypes(types) {
  const known = Object.keys(EVENT_TYPE_CONFIG);
  const normalized = types.filter((type) => known.includes(type));
  if (!normalized.includes("user_message")) normalized.push("user_message");
  if (!normalized.includes("done")) normalized.push("done");
  return normalized.length ? normalized : defaultVisibleEventTypes();
}

function renderEventTypeFilters() {
  els.eventTypeFilters.innerHTML = "";
  for (const [type, config] of Object.entries(EVENT_TYPE_CONFIG)) {
    if (type === "tool_use" || type === "tool_result") continue;
    const label = document.createElement("label");
    label.className = "event-type-filter";
    label.innerHTML = `
      <input type="checkbox" value="${escapeHtml(type)}" ${state.visibleEventTypes.includes(type) ? "checked" : ""}>
      <span class="event-type-filter-icon tone-${config.tone}">${escapeHtml(config.icon)}</span>
      <span>${escapeHtml(config.label)}</span>
    `;
    label.querySelector("input").addEventListener("change", (event) => {
      if (event.target.checked) {
        state.visibleEventTypes = [...new Set([...state.visibleEventTypes, type])];
      } else {
        state.visibleEventTypes = state.visibleEventTypes.filter((item) => item !== type);
      }
      localStorage.setItem("interaction.visible_event_types", JSON.stringify(state.visibleEventTypes));
      renderEvents();
    });
    els.eventTypeFilters.appendChild(label);
  }
}

async function loadAgents() {
  const response = await fetch("/api/agents");
  state.agents = await response.json();
  if (!state.agents.some((agent) => agent.id === state.agentId)) {
    state.agentId = state.agents[0]?.id || state.agentId;
  }
  localStorage.setItem("interaction.agent_id", state.agentId);
  renderAgents();
}

function renderAgents() {
  els.agentList.innerHTML = "";
  for (const agent of state.agents) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `agent-card ${agent.id === state.agentId ? "active" : ""}`;
    button.innerHTML = `
      <span class="agent-card-top">
        <span class="agent-type">${escapeHtml(agent.agent_type)}</span>
        <span class="status-badge online"><span class="status-dot"></span>Online</span>
      </span>
      <strong>${escapeHtml(agent.name)}</strong>
      <small class="mono">${escapeHtml(agent.upstream_url)}</small>
    `;
    button.addEventListener("click", () => {
      state.agentId = agent.id;
      localStorage.setItem("interaction.agent_id", state.agentId);
      renderAgents();
    });
    els.agentList.appendChild(button);
  }
}

async function sendMessage(userInput) {
  state.busy = true;
  setStatus("连接上游中…");

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: state.agentId, thread_id: state.threadId, user_input: userInput }),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    await readSse(response.body);
  } catch (error) {
    addLocalEvent("error", { message: error.message || String(error) });
  } finally {
    state.busy = false;
    setStatus("未连接");
  }
}

async function readSse(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  setStatus("接收事件流…");

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data:"));
      if (!line) continue;
      const data = line.slice(5).trim();
      if (!data) continue;
      appendEvent(JSON.parse(data));
    }
  }
}

function addLocalEvent(type, payload) {
  appendEvent({
    type,
    thread_id: state.threadId,
    agent_id: state.agentId,
    seq: nextSeq(),
    timestamp: new Date().toISOString(),
    payload,
    raw: null,
  });
}

function appendEvent(event) {
  state.events.push(event);
  if (event.type === "interrupt") state.waitingInterrupt = true;
  if (event.type === "done") state.waitingInterrupt = false;
  persist();
  renderEvents();
  updateRuntimeMeta();
  els.timeline.scrollTop = els.timeline.scrollHeight;
}

function nextSeq() {
  return Math.max(0, ...state.events.map((event) => Number(event.seq) || 0)) + 1;
}

function persist() {
  localStorage.setItem("interaction.agent_id", state.agentId);
  // 当前窗口会话态写入 sessionStorage，避免多个 tab/window 共享同一个上游 thread_id。
  sessionStorage.setItem("interaction.thread_id", state.threadId);
  sessionStorage.setItem("interaction.events", JSON.stringify(state.events));
}

function renderEvents() {
  els.timeline.innerHTML = "";
  if (!state.events.length) {
    els.timeline.appendChild(renderEmptyState());
    updateRuntimeMeta();
    return;
  }

  const displayEvents = groupToolEventsForDisplay(state.events).filter((event) => state.visibleEventTypes.includes(event.type));
  if (!displayEvents.length) {
    els.timeline.appendChild(renderEmptyState("当前筛选条件下没有事件"));
    updateRuntimeMeta();
    return;
  }
  for (const event of displayEvents) {
    const config = getEventConfig(event.type);
    const card = document.createElement("article");
    card.className = `event-card type-${event.type} tone-${config.tone} ${event.seq === state.selectedSeq ? "selected" : ""}`;

    if (event.type === "tool_event_group") {
      card.appendChild(renderTimelineNode(config));
      const shell = document.createElement("div");
      shell.className = "event-content-shell";
      shell.appendChild(renderToolEventGroup(event, { summaryNode: renderToolEventSummary(event, config) }));
      card.appendChild(shell);
    } else if (event.type === "done") {
      card.appendChild(renderTimelineNode(config));
      const shell = document.createElement("div");
      shell.className = "event-content-shell";
      shell.appendChild(renderDoneEventSummary(config));
      card.appendChild(shell);
    } else if (event.type === "middleware") {
      card.appendChild(renderTimelineNode(config));
      const shell = document.createElement("div");
      shell.className = "event-content-shell";
      shell.appendChild(renderMiddlewareEventSummary(event, config));
      card.appendChild(shell);
    } else {
      card.innerHTML = `
        <div class="timeline-node"><span>${escapeHtml(config.icon)}</span></div>
        <div class="event-content-shell">
          <div class="event-head">
            <div class="event-title-wrap">
              <span class="event-type-badge tone-${config.tone}">${escapeHtml(config.label)}</span>
            </div>
            <time class="event-time mono">${escapeHtml(formatTime(event.timestamp))}</time>
          </div>
          ${["assistant_message", "user_message"].includes(event.type) ? "" : `<div class="event-summary">${escapeHtml(eventSummary(event))}</div>`}
          <div class="event-body"></div>
        </div>
      `;
      card.querySelector(".event-body").appendChild(renderEventBody(event));
    }

    card.addEventListener("click", () => {
      state.selectedSeq = event.seq;
      state.detailMode = "event";
      renderDetailPanel();
      renderEvents();
    });
    els.timeline.appendChild(card);
  }
  updateRuntimeMeta();
}

function renderTimelineNode(config) {
  const node = document.createElement("div");
  node.className = "timeline-node";
  node.innerHTML = `<span>${escapeHtml(config.icon)}</span>`;
  return node;
}

function renderToolEventSummary(event, config) {
  const summary = document.createElement("summary");
  summary.className = "tool-event-summary-inline";
  summary.innerHTML = `
    <span class="tool-event-summary-main">
      <span class="event-type-badge tone-${config.tone}">${escapeHtml(config.label)}</span>
      <span class="tool-event-name">${escapeHtml(event.payload.tool_name || "unknown")}</span>
    </span>
    <time class="event-time mono">${escapeHtml(formatTime(event.timestamp))}</time>
  `;
  return summary;
}

function renderMiddlewareEventSummary(event, config) {
  const summary = document.createElement("div");
  summary.className = "tool-event-summary-inline middleware-event-summary-inline";
  summary.innerHTML = `
    <span class="tool-event-summary-main">
      <span class="event-type-badge tone-${config.tone}">${escapeHtml(config.label)}</span>
      <span class="tool-event-name">${escapeHtml(event.payload.name || "middleware")}</span>
    </span>
    <time class="event-time mono">${escapeHtml(formatTime(event.timestamp))}</time>
  `;
  return summary;
}

function renderDoneEventSummary(config) {
  const summary = document.createElement("div");
  summary.className = "done-event-summary-inline";
  summary.innerHTML = `<span class="event-type-badge tone-${config.tone}">${escapeHtml(config.label)}</span>`;
  return summary;
}

function groupToolEventsForDisplay(events) {
  const output = [];
  const pendingGroupById = new Map();

  for (const event of events) {
    if (event.type === "tool_use") {
      const group = createToolGroup({ toolUse: event });
      output.push(group);
      const id = getToolUseId(event);
      if (id) pendingGroupById.set(id, group);
      continue;
    }

    if (event.type === "tool_result") {
      const id = getToolResultId(event);
      const group = id ? pendingGroupById.get(id) : null;
      if (group) {
        attachToolResult(group, event);
        pendingGroupById.delete(id);
      } else {
        output.push(createToolGroup({ toolResult: event }));
      }
      continue;
    }

    output.push(event);
  }

  return output;
}

function getToolUseId(event) {
  return event?.payload?.id || event?.payload?.tool_call_id || event?.payload?.call_id || event?.id || null;
}

function getToolResultId(event) {
  return event?.payload?.tool_call_id || event?.payload?.id || event?.payload?.call_id || event?.id || null;
}

function createToolGroup({ toolUse = null, toolResult = null }) {
  const toolName = toolUse?.payload?.tool_name || toolResult?.payload?.tool_name || "unknown";
  const toolCallId = getToolUseId(toolUse) || getToolResultId(toolResult);
  const group = {
    type: "tool_event_group",
    thread_id: toolUse?.thread_id || toolResult?.thread_id,
    agent_id: toolUse?.agent_id || toolResult?.agent_id,
    seq: toolUse?.seq || toolResult?.seq,
    timestamp: toolUse?.timestamp || toolResult?.timestamp,
    payload: {
      tool_name: toolName,
      tool_call_id: toolCallId,
      has_use: Boolean(toolUse),
      has_result: Boolean(toolResult),
      use: toolUse?.payload || null,
      result: toolResult?.payload || null,
    },
    events: [],
  };
  if (toolUse) group.events.push(toolUse);
  if (toolResult) group.events.push(toolResult);
  return group;
}

function attachToolResult(group, toolResult) {
  group.payload.has_result = true;
  group.payload.result = toolResult.payload;
  group.events.push(toolResult);
}

function renderEmptyState(message = "输入问题并发送后，这里会显示 Skill 加载、工具调用、interrupt 和最终结果。") {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.innerHTML = `
    <div class="empty-orbit"><span>✦</span></div>
    <h2>等待 Agent 事件</h2>
    <p>${escapeHtml(message)}</p>
  `;
  return empty;
}

function renderEventBody(event) {
  switch (event.type) {
    case "skill_loaded":
      return document.createDocumentFragment();
    case "user_message":
    case "assistant_message":
      return renderMarkdown(event.payload.content || "");
    case "tool_event_group":
      return renderToolEventGroup(event);
    case "tool_use":
      return details(`工具调用：${event.payload.tool_name || "unknown"}`, event.payload);
    case "tool_result":
      return details(`工具返回：${event.payload.tool_name || "unknown"}`, event.payload);
    case "interrupt":
      return renderInterrupt(event);
    case "error":
      return text(event.payload.message || JSON.stringify(event.payload), "error");
    case "middleware":
      return details(`Middleware：${event.payload.name}`, event.payload);
    case "done":
      return text("事件流结束");
    default:
      return details("调试事件", event.raw ?? event.payload);
  }
}

function renderToolEventGroup(group, { summaryNode = null } = {}) {
  const detailsNode = document.createElement("details");
  detailsNode.className = "tool-event-details";
  detailsNode.addEventListener("click", (clickEvent) => {
    clickEvent.stopPropagation();
  });

  const summary = summaryNode || document.createElement("summary");
  if (!summaryNode) summary.textContent = "展开查看 Tool Use / Tool Result";
  detailsNode.appendChild(summary);

  const container = document.createElement("div");
  container.className = "interrupt-form tool-event-group-body";

  const toolUse = group.events.find((event) => event.type === "tool_use");
  const toolResult = group.events.find((event) => event.type === "tool_result");
  const resultStatus = getToolGroupStatus(group);

  const useStatus = toolUse ? "arguments" : "missing";
  container.appendChild(toolSubCard({
    title: "Tool Use",
    status: useStatus,
    event: toolUse,
    contentNode: toolUse ? toolEventContentNode(group, toolUse.payload?.args || {}) : emptyToolText("未收到 tool_use 事件"),
    onExpand: toolUse ? () => openToolEventModal({
      title: "Tool Use",
      status: useStatus,
      toolName: group.payload.tool_name,
      contentNode: () => toolEventContentNode(group, toolUse.payload?.args || {}),
    }) : null,
  }));

  const resultValue = toolResult?.payload?.content ?? toolResult?.payload;
  const resultStatusLabel = group.payload.has_result ? resultStatus.label : "waiting";
  container.appendChild(toolSubCard({
    title: "Tool Result",
    status: resultStatusLabel,
    event: toolResult,
    contentNode: toolResult ? toolEventContentNode(group, resultValue) : emptyToolText("等待工具返回…"),
    isError: resultStatus.isError,
    onExpand: toolResult ? () => openToolEventModal({
      title: "Tool Result",
      status: resultStatusLabel,
      toolName: group.payload.tool_name,
      isError: resultStatus.isError,
      contentNode: () => toolEventContentNode(group, resultValue),
    }) : null,
  }));

  detailsNode.appendChild(container);
  return detailsNode;
}

function toolSubCard({ title, status, event, contentNode, isError = false, onExpand = null }) {
  const section = document.createElement("div");
  section.className = `interrupt-section tool-event-subcard ${event ? "clickable" : ""}`;
  const head = document.createElement("div");
  head.className = "tool-event-subcard-head";

  const titleNode = document.createElement("strong");
  titleNode.className = "tool-event-subtitle";
  titleNode.textContent = title;
  head.appendChild(titleNode);

  const headActions = document.createElement("div");
  headActions.className = "tool-event-subcard-actions";
  const statusNode = document.createElement("span");
  statusNode.className = `tool-event-status ${isError ? "error" : ""}`;
  statusNode.textContent = status;
  headActions.appendChild(statusNode);

  if (onExpand) {
    const expandButton = document.createElement("button");
    expandButton.type = "button";
    expandButton.className = "tool-event-expand-button";
    expandButton.textContent = "展开全部";
    expandButton.addEventListener("click", (clickEvent) => {
      clickEvent.stopPropagation();
      onExpand();
    });
    headActions.appendChild(expandButton);
  }

  head.appendChild(headActions);
  const body = document.createElement("div");
  body.className = "tool-event-subcard-body";
  body.appendChild(contentNode);
  section.appendChild(head);
  section.appendChild(body);

  if (event) {
    section.addEventListener("click", (clickEvent) => {
      clickEvent.stopPropagation();
      state.selectedSeq = event.seq;
      state.detailMode = "event";
      renderDetailPanel();
      renderEvents();
    });
  }

  return section;
}

function openToolEventModal({
  title,
  status,
  toolName,
  contentNode,
  normalizedContentNode = null,
  isError = false,
  subtitle: customSubtitle = null,
}) {
  const backdrop = document.createElement("div");
  backdrop.className = "tool-event-modal-backdrop";

  const modal = document.createElement("div");
  modal.className = "tool-event-modal";
  modal.addEventListener("click", (clickEvent) => {
    clickEvent.stopPropagation();
  });

  const head = document.createElement("div");
  head.className = "tool-event-modal-head";

  const titleWrap = document.createElement("div");
  titleWrap.className = "tool-event-modal-title-wrap";
  const titleNode = document.createElement("strong");
  titleNode.className = "tool-event-modal-title";
  titleNode.textContent = title;
  const subtitle = document.createElement("div");
  subtitle.className = `tool-event-modal-subtitle ${isError ? "error" : ""}`;
  subtitle.textContent = customSubtitle || `工具：${toolName || "unknown"} · 状态：${status}`;
  titleWrap.appendChild(titleNode);
  titleWrap.appendChild(subtitle);

  const actions = document.createElement("div");
  actions.className = "tool-event-modal-actions";

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "tool-event-modal-close";
  closeButton.setAttribute("aria-label", "关闭");
  closeButton.title = "关闭";
  const closeIcon = document.createElement("img");
  closeIcon.src = "/assets/close.svg";
  closeIcon.alt = "";
  closeIcon.setAttribute("aria-hidden", "true");
  closeButton.appendChild(closeIcon);

  head.appendChild(titleWrap);
  head.appendChild(actions);

  const body = document.createElement("div");
  body.className = "tool-event-modal-body";
  body.appendChild(contentNode());

  if (normalizedContentNode) {
    let showingNormalized = false;
    const normalizeButton = document.createElement("button");
    normalizeButton.type = "button";
    normalizeButton.className = "tool-event-modal-normalize";
    normalizeButton.title = "规格化内容探查";
    const normalizeIcon = document.createElement("img");
    normalizeIcon.src = "/assets/txt.svg";
    normalizeIcon.alt = "";
    normalizeIcon.setAttribute("aria-hidden", "true");
    const normalizeText = document.createElement("span");
    normalizeText.textContent = "规格化内容探查";
    normalizeButton.appendChild(normalizeIcon);
    normalizeButton.appendChild(normalizeText);
    normalizeButton.addEventListener("click", () => {
      showingNormalized = !showingNormalized;
      body.replaceChildren(showingNormalized ? normalizedContentNode() : contentNode());
      normalizeButton.classList.toggle("active", showingNormalized);
      normalizeText.textContent = showingNormalized ? "查看原始内容" : "规格化内容探查";
      normalizeButton.title = normalizeText.textContent;
    });
    actions.appendChild(normalizeButton);
  }

  actions.appendChild(closeButton);

  modal.appendChild(head);
  modal.appendChild(body);
  backdrop.appendChild(modal);

  const closeModal = () => {
    document.removeEventListener("keydown", handleKeydown);
    backdrop.remove();
  };
  const handleKeydown = (keyEvent) => {
    if (keyEvent.key === "Escape") closeModal();
  };

  closeButton.addEventListener("click", closeModal);
  backdrop.addEventListener("click", closeModal);
  document.addEventListener("keydown", handleKeydown);
  document.body.appendChild(backdrop);
  closeButton.focus();
}

function toolEventContentNode(group, value) {
  const jsonContent = jsonMarkdownValue(value);
  if (jsonContent) return renderMarkdown(jsonContent);

  const content = normalizeMarkdownContent(markdownValue(value));
  if (isReadTool(group)) return renderMarkdown(stripMarkdownFrontmatter(stripReadLineNumbers(content)));
  return renderMarkdown(content);
}

function isReadTool(group) {
  return ["read", "read_file"].includes((group.payload.tool_name || "").toLowerCase());
}

function stripReadLineNumbers(value) {
  return String(value || "").replace(/^\s*\d+(?:\t|\\t)/gm, "");
}

function stripMarkdownFrontmatter(value) {
  return String(value || "").replace(/^﻿?\s*---\s*\n[\s\S]*?\n\s*---\s*(?:\n|$)/, "");
}

function jsonMarkdownValue(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed || !/^[{[]/.test(trimmed)) return null;
    try {
      return `\`\`\`json\n${JSON.stringify(JSON.parse(trimmed), null, 2)}\n\`\`\``;
    } catch {
      return null;
    }
  }
  try {
    return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``;
  } catch {
    return null;
  }
}

function markdownValue(value) {
  return typeof value === "string" ? value : formatValue(value);
}

function preformatted(value) {
  const node = document.createElement("pre");
  node.className = "tool-event-pre";
  node.textContent = formatValue(value);
  return node;
}

function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getToolGroupStatus(group) {
  if (isToolResultError(group.payload.result)) return { label: "error", isError: true };
  if (group.payload.has_use && group.payload.has_result) return { label: "completed", isError: false };
  if (group.payload.has_use) return { label: "running", isError: false };
  return { label: "result only", isError: false };
}

function isToolResultError(result) {
  if (!result) return false;
  return result.is_error === true || result.error || result.status === "error" || result.content?.error;
}

function emptyToolText(value) {
  return renderMarkdown(value);
}

function renderInterrupt(event) {
  const form = document.createElement("form");
  form.className = "interrupt-form";
  const sections = event.payload.sections || [];
  const submission = event.payload.submission || null;
  const submitted = Boolean(submission);
  form.innerHTML = `<strong class="interrupt-title">需要用户确认：${escapeHtml(event.payload.interrupt_type || "interrupt")}</strong>`;

  sections.forEach((section, sectionIndex) => {
    const submittedSection = submission?.sections?.[sectionIndex];
    const box = document.createElement("div");
    box.className = "interrupt-section";
    const title = section.title || section.name || section.label || `选项 ${sectionIndex + 1}`;
    const selectMode = section.select_mode || section.selectMode || "single";
    const inputType = selectMode === "multiple" ? "checkbox" : "radio";
    const groupName = `section-${event.seq}-${sectionIndex}`;
    const options = section.options || section.items || section.choices || [];
    box.innerHTML = `<strong>${escapeHtml(title)}</strong>`;

    options.forEach((option, optionIndex) => {
      const value = option.value ?? option.id ?? option.label ?? option.name ?? String(option);
      const label = option.label ?? option.name ?? option.alias ?? value;
      const wrapper = document.createElement("label");
      wrapper.className = "option";
      wrapper.innerHTML = `<input type="${inputType}" name="${groupName}" value="${escapeHtml(String(value))}"> <span>${escapeHtml(String(label))}</span>`;
      const input = wrapper.querySelector("input");
      if (submittedSection) {
        input.checked = submittedSection.checked.includes(String(value));
        input.disabled = true;
      } else if (optionIndex === 0 && inputType === "radio") {
        input.checked = true;
      }
      box.appendChild(wrapper);
    });

    if (section.allow_freeform === true || section.allowFreeform === true) {
      const textarea = document.createElement("textarea");
      textarea.className = "freeform";
      textarea.name = `${groupName}-freeform`;
      textarea.placeholder = section.freeform_hint || "补充说明";
      if (submittedSection) {
        textarea.value = submittedSection.freeform;
        textarea.disabled = true;
      }
      box.appendChild(textarea);
    }
    form.appendChild(box);
  });

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "primary";
  submit.textContent = submitted ? "已提交并确认" : "提交并继续";
  submit.disabled = submitted;
  form.appendChild(submit);

  form.addEventListener("click", (clickEvent) => {
    clickEvent.stopPropagation();
  });

  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    submitEvent.stopPropagation();
    if (event.payload.submission) return;
    const submission = collectInterruptSubmission(form, sections, event);
    event.payload.submission = submission;
    persist();
    renderEvents();
    await sendMessage(buildInterruptMessage(submission));
  });
  return form;
}

function collectInterruptSubmission(form, sections, event) {
  return {
    sections: sections.map((section, index) => ({
      title: section.title || section.name || section.label || `选项 ${index + 1}`,
      checked: [...form.querySelectorAll(`[name="section-${event.seq}-${index}"]:checked`)].map((input) => input.value),
      freeform: form.querySelector(`[name="section-${event.seq}-${index}-freeform"]`)?.value.trim() || "",
    })),
  };
}

function buildInterruptMessage(submission) {
  const lines = ["用户已经回答了你的问题，并确认了以下内容："];
  submission.sections.forEach((section) => {
    const answer = section.checked.length ? `已确认 ${section.checked.join("、")}` : "未选择";
    lines.push(`${section.title}：${answer}`);
    if (section.freeform) lines.push(`补充说明：${section.freeform}`);
  });
  if (!submission.sections.length) lines.push("用户已确认。");
  lines.push("请继续!");
  return lines.join("\n");
}

function details(summary, value) {
  const node = document.createElement("details");
  const json = JSON.stringify(value, null, 2);
  node.innerHTML = `<summary>${escapeHtml(summary)}</summary><pre>${highlightJson(json)}</pre>`;
  return node;
}

function text(value, className = "") {
  const node = document.createElement("div");
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function renderMarkdown(content, options = {}) {
  const node = document.createElement("div");
  node.className = "markdown-body";

  if (!window.markdownit || !window.DOMPurify) {
    node.textContent = content;
    return node;
  }

  const markdown = window.markdownit({
    html: options.html === true,
    linkify: true,
    breaks: true,
  });
  markdown.enable("table");
  const unsafeHtml = markdown.render(normalizeMarkdownContent(content));
  node.innerHTML = window.DOMPurify.sanitize(unsafeHtml, {
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
  });

  for (const code of node.querySelectorAll('pre > code.language-json, pre > code[class*="language-json"]')) {
    if (code.closest(".llm-schema-json")) continue;
    code.innerHTML = highlightJson(code.textContent || "");
  }

  for (const link of node.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href") || "";
    if (!isSafeLink(href)) {
      link.removeAttribute("href");
      continue;
    }
    link.target = "_blank";
    link.rel = "noopener noreferrer nofollow";
  }

  return node;
}

function normalizeMarkdownContent(content) {
  return String(content || "")
    .replaceAll("\\r\\n", "\n")
    .replaceAll("\\n", "\n")
    .replaceAll("\\t", "\t");
}

function isSafeLink(href) {
  if (!/^[a-z][a-z0-9+.-]*:/i.test(href)) return false;
  try {
    const url = new URL(href);
    return ["http:", "https:", "mailto:", "tel:"].includes(url.protocol);
  } catch {
    return false;
  }
}

function renderDetailPanel() {
  els.showEventDetail.classList.toggle("active", state.detailMode === "event");
  els.showLlmTrace.classList.toggle("active", state.detailMode === "trace");
  if (state.detailMode === "trace") {
    setTraceDetail();
    return;
  }
  const event = state.events.find((item) => item.seq === state.selectedSeq) || null;
  setDetail(event);
}

async function refreshTraces() {
  try {
    const url = `/api/sessions/${encodeURIComponent(state.threadId)}/llm-traces?agent_id=${encodeURIComponent(state.agentId)}`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.traces = data.events || [];
  } catch (error) {
    state.traces = [{ event_type: "error", payload: { message: error.message || String(error) } }];
  }
}

function setTraceDetail() {
  const groups = groupLlmTracesForDisplay(state.traces);
  const llmTraceCount = state.traces.filter((trace) => ["llm_input", "llm_output", "llm_error"].includes(trace.event_type)).length;
  els.eventMeta.className = "event-meta";
  els.eventMeta.innerHTML = `<span class="meta-pill mono">${escapeHtml(state.threadId)}</span><span>${state.traces.length} 条 trace</span><span>${llmTraceCount} 条 LLM trace</span><span>${groups.length} 次 LLM 调用</span>`;
  if (!state.traces.length) {
    els.detail.textContent = "当前 thread 暂无 LLM Trace";
    return;
  }
  if (!groups.length) {
    els.detail.textContent = "非 LLM trace 已隐藏，请仅查看 llm_input / llm_output / llm_error。";
    return;
  }

  const container = document.createElement("div");
  container.className = "trace-list";
  for (const group of groups) {
    container.appendChild(renderLlmTraceGroup(group));
  }
  els.detail.replaceChildren(container);
}

function groupLlmTracesForDisplay(traces) {
  const groups = [];
  const groupsByFallbackKey = new Map();
  const groupsByRunId = new Map();
  let latestOpenGroup = null;

  for (const trace of traces) {
    if (!["llm_input", "llm_output", "llm_error"].includes(trace.event_type)) continue;

    let group = findLlmTraceGroup(trace, groupsByFallbackKey, groupsByRunId);
    if (!group && trace.event_type !== "llm_input" && latestOpenGroup && !latestOpenGroup.output && !latestOpenGroup.error) {
      group = latestOpenGroup;
    }
    if (!group) {
      group = createLlmTraceGroup(trace);
      groups.push(group);
    }

    attachLlmTraceToGroup(group, trace);
    indexLlmTraceGroup(group, groupsByFallbackKey, groupsByRunId);
    if (trace.event_type === "llm_input") latestOpenGroup = group;
    if (trace.event_type === "llm_output" || trace.event_type === "llm_error") latestOpenGroup = null;
  }

  return groups;
}

function findLlmTraceGroup(trace, groupsByFallbackKey, groupsByRunId) {
  if (trace.run_id) return groupsByRunId.get(trace.run_id) || null;
  const fallbackKey = traceFallbackGroupKey(trace);
  return fallbackKey ? groupsByFallbackKey.get(fallbackKey) || null : null;
}

function traceFallbackGroupKey(trace) {
  return trace.parent_run_id || null;
}

function createLlmTraceGroup(trace) {
  return {
    type: "llm_trace_group",
    seq: trace.seq,
    created_at: trace.created_at,
    request_id: trace.request_id,
    run_id: trace.run_id,
    fallback_key: traceFallbackGroupKey(trace),
    model_name: trace.model_name,
    token_usage: trace.token_usage,
    input: null,
    output: null,
    error: null,
    traces: [],
  };
}

function attachLlmTraceToGroup(group, trace) {
  group.traces.push(trace);
  if (trace.event_type === "llm_input") group.input = trace;
  if (trace.event_type === "llm_output") group.output = trace;
  if (trace.event_type === "llm_error") group.error = trace;

  group.seq = group.seq ?? trace.seq;
  group.created_at = group.created_at || trace.created_at;
  group.request_id = group.request_id || trace.request_id;
  group.run_id = group.run_id || trace.run_id;
  group.fallback_key = group.fallback_key || traceFallbackGroupKey(trace);
  group.model_name = group.model_name || trace.model_name;
  group.token_usage = group.token_usage || trace.token_usage;
}

function indexLlmTraceGroup(group, groupsByFallbackKey, groupsByRunId) {
  if (group.run_id) groupsByRunId.set(group.run_id, group);
  if (!group.run_id && group.fallback_key) groupsByFallbackKey.set(group.fallback_key, group);
}

function renderLlmTraceGroup(group) {
  const detailsNode = document.createElement("details");
  detailsNode.className = "trace-item llm-trace-card";
  detailsNode.open = true;
  detailsNode.appendChild(llmTraceGroupSummary(group));

  const container = document.createElement("div");
  container.className = "interrupt-form tool-event-group-body llm-trace-group-body";

  const inputStatus = group.input ? "payload" : "missing";
  container.appendChild(toolSubCard({
    title: "LLM Input",
    status: inputStatus,
    event: null,
    contentNode: llmTraceInputContentNode(group.input),
    onExpand: group.input ? () => openToolEventModal({
      title: "LLM Input",
      status: inputStatus,
      subtitle: llmTraceModalSubtitle(group, inputStatus),
      contentNode: () => llmTraceInputContentNode(group.input),
      normalizedContentNode: () => llmTraceNormalizedContentNode(group.input),
    }) : null,
  }));

  if (group.error) {
    container.appendChild(toolSubCard({
      title: "LLM Error",
      status: "error",
      event: null,
      contentNode: llmTraceErrorContentNode(group.error),
      isError: true,
      onExpand: () => openToolEventModal({
        title: "LLM Error",
        status: "error",
        subtitle: llmTraceModalSubtitle(group, "error"),
        isError: true,
        contentNode: () => llmTraceErrorContentNode(group.error),
        normalizedContentNode: () => llmTraceNormalizedContentNode(group.error),
      }),
    }));
  } else {
    const outputStatus = group.output ? "completed" : "waiting";
    container.appendChild(toolSubCard({
      title: "LLM Output",
      status: outputStatus,
      event: null,
      contentNode: group.output ? llmTraceOutputContentNode(group.output) : emptyToolText("等待 LLM 输出…"),
      onExpand: group.output ? () => openToolEventModal({
        title: "LLM Output",
        status: outputStatus,
        subtitle: llmTraceModalSubtitle(group, outputStatus),
        contentNode: () => llmTraceOutputContentNode(group.output),
        normalizedContentNode: () => llmTraceNormalizedContentNode(group.output),
      }) : null,
    }));
  }

  detailsNode.appendChild(container);
  return detailsNode;
}

function llmTraceGroupSummary(group) {
  const summary = document.createElement("summary");
  summary.className = "llm-trace-summary-inline";
  summary.innerHTML = `
    <span class="tool-event-summary-main">
      <span class="event-type-badge tone-blue">LLM</span>
      ${llmTraceTokenSummary(group)}
    </span>
    <time class="event-time mono">${escapeHtml(formatTime(group.created_at))}</time>
  `;
  return summary;
}

function llmTraceInputContentNode(trace) {
  return llmTracePayloadContentNode(trace, "未收到 llm_input 事件");
}

function llmTraceOutputContentNode(trace) {
  return llmTracePayloadContentNode(trace, "未收到 llm_output 事件");
}

function llmTraceErrorContentNode(trace) {
  return llmTracePayloadContentNode(trace, "未收到 llm_error 事件");
}

function llmTracePayloadContentNode(trace, emptyText) {
  if (!trace) return renderMarkdown(emptyText);
  const jsonContent = jsonMarkdownValue(trace.payload);
  if (jsonContent) return renderMarkdown(jsonContent);
  return renderMarkdown(codeBlock(formatValue(trace.payload)));
}

function llmTraceNormalizedContentNode(trace) {
  return renderMarkdown(llmTraceNormalizedMarkdown(trace), { html: true });
}

function llmTraceNormalizedMarkdown(trace) {
  if (!trace) return "无可展示内容";
  const formatters = window.LLMTraceFormatters;
  if (!formatters) return "# LLM Trace\n\n_Formatters are not available. View raw content instead._";
  switch (trace.event_type) {
    case "llm_input":
      return formatters.formatLLMInput(trace.payload);
    case "llm_output":
      return formatters.formatLLMOutput(trace.payload);
    case "llm_error":
      return formatters.formatLLMError(trace.payload);
    default:
      return [
        "# LLM Trace",
        "",
        "## Overview",
        "",
        "_Unknown trace type. View raw content for the complete payload._",
      ].join("\n");
  }
}

function llmTraceModalSubtitle(group, status) {
  return [
    `模型：${group.model_name || "unknown"}`,
    `状态：${status}`,
  ].filter(Boolean).join(" · ");
}

function codeBlock(value) {
  return `\`\`\`\n${String(value || "无可展示内容")}\n\`\`\``;
}

function llmTraceTokenSummary(group) {
  const usage = group.token_usage || {};
  const input = usage.input_tokens ?? usage.inputTokens ?? usage.prompt_tokens ?? usage.promptTokens ?? usage.prompt;
  const output = usage.output_tokens ?? usage.outputTokens ?? usage.completion_tokens ?? usage.completionTokens ?? usage.completion;
  const parts = [];
  if (input !== undefined) parts.push(`<span class="llm-trace-token-count">Input ${escapeHtml(input)}</span>`);
  if (output !== undefined) parts.push(`<span class="llm-trace-token-count"> & Output ${escapeHtml(output)}</span>`);
  if (!parts.length) return "";
  return `<span class="llm-trace-token-summary">${parts.join("")}</span>`;
}

function traceTokenSummary(trace) {
  if (!trace.token_usage) return "";
  const usage = trace.token_usage;
  const total = usage.total_tokens ?? usage.totalTokens ?? usage.total;
  if (total !== undefined) return ` · tokens ${escapeHtml(total)}`;
  return ` · tokens ${escapeHtml(JSON.stringify(usage))}`;
}

function setDetail(event) {
  if (!event) {
    els.detail.textContent = "选择左侧时间线事件查看详情";
    els.eventMeta.className = "event-meta empty-meta";
    els.eventMeta.textContent = "选择事件后显示元信息";
    return;
  }
  const config = getEventConfig(event.type);
  const detailEvent = detailEventForDisplay(event);
  els.detail.innerHTML = highlightJson(JSON.stringify(detailEvent, null, 2));
  els.eventMeta.className = `event-meta tone-${config.tone}`;
  els.eventMeta.innerHTML = `
    <span class="event-type-badge tone-${config.tone}">${escapeHtml(config.label)}</span>
    <span class="mono">seq #${event.seq}</span>
    <span class="mono">${escapeHtml(formatTime(event.timestamp))}</span>
    <span class="mono truncate">${escapeHtml(event.thread_id || "")}</span>
  `;
}

function detailEventForDisplay(event) {
  const detailEvent = structuredClone(event);
  if (detailEvent.type === "assistant_message" && detailEvent.raw?.model?.messages) {
    delete detailEvent.raw.model.messages;
  }
  return detailEvent;
}

function setStatus(value) {
  const isRunning = value !== "未连接";
  els.status.className = `runtime-status ${isRunning ? "running" : ""}`;
  els.status.innerHTML = `<span class="status-dot ${isRunning ? "running" : "idle"}"></span>${escapeHtml(value)}`;
}

function updateRuntimeMeta() {
  els.eventCount.textContent = String(state.events.length);
  els.sessionPill.textContent = state.threadId;
}

function getEventConfig(type) {
  return EVENT_TYPE_CONFIG[type] || EVENT_TYPE_CONFIG.raw;
}

function eventSummary(event) {
  switch (event.type) {
    case "user_message":
      return trimSummary(event.payload.content || "User message");
    case "assistant_message":
      return trimSummary(event.payload.content || "Assistant message");
    case "skill_loaded":
      return `加载 ${skillLoadedName(event)} skill`;
    case "middleware":
      return event.payload.name || "Middleware event";
    case "tool_event_group":
      return `Tool：${event.payload.tool_name || "unknown"}`;
    case "tool_use":
      return `工具调用：${event.payload.tool_name || "unknown"}`;
    case "tool_result":
      return `工具返回：${event.payload.tool_name || "unknown"}`;
    case "interrupt":
      return `等待用户确认：${event.payload.interrupt_type || "interrupt"}`;
    case "error":
      return event.payload.message || "发生错误";
    case "done":
      return "事件流已结束";
    default:
      return "调试事件";
  }
}

function skillLoadedName(event) {
  const skills = event.payload.skills || [];
  const names = skills.map((skill) => skill.name || skill.id || skill.skill_name || skill).filter(Boolean);
  return names.length ? names.join("、") : "unknown";
}

function trimSummary(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

function formatTime(value) {
  if (!value) return "--:--:--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function highlightJson(json) {
  const source = String(json ?? "");
  let output = "";
  let index = 0;

  while (index < source.length) {
    const char = source[index];

    if (char === '"') {
      const start = index;
      index += 1;
      while (index < source.length) {
        if (source[index] === "\\") {
          index += 2;
          continue;
        }
        if (source[index] === '"') {
          index += 1;
          break;
        }
        index += 1;
      }
      const token = source.slice(start, index);
      let lookahead = index;
      while (/\s/.test(source[lookahead] || "")) lookahead += 1;
      const className = source[lookahead] === ":" ? "json-key" : "json-string";
      output += `<span class="${className}">${escapeHtml(token)}</span>`;
      continue;
    }

    const rest = source.slice(index);
    const literal = rest.match(/^(true|false|null)\b/);
    if (literal) {
      output += `<span class="json-literal">${literal[0]}</span>`;
      index += literal[0].length;
      continue;
    }

    const number = rest.match(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (number) {
      output += `<span class="json-number">${number[0]}</span>`;
      index += number[0].length;
      continue;
    }

    if ("{}[]:,".includes(char)) {
      output += `<span class="json-punctuation">${escapeHtml(char)}</span>`;
    } else {
      output += escapeHtml(char);
    }
    index += 1;
  }

  return output;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
