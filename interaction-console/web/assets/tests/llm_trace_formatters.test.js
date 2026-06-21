const test = require("node:test");
const assert = require("node:assert/strict");

const {
  formatLLMInput,
  formatLLMOutput,
  formatToolCalls,
} = require("../llm_trace_formatters.js");

function assertNoRawAppendix(markdown) {
  assert.equal(markdown.includes("JSON Appendix"), false);
  assert.equal(markdown.includes('"messages": ['), false);
  assert.equal(markdown.includes('"choices": ['), false);
}

test("LLM Input 基础摘要包含 overview、parameters、messages、tools，且不含 raw appendix", () => {
  const markdown = formatLLMInput({
    provider: "openai-compatible",
    api_shape: "chat.completions",
    source: "agent",
    payload: {
      model: "test-model",
      temperature: 0.2,
      max_completion_tokens: 1024,
      stream: false,
      messages: [
        { role: "system", content: [{ type: "text", text: "系统规则" }] },
        { role: "user", content: "请分析贷款风险" },
        {
          role: "assistant",
          content: null,
          tool_calls: [
            {
              id: "call_123",
              type: "function",
              function: { name: "search", arguments: JSON.stringify({ query: "贷款风险", top_k: 5 }) },
            },
          ],
        },
        { role: "tool", tool_call_id: "call_123", content: '{"ok":true,"items":[1,2]}' },
      ],
      tools: [
        {
          type: "function",
          function: {
            name: "search",
            description: "搜索指标",
            parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
          },
        },
      ],
    },
  });

  assert.match(markdown, /^# LLM Input/);
  assert.match(markdown, /## Overview/);
  assert.match(markdown, /## Parameters/);
  assert.match(markdown, /## Messages/);
  assert.match(markdown, /## Tools/);
  assert.match(markdown, /openai-compatible/);
  assert.match(markdown, /chat\.completions/);
  assert.match(markdown, /agent/);
  assert.match(markdown, /test-model/);
  assert.match(markdown, /temperature/);
  assert.match(markdown, /max_completion_tokens/);
  assert.match(markdown, /stream/);
  assert.match(markdown, /系统规则/);
  assert.match(markdown, /请分析贷款风险/);
  assert.match(markdown, /call_123/);
  assert.match(markdown, /function/);
  assert.match(markdown, /search/);
  assert.equal(markdown.includes("Tool schemas are hidden for readability"), false);
  assert.equal(markdown.includes("Object(3 keys"), false);
  assert.equal(markdown.includes("Search in raw: object"), false);
  assert.match(markdown, /<table class="llm-tools-table">/);
  assert.match(markdown, /<pre class="llm-schema-json"><code class="language-json"><span class="json-punctuation">\{<\/span><br>&nbsp;&nbsp;<span class="json-key">&quot;type&quot;<\/span>/);
  assert.match(markdown, /&quot;properties&quot;/);
  assert.match(markdown, /&quot;required&quot;/);
  assert.match(markdown, /\|---\|---\|/);
  assertNoRawAppendix(markdown);
});

test("Tools 描述中的换行和尖括号不会吞掉 Schema 列", () => {
  const markdown = formatLLMInput({
    payload: {
      tools: [
        {
          type: "function",
          function: {
            name: "batch_get_indicator_themes",
            description: '批量查询\nArgs:\n    indicator_ids: 指标 ID 列表\nReturns:\n    {"id":"theme"}</td><td>bad',
            parameters: { type: "object", required: ["indicator_ids"], properties: { indicator_ids: { type: "array", items: { type: "string" } } } },
          },
        },
        {
          type: "function",
          function: {
            name: "AskUserQuestion_tools",
            description: "构造前端确认页面的 interrupt payload。\n参数描述的是待用户确认的页面内容。",
            parameters: { type: "object", required: ["thread_id", "sections"], properties: { thread_id: { type: "string" }, sections: { type: "array" } } },
          },
        },
      ],
    },
  });

  assert.match(markdown, /batch_get_indicator_themes/);
  assert.match(markdown, /AskUserQuestion_tools/);
  assert.equal((markdown.match(/<pre class="llm-schema-json">/g) || []).length, 2);
  assert.match(markdown, /&lt;\/td&gt;&lt;td&gt;bad/);
  assert.match(markdown, /批量查询<br>Args:/);
});


test("LLM Output stop 摘要展示 id、model、usage、choice、短内容与长内容 raw 提示", () => {
  const longText = `短回答。${"这是很长的补充内容".repeat(120)}`;
  const markdown = formatLLMOutput({
    id: "chatcmpl_1",
    model: "test-model",
    object: "chat.completion",
    usage: { prompt_tokens: 10, completion_tokens: 20, total_tokens: 30 },
    choices: [
      { index: 0, finish_reason: "stop", message: { role: "assistant", content: longText } },
    ],
  }, { maxPreviewChars: 120 });

  assert.match(markdown, /^# LLM Output/);
  assert.match(markdown, /chatcmpl_1/);
  assert.match(markdown, /test-model/);
  assert.match(markdown, /prompt_tokens/);
  assert.match(markdown, /completion_tokens/);
  assert.match(markdown, /total_tokens/);
  assert.match(markdown, /finish_reason|Finish Reason/);
  assert.match(markdown, /stop/);
  assert.match(markdown, /短回答/);
  assert.match(markdown, /Search in raw/);
  assertNoRawAppendix(markdown);
});

test("LLM Output tool_calls 摘要展示 tool calls、长 arguments 和 reasoning_content 存在提示", () => {
  const longArgs = JSON.stringify({ query: "风险".repeat(800), filters: { branch: "南京" } });
  const markdown = formatLLMOutput({
    model: "test-model",
    choices: [
      {
        index: 0,
        finish_reason: "tool_calls",
        message: {
          role: "assistant",
          reasoning_content: "这里是很长的思考内容，不应默认完整展示。",
          content: null,
          tool_calls: [
            { id: "call_long", type: "function", function: { name: "lookup", arguments: longArgs } },
          ],
        },
      },
    ],
  }, { maxToolArgumentPreviewChars: 120 });

  assert.match(markdown, /tool_calls/);
  assert.match(markdown, /call_long/);
  assert.match(markdown, /lookup/);
  assert.match(markdown, /Arguments Type/);
  assert.match(markdown, /Search in raw/);
  assert.match(markdown, /Reasoning content/);
  assert.match(markdown, /Present but hidden by default/);
  assert.equal(markdown.includes("这里是很长的思考内容，不应默认完整展示。"), false);
});

test("短 Tool Arguments 完整展示为 json fenced code block 且不出现 [object Object]", () => {
  const markdown = formatToolCalls([
    { id: "call_short", type: "function", function: { name: "lookup", arguments: '{"a":1,"b":"x"}' } },
  ]);

  assert.match(markdown, /```json\n\{\n  "a": 1,\n  "b": "x"\n\}\n```/);
  assert.equal(markdown.includes("[object Object]"), false);
});

test("非法 JSON Arguments 不崩溃，展示 text preview 和 Search in raw 提示", () => {
  const markdown = formatToolCalls([
    { id: "call_bad", type: "function", function: { name: "broken", arguments: '{"a":1'.repeat(80) } },
  ], { maxToolArgumentPreviewChars: 80 });

  assert.match(markdown, /```text/);
  assert.match(markdown, /\{"a":1/);
  assert.match(markdown, /Search in raw/);
});

test("默认不包含 JSON Appendix 或完整原始 JSON，截断内容提供 raw 查看提示", () => {
  const rawSecretTail = "UNIQUE_RAW_TAIL_SHOULD_NOT_APPEAR";
  const markdown = formatLLMOutput({
    model: "test-model",
    choices: [
      { index: 0, finish_reason: "stop", message: { role: "assistant", content: `${"abc".repeat(200)}${rawSecretTail}` } },
    ],
  }, { maxPreviewChars: 60 });

  assert.equal(markdown.includes("JSON Appendix"), false);
  assert.equal(markdown.includes(rawSecretTail), false);
  assert.match(markdown, /View raw content|Search in raw/);
});
