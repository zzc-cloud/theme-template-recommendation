"""前端确认页面 interrupt 工具。

本模块提供 DeepAgents 可调用的确认页 payload 构造器。该工具调用会被
`interrupt_on` 作为 human-in-the-loop 暂停点拦截；本模块不负责持久化、
恢复执行或解析用户确认结果。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Option(BaseModel):
    """确认项。"""

    label: str = Field(description="展示文案，如“二级账务机构名称 = 南京分行”。")
    description: str = Field(description="补充说明，可为空字符串。")
    value: str = Field(description="回传标识，如 org=南京分行 或 dim=loan_risk。")


class Section(BaseModel):
    """确认区块。"""

    key: str = Field(description="区块标识，如 candidate_filters 或 candidate_dimensions。")
    title: str = Field(description="区块标题文案，直接展示给用户。")
    select_mode: Literal["single", "multiple", "none"] = Field(
        description="选择模式：single 单选，multiple 多选，none 纯展示。"
    )
    options: list[Option] = Field(description="候选项列表。")
    allow_freeform: bool = Field(description="是否允许用户直接输入修改意见。")
    freeform_hint: str = Field(description="自由输入框提示文案。")


def AskUserQuestion_tools(
    thread_id: str,
    sections: list[Section],
    interrupt_type: str = "user_question",
) -> dict[str, Any]:
    """构造前端确认页面的 interrupt payload。

    参数描述的是“待用户确认的页面内容”，不是用户最终确认结果。
    在 DeepAgents 注册流程中，该工具名由 `interrupt_on` 拦截；因此该 dict
    用于生成 interrupt payload，而不是作为普通工具结果交还模型。
    用户确认内容由后续同 thread_id 的恢复请求带回。
    """
    return {
        "interrupt_type": interrupt_type,
        "thread_id": thread_id,
        "sections": [
            section.model_dump()
            if isinstance(section, Section)
            else Section.model_validate(section).model_dump()
            for section in sections or []
        ],
    }
