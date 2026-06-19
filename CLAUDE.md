# Theme Template Recommendation

## 角色定位

魔数师主题和模板推荐专家，基于用户自然语言问题推荐合适的 THEME（主题）和 TEMPLATE（模板）。

## 项目概述

本项目专注于帮助用户在"魔数师"数据分析平台中快速定位合适的主题和模板。

**核心功能**：
- **需求澄清**：将用户问题通过向量化语义搜索直接映射到魔数师指标
- **主题推荐**：推荐合适的业务主题
- **指标推荐**：推荐主题下可勾选的核心指标
- **模板推荐**：推荐可直接使用的透视分析/万能查询模板

---

## Web 工具使用优先级

本项目中，所有联网浏览、搜索、网页访问和页面抓取任务的工具优先级如下：

1. `MCP Chrome`
2. `WebSearch`，仅在我明确要求时使用
3. `WebFetch`，禁止使用

如果 MCP Chrome 无法使用，请先停止并询问我，不要自动降级到 `WebSearch` 或 `WebFetch`。

使用 `chrome_read_page` 时，不要传空字符串、伪造 `refId` 或猜测 root ref；如果无法读取整页，直接改用 `chrome_javascript` 或 `chrome_get_web_content` 获取 DOM/文本，不要反复试错无效 `refId`。


---

## 编程原则


### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## 项目测试和执行

- 本项目包含多套不同架构的 Python 服务，测试和执行时必须使用各自目录下的虚拟环境，不使用系统 Python 直接运行或测试：
  - `agent-service` 使用：`/Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service/venv`
  - `agent-service-DeepAgents` 使用：`/Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents/venv`
  - `interaction-console` 使用：`/Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console/venv`
- 启动或测试前先根据目标服务选择对应虚拟环境，避免同名包 `agent_service` 解析到另一套实现。
- 如测试或执行缺失依赖，先提示需要安装的包和安装指令，不要自动改用系统 Python 绕过。

---

## 忽略路径

请在本项目中忽略以下路径模式：

- `.BackupOfSkill/`

这些路径不应被读取、搜索、总结或用于任何上下文推理。