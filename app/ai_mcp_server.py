"""MCP stdio 工具服务 —— 把 `ai_tools` 那 10 个只读工具挂给 dsh。

**为什么需要这个文件**:换成 DeepSeek Harness 当内核之后,工具循环由 dsh 跑,
我们的工具要挂进去只能走 MCP。dsh 的 MCP client 插件拉起一个 stdio 子进程,
通过 newline-delimited JSON-RPC 问它「有哪些工具」「调用某个工具」。

**为什么是纯标准库**:这个服务端只用到 `initialize` / `tools/list` / `tools/call`
三个方法,手写一遍比引入 `mcp` 包更可控 —— 而且不往 requirements 里加依赖。
(踩过的坑:`mcp` 包 2.x 把 `FastMCP` 改名成 `MCPServer`,照 1.x 写会 import 崩,
而 dsh 那边只会报「初次连接失败」,看着像 dsh 的锅。见 README 尾部的排查说明。)

## 身份从哪来

工具要按提问者的权限裁剪(草稿帖子对普通用户不可见),而这是个**独立进程**,
拿不到 FastAPI 请求里的 `Viewer`。所以身份走**环境变量**,由 `ai_agent.py`
在起 harness 实例时写进该实例自己的 patch 文件里 —— 一个会话一个实例,
每个实例的 MCP 子进程带着各自的 viewer 快照。

**身份缺失就拒绝服务,不猜、不降级。** 拿不准提问者是谁时,唯一安全的做法是
不回答 —— 猜成管理员会泄露,猜成游客会让工具莫名其妙查不到东西。

## 只读

这里挂出去的工具**全是只读查询**(见 `ai_tools.py` 模块头)。本地 shell 工具
(`persistent-bash` / `persistent-pwsh`)在 profile patch 里被显式禁掉了 ——
`ai_agent.py` 生成的 patch 带着那两条 `disabled: true`。

## 用法

    python -m app.ai_mcp_server

stdout 只走协议,**日志一律走 stderr** —— 往 stdout 写一个 print 就会破坏帧。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from .ai_tools import TOOLS, Viewer, run_tool
from .db import SessionLocal

log = logging.getLogger("ai_mcp_server")

# dsh 那边把工具名拼成 `mcp__<serverName>__<rawName>`,serverName 在 patch 里配。
SERVER_NAME = "producthub"

VIEWER_ID_ENV = "PRODUCTHUB_VIEWER_ID"
VIEWER_ROLE_ENV = "PRODUCTHUB_VIEWER_ROLE"
VIEWER_NAME_ENV = "PRODUCTHUB_VIEWER_NAME"


# ---------------------------------------------------------------- 协议收发

def _send(message: dict) -> None:
    """写一帧。必须 flush —— 管道那头的 node 在等这一行。"""
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_result(msg_id: Any, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _send_error(msg_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _log(text: str) -> None:
    """日志走 stderr。stdout 是协议通道,不能碰。"""
    print(f"[ai-mcp] {text}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 身份

def _viewer_from_env() -> Viewer:
    """从环境变量取提问者快照。缺一个就退出。

    这里**不是**「防御模型注入」—— 环境变量由我们自己的代码写进 patch 文件,
    模型碰不到。防的是「配置漏了却静默跑起来」:那样工具会以一个身份不明的
    视角查库,查出来的东西是不是该给这个人看,就没人保证了。
    """
    missing = [
        k for k in (VIEWER_ID_ENV, VIEWER_ROLE_ENV) if not os.getenv(k)
    ]
    if missing:
        _log(f"缺少环境变量 {missing} —— 不知道提问者是谁,拒绝服务。")
        sys.exit(2)
    raw_id = os.environ[VIEWER_ID_ENV]
    try:
        viewer_id = int(raw_id)
    except ValueError:
        _log(f"{VIEWER_ID_ENV}={raw_id!r} 不是整数,拒绝服务。")
        sys.exit(2)
    return Viewer(
        id=viewer_id,
        role=os.environ[VIEWER_ROLE_ENV],
        name=os.getenv(VIEWER_NAME_ENV, ""),
    )


# ---------------------------------------------------------------- 工具表

def _tool_list() -> list[dict]:
    """把 `ai_tools.TOOLS` 翻译成 MCP 的工具清单。

    **从 TOOLS 派生,不另抄一份** —— 两份清单必然会漂移,而漂移的表现是
    「某个工具模型看得见却调不动」,很难查。
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            # ToolSpec.parameters 本来就是 JSON Schema 的 object 型,直接用。
            "inputSchema": spec.parameters,
        }
        for spec in TOOLS
    ]


def _call_tool(viewer: Viewer, name: str, args: Any) -> dict:
    """执行一个工具,返回 MCP 的 `tools/call` 结果。

    `run_tool` 已经把**所有失败都收敛成信封**(见它的 docstring),所以这里
    正常路径下不会抛。它返回的文本带着 `<tool_result name="...">` 标记 ——
    那是系统提示里声明过的「这里面是数据」的边界,**保留原样**,与内核无关。
    """
    if not isinstance(args, dict):
        args = {}
    # 每次调用开一个短连接:工具跑完就还回池子,和 v6 里 ai_agent 的做法一致。
    db = SessionLocal()
    try:
        text = run_tool(db, name, args, viewer=viewer)
    finally:
        db.close()
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------- 主循环

def _handle(viewer: Viewer, message: dict) -> None:
    method = message.get("method")
    msg_id = message.get("id")

    # 通知(没有 id)不需要回应。
    if msg_id is None:
        return

    if method == "initialize":
        params = message.get("params") or {}
        _send_result(msg_id, {
            # 回客户端提议的版本,避免版本协商失败。
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
        })
    elif method == "tools/list":
        _send_result(msg_id, {"tools": _tool_list()})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        try:
            _send_result(msg_id, _call_tool(viewer, name, params.get("arguments")))
        except Exception:  # noqa: BLE001 —— 绝不能因为一个工具炸掉就断了整条管道
            log.exception("工具 %s 调用异常", name)
            _send_result(msg_id, {
                "content": [{"type": "text", "text": f"工具 {name} 执行出错"}],
                "isError": True,
            })
    elif method == "ping":
        _send_result(msg_id, {})
    else:
        _send_error(msg_id, -32601, f"未实现的方法:{method}")


def main() -> None:
    viewer = _viewer_from_env()
    _log(f"就绪:viewer={viewer.name!r} id={viewer.id} role={viewer.role},"
         f"工具 {len(TOOLS)} 个")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _log(f"收到非 JSON 的一行,已跳过:{line[:120]!r}")
            continue
        _handle(viewer, message)


if __name__ == "__main__":
    main()
