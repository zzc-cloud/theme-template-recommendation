(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.LLMTraceFormatters = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const defaultLLMTraceFormatOptions = {
    includeJsonAppendix: false,
    maxPreviewChars: 800,
    maxMarkdownContentChars: 3000,
    maxToolArgumentPreviewChars: 1500,
    maxArrayPreviewItems: 10,
    showToolSchemas: false,
    showReasoningContent: false,
    showAdditionalFields: false,
    showNullFields: false,
    renderAssistantMarkdown: true,
  };

  function withOptions(options) {
    return { ...defaultLLMTraceFormatOptions, ...(options || {}) };
  }

  function formatLLMInput(input, options) {
    const opts = withOptions(options);
    const payload = tracePayloadInner(input);
    const lines = ["# LLM Input", "", formatOverview(input, "input", opts), "", formatParameters(payload, opts)];
    lines.push("", formatMessages(payload?.messages, opts));
    lines.push("", formatTools(payload?.tools, opts));
    appendJsonAppendix(lines, input, opts);
    return compactBlankLines(lines.join("\n"));
  }

  function formatLLMOutput(output, options) {
    const opts = withOptions(options);
    const lines = ["# LLM Output", "", formatOverview(output, "output", opts), "", formatTokenUsage(output?.usage || output?.token_usage, opts), "", formatChoices(output?.choices, opts)];
    if (!Array.isArray(output?.choices) || !output.choices.length) {
      const fallback = output?.output ?? output?.response ?? output?.content;
      if (fallback !== undefined) {
        lines.push("", "## Content", "", valuePreviewBlock(fallback, opts.maxPreviewChars, "content"));
      }
    }
    appendJsonAppendix(lines, output, opts);
    return compactBlankLines(lines.join("\n"));
  }

  function formatLLMError(error, options) {
    const opts = withOptions(options);
    const payload = error?.error || error || {};
    const rows = [
      ["Type", payload.type ?? error?.type],
      ["Code", payload.code ?? error?.code],
      ["Status", payload.status ?? error?.status],
      ["Message", payload.message ?? error?.message],
    ];
    const lines = ["# LLM Error", "", "## Overview", "", formatMarkdownTable(rows.filter(([, value]) => opts.showNullFields || value !== undefined))];
    for (const field of ["stack", "traceback", "response", "body"]) {
      const value = payload[field] ?? error?.[field];
      if (value !== undefined) lines.push("", `## ${field}`, "", valuePreviewBlock(value, opts.maxPreviewChars, field));
    }
    appendJsonAppendix(lines, error, opts);
    return compactBlankLines(lines.join("\n"));
  }

  function formatOverview(data, kind, options) {
    const opts = withOptions(options);
    const inner = tracePayloadInner(data);
    const rows = [
      ["Provider", data?.provider ?? inner?.provider],
      ["API Shape", data?.api_shape ?? inner?.api_shape],
      ["Source", data?.source ?? inner?.source],
      ["Model", data?.model ?? inner?.model],
      ["ID", data?.id ?? inner?.id],
      ["Object", data?.object ?? inner?.object],
    ];
    if (kind === "output") rows.push(["Finish Reason", firstFinishReason(data)]);
    return ["## Overview", "", formatMarkdownTable(rows.filter(([, value]) => opts.showNullFields || value !== undefined))].join("\n");
  }

  function formatParameters(payload, options) {
    const opts = withOptions(options);
    const fields = [
      "temperature", "max_completion_tokens", "max_tokens", "tool_choice", "stop", "response_format", "stream",
      "top_p", "frequency_penalty", "presence_penalty", "parallel_tool_calls", "seed", "n",
    ];
    const rows = fields.filter((field) => opts.showNullFields || payload?.[field] !== undefined).map((field) => [field, payload?.[field]]);
    return ["## Parameters", "", rows.length ? formatMarkdownTable(rows) : "_No parameters found._"].join("\n");
  }

  function formatMessages(messages, options) {
    const opts = withOptions(options);
    const lines = ["## Messages", ""];
    if (!Array.isArray(messages) || !messages.length) return lines.concat("_No messages found._").join("\n");
    messages.forEach((message, index) => {
      const role = message?.role || "message";
      const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
      lines.push(`### ${index + 1}. ${role}`, "");
      lines.push(formatMarkdownTable([
        ["Role", role],
        ["Name", message?.name],
        ["Tool Call ID", message?.tool_call_id],
        ["Content Type", typeName(message?.content)],
        ["Tool Calls", toolCalls.length],
      ].filter(([, value]) => opts.showNullFields || value !== undefined)));
      lines.push("");
      if (message?.content === null && toolCalls.length) {
        lines.push("_No textual content; message contains tool calls._", "");
      } else if (message?.content !== undefined) {
        lines.push("**Content preview**", "", valuePreviewBlock(message.content, opts.maxPreviewChars, "message content"), "");
      }
      if (toolCalls.length) lines.push(formatToolCalls(toolCalls, opts), "");
    });
    return lines.join("\n");
  }

  function formatTools(tools, options) {
    const opts = withOptions(options);
    const lines = ["## Tools", ""];
    if (!Array.isArray(tools) || !tools.length) return lines.concat("_No tools found._").join("\n");
    lines.push(formatToolsTableHtml(tools, opts));
    return lines.join("\n");
  }

  function formatToolsTableHtml(tools, options) {
    const rows = tools.map((tool, index) => {
      const fn = tool?.function || tool;
      return [
        htmlTableText(fn?.name || tool?.name || `tool_${index + 1}`),
        htmlTableText(tool?.type || fn?.type || "function"),
        htmlTableText(fn?.description || "No description"),
        formatToolSchemaCell(fn?.parameters ?? tool?.parameters),
      ];
    });
    const body = rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("\n");
    return [
      '<table class="llm-tools-table">',
      "<thead><tr><th>Tool</th><th>Type</th><th>Description</th><th>Schema</th></tr></thead>",
      `<tbody>${body}</tbody>`,
      "</table>",
    ].join("\n");
  }

  function formatToolSchemaCell(schema) {
    if (schema === undefined) return "<em>Not provided</em>";
    return `<pre class="llm-schema-json"><code class="language-json">${highlightJsonHtml(safeJson(schema))}</code></pre>`;
  }

  function formatTokenUsage(usage, options) {
    const opts = withOptions(options);
    const lines = ["## Token Usage", ""];
    if (!usage || typeof usage !== "object") return lines.concat("_No token usage found._").join("\n");
    const rows = [];
    for (const key of ["prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]) {
      if (usage[key] !== undefined || opts.showNullFields) rows.push([key, usage[key]]);
    }
    for (const [key, value] of Object.entries(usage)) {
      if (rows.some(([field]) => field === key)) continue;
      if (typeof value === "number" || /tokens/i.test(key)) rows.push([key, summarizeValue(value, opts)]);
    }
    return lines.concat(formatMarkdownTable(rows.length ? rows : Object.entries(usage).map(([k, v]) => [k, summarizeValue(v, opts)]))).join("\n");
  }

  function formatChoices(choices, options) {
    const opts = withOptions(options);
    const lines = ["## Choices", ""];
    if (!Array.isArray(choices) || !choices.length) return lines.concat("_No choices found._").join("\n");
    choices.forEach((choice, index) => {
      const message = choice?.message || choice?.delta || {};
      const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
      lines.push(`### Choice ${index}`, "");
      lines.push(formatMarkdownTable([
        ["Index", choice?.index ?? index],
        ["Finish Reason", choice?.finish_reason],
        ["Role", message?.role],
        ["Content Type", typeName(message?.content)],
        ["Tool Calls", toolCalls.length],
      ].filter(([, value]) => opts.showNullFields || value !== undefined)));
      lines.push("");
      if (message?.reasoning_content) {
        lines.push(opts.showReasoningContent ? "**Reasoning content preview**" : "**Reasoning content**", "");
        lines.push(opts.showReasoningContent ? valuePreviewBlock(message.reasoning_content, opts.maxPreviewChars, "reasoning_content") : "_Present but hidden by default. View raw content for reasoning_content. Search in raw: reasoning_content_", "");
      }
      if (message.content === null && toolCalls.length) {
        lines.push("_No textual content; model requested tool calls._", "");
      } else if (message.content !== undefined) {
        lines.push("**Assistant content preview**", "", valuePreviewBlock(message.content, opts.maxPreviewChars, "assistant content"), "");
      }
      if (toolCalls.length) lines.push(formatToolCalls(toolCalls, opts), "");
    });
    return lines.join("\n");
  }

  function formatToolCalls(toolCalls, options) {
    const opts = withOptions(options);
    const lines = ["#### Tool Calls", ""];
    if (!Array.isArray(toolCalls) || !toolCalls.length) return lines.concat("_No tool calls found._").join("\n");
    toolCalls.forEach((toolCall, index) => {
      const fn = toolCall?.function || {};
      const args = fn.arguments ?? toolCall?.arguments;
      lines.push(`##### ${index + 1}. ${fn.name || toolCall?.name || "tool_call"}`, "");
      lines.push(formatMarkdownTable([
        ["ID", toolCall?.id],
        ["Type", toolCall?.type],
        ["Function", fn.name || toolCall?.name],
        ["Arguments Type", typeName(args)],
      ].filter(([, value]) => opts.showNullFields || value !== undefined)));
      if (args !== undefined) {
        lines.push("", "**Arguments**", "", formatArguments(args, opts));
      }
      lines.push("");
    });
    return lines.join("\n");
  }

  function formatArguments(args, options) {
    const opts = withOptions(options);
    const parsed = tryParseJsonString(args);
    const sourceText = typeof args === "string" ? args : safeJson(args);
    const isShort = sourceText.length <= opts.maxToolArgumentPreviewChars;
    if (isShort && parsed !== args) return formatCodeBlock(parsed, "json");
    if (isShort && typeof parsed !== "string") return formatCodeBlock(parsed, "json");
    if (isShort && looksLikeJsonText(sourceText)) return formatCodeBlock(sourceText, "text");
    const preview = truncateText(sourceText, opts.maxToolArgumentPreviewChars);
    const suffix = sourceText.length > opts.maxToolArgumentPreviewChars ? `\n\n_View raw content for complete arguments. Search in raw: ${extractSearchKeywords(sourceText, opts).join(", ") || "arguments"}_` : "";
    return `${formatCodeBlock(preview, typeof parsed === "string" ? "text" : "json")}${suffix}`;
  }

  function summarizeValue(value, options) {
    const opts = withOptions(options);
    if (value === undefined) return "_Not provided_";
    if (value === null) return "`null`";
    if (typeof value === "string") {
      const parsed = tryParseJsonString(value);
      if (parsed !== value) return `${summarizeValue(parsed, opts)} (JSON string)`;
      return value.length > opts.maxPreviewChars ? `${inlineCode(truncateText(value, opts.maxPreviewChars))} — Search in raw: ${extractSearchKeywords(value, opts).join(", ")}` : inlineCode(value || "");
    }
    if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return inlineCode(String(value));
    if (Array.isArray(value)) {
      if (!value.length) return "`[]`";
      return `Array(${value.length}); first: ${summarizeValue(value[0], opts)}`;
    }
    if (typeof value === "object") {
      const keys = Object.keys(value);
      if (!keys.length) return "`{}`";
      return `Object(${keys.length} keys: ${keys.slice(0, opts.maxArrayPreviewItems).join(", ")}${keys.length > opts.maxArrayPreviewItems ? ", …" : ""})`;
    }
    return inlineCode(String(value));
  }

  function extractSearchKeywords(value, options) {
    const opts = withOptions(options);
    const text = typeof value === "string" ? value : safeJson(value);
    const keywords = [];
    const add = (item) => {
      const normalized = String(item || "").trim().replace(/[`|]/g, "");
      if (normalized && normalized.length >= 3 && !keywords.includes(normalized)) keywords.push(normalized);
    };
    for (const match of text.matchAll(/"(?:id|name|model|type|role|finish_reason|tool_call_id)"\s*:\s*"([^"]{3,80})"/gi)) add(match[1]);
    for (const match of text.matchAll(/[A-Za-z_][A-Za-z0-9_.:-]{8,}/g)) add(match[0]);
    const phrase = text.replace(/\s+/g, " ").trim().slice(0, 80);
    add(phrase);
    return keywords.slice(0, opts.maxArrayPreviewItems);
  }

  function tryParseJsonString(value) {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!trimmed || !/^[{[]/.test(trimmed)) return value;
    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }

  function formatMarkdownTable(rows, options) {
    if (!Array.isArray(rows) || !rows.length) return "_No fields found._";
    const headerProvided = options?.headerProvided === true;
    const tableRows = rows.map((row) => Array.isArray(row) ? row : [row?.field, row?.value]);
    const columnCount = Math.max(...tableRows.map((row) => row.length));
    const header = headerProvided ? tableRows[0] : ["Field", "Value", ...Array.from({ length: Math.max(0, columnCount - 2) }, (_, i) => `Value ${i + 2}`)];
    const body = headerProvided ? tableRows.slice(1) : tableRows;
    const lines = [markdownRow(header, columnCount), `|${header.map(() => "---").join("|")}|`];
    body.forEach((row) => lines.push(markdownRow(row, columnCount)));
    return lines.join("\n");
  }

  function formatCodeBlock(content, language) {
    const text = typeof content === "string" ? content : safeJson(content);
    const ticks = Math.max(3, ...Array.from(text.matchAll(/`+/g), (match) => match[0].length + 1));
    const fence = "`".repeat(ticks);
    return `${fence}${language || ""}\n${text}\n${fence}`;
  }

  function formatScalar(value) {
    if (value === undefined) return "_Not provided_";
    if (value === null) return "`null`";
    if (value === "") return "_Empty string_";
    if (Array.isArray(value) && value.length === 0) return "`[]`";
    if (value && typeof value === "object" && Object.keys(value).length === 0) return "`{}`";
    if (typeof value === "object") return summarizeValue(value);
    return inlineCode(String(value));
  }

  function truncateText(text, maxChars) {
    const source = String(text ?? "");
    if (!maxChars || source.length <= maxChars) return source;
    return `${source.slice(0, Math.max(0, maxChars - 1))}…`;
  }

  function valuePreviewBlock(value, maxChars, label) {
    const parsed = tryParseJsonString(value);
    const text = typeof parsed === "string" ? parsed : safeJson(parsed);
    const truncated = text.length > maxChars;
    const language = typeof parsed === "string" ? "text" : "json";
    const note = truncated ? `\n\n_View raw content for complete ${label || "content"}. Search in raw: ${label || "content"}_` : "";
    return `${formatCodeBlock(truncateText(text, maxChars), language)}${note}`;
  }

  function appendJsonAppendix(lines, payload, options) {
    if (options.includeJsonAppendix) lines.push("", "## JSON Appendix", "", formatCodeBlock(payload, "json"));
  }

  function firstFinishReason(payload) {
    return payload?.choices?.find((choice) => choice?.finish_reason !== undefined)?.finish_reason;
  }

  function tracePayloadInner(payload) {
    return payload?.payload && typeof payload.payload === "object" ? payload.payload : payload || {};
  }

  function typeName(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value;
  }

  function looksLikeJsonText(value) {
    return /^[\s]*[{[]/.test(String(value || ""));
  }

  function safeJson(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function htmlTableText(value) {
    return escapeHtml(value).replace(/\r?\n/g, "<br>");
  }

  function highlightJsonHtml(json) {
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
      if ("{}[]:,".includes(char)) output += `<span class="json-punctuation">${escapeHtml(char)}</span>`;
      else if (char === "\n") output += "<br>";
      else if (char === " ") output += "&nbsp;";
      else if (char === "\t") output += "&nbsp;&nbsp;";
      else output += escapeHtml(char);
      index += 1;
    }
    return output;
  }

  function inlineCode(value) {
    return `\`${String(value).replaceAll("`", "\\`").replaceAll("|", "\\|")}\``;
  }

  function markdownRow(row, columnCount) {
    const cells = Array.from({ length: columnCount }, (_, index) => markdownCell(row[index]));
    return `| ${cells.join(" | ")} |`;
  }

  function markdownCell(value) {
    if (value === "---") return "---";
    if (value === undefined) return "_Not provided_";
    if (value === null) return "`null`";
    if (typeof value === "object") return summarizeValue(value);
    const text = String(value);
    if (text === "") return "_Empty string_";
    return text.replaceAll("|", "\\|").replace(/\s+/g, " ").trim();
  }

  function compactBlankLines(markdown) {
    return String(markdown || "").replace(/\n{3,}/g, "\n\n").trim() + "\n";
  }

  return {
    defaultLLMTraceFormatOptions,
    formatLLMInput,
    formatLLMOutput,
    formatLLMError,
    formatOverview,
    formatParameters,
    formatMessages,
    formatTools,
    formatTokenUsage,
    formatChoices,
    formatToolCalls,
    summarizeValue,
    extractSearchKeywords,
    tryParseJsonString,
    formatMarkdownTable,
    formatCodeBlock,
    formatScalar,
    truncateText,
  };
});
