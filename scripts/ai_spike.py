"""DeepSeek 接入前的实测探针(第六版 · A0)。

**不进生产镜像**:Dockerfile 只 COPY `scripts/docker-entrypoint.sh` 这一个文件
(见 Dockerfile:35),所以 scripts/ 下的其它脚本不会被复制进镜像。

## 为什么要有这个脚本

动 `app/` 之前,下面四件事必须**眼见为实** —— 官方文档要么没写,要么写了也不该直接信:

  1. **一轮带工具调用的完整往返**:请求体到底长什么样,响应里 assistant 消息有哪些字段;
  2. **`reasoning_content` 回传**:带回去能过、不带回去报什么错。
     ⚠️ 这条决定 `ai_messages` 的 schema,不能猜;
  3. **流式下 `reasoning_content` 与 `tool_calls` 参数分片的真实形状**:
     文档只说"是碎的",没说碎成什么样;
  4. **默认 effort 下的真实延迟与 token 花费**:决定 wall-clock 预算与配额默认值,
     这两样文档不给。

## 用法

    export DEEPSEEK_API_KEY=sk-...
    conda activate web
    python scripts/ai_spike.py

不读 .env —— 这是一次性探针,不该依赖应用配置;key 只从环境变量来,不落盘、不打印。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
# 绝不硬编码模型名:DeepSeek 改过一轮命名(deepseek-v4-flash 已是退役别名),
# 写死等于给自己埋一次「没改代码但线上挂了」。生产同理走 AI_MODEL。
MODEL = os.getenv("AI_MODEL", "deepseek-flash")


def _require_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("缺 DEEPSEEK_API_KEY。先 export DEEPSEEK_API_KEY=sk-... 再跑。")
    return key


def _url() -> str:
    return f"{BASE_URL.rstrip('/')}/chat/completions"


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_require_key()}",
        # 照 app/mailer.py 的先例给个正常 UA:某些前置(Cloudflare 之类)会拦 Python-urllib。
        "User-Agent": "producthub-ai-spike/1.0",
    }


def _post(payload: dict, timeout: int = 300):
    """POST /chat/completions。stream=False 返回解析后的 dict;stream=True 返回原始响应对象。"""
    req = urllib.request.Request(
        _url(),
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # HTTPError 必须在 OSError 之前 —— 它是 OSError 的子类,顺序反了就永远读不到错误体。
        body = exc.read().decode("utf-8", "replace")
        print(f"  ✗ HTTP {exc.code}")
        print(f"    原始错误体: {body[:800]}")
        try:
            print(f"    解析后: {json.dumps(json.loads(body), ensure_ascii=False)[:500]}")
        except json.JSONDecodeError:
            print("    (不是合法 JSON)")
        raise
    return resp


def _post_json(payload: dict, timeout: int = 300) -> dict:
    with _post(payload, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- 工具定义

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "按关键词搜索产品库,返回匹配的产品列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回条数,默认 5"},
                },
                "required": ["query"],
            },
        },
    }
]


def _fake_tool_result(args: dict) -> str:
    """假装查了库 —— 探针要测的是**协议**,不是我们的数据库。"""
    return json.dumps(
        {
            "query": args.get("query"),
            "results": [
                {"id": 1, "name": "潮汐基石", "status": "运营中"},
                {"id": 2, "name": "示例产品", "status": "调研中"},
            ],
        },
        ensure_ascii=False,
    )


def _baseline_messages() -> list:
    return [
        {
            "role": "system",
            "content": "你是 producthub 的站内助手。只能用提供的工具查资料,不要编造。",
        },
        {"role": "user", "content": "帮我搜一下「潮汐」相关的产品。"},
    ]


# ---------------------------------------------------------------- 步骤 1

def step1_roundtrip() -> dict:
    """一轮工具调用往返:拿到 assistant 消息,看它到底有哪些字段。"""
    print("\n【1】一轮带工具调用的完整往返")
    started = time.time()
    data = _post_json(
        {"model": MODEL, "messages": _baseline_messages(), "tools": TOOLS}
    )
    elapsed = time.time() - started

    msg = data["choices"][0]["message"]
    print(f"  耗时 {elapsed:.1f}s   usage={data.get('usage')}")
    print(f"  finish_reason={data['choices'][0].get('finish_reason')!r}")
    print(f"  message 的键: {sorted(msg.keys())}")
    print(f"  content={msg.get('content')!r}")
    # 这是本步骤最想知道的一件事:reasoning_content 在不在、是不是空串。
    if "reasoning_content" in msg:
        rc = msg["reasoning_content"]
        print(f"  reasoning_content 存在,长度={len(rc or '')},前 120 字={ (rc or '')[:120]!r}")
    else:
        print("  reasoning_content **不存在**")
    print(f"  tool_calls={json.dumps(msg.get('tool_calls'), ensure_ascii=False)[:400]}")
    return msg


# ---------------------------------------------------------------- 步骤 2

def step2_reasoning_roundtrip(assistant_msg: dict) -> None:
    """把 assistant 消息原样回传 vs 丢掉 reasoning_content —— 对比结果。

    这条直接决定 ai_messages 要不要存 reasoning_content。
    """
    print("\n【2】reasoning_content 回传对比")
    if not assistant_msg.get("tool_calls"):
        print("  ⚠ 上一轮没有 tool_calls,本步骤无法进行(模型可能没调工具)。")
        return

    call = assistant_msg["tool_calls"][0]
    try:
        args = json.loads(call["function"]["arguments"])
    except json.JSONDecodeError:
        args = {}
    tool_msg = {
        "role": "tool",
        "tool_call_id": call["id"],
        "content": _fake_tool_result(args),
    }

    # (a) 原样回传(含 reasoning_content)
    print("\n  (a) 原样回传 assistant 消息(含 reasoning_content):")
    kept = dict(assistant_msg)
    try:
        data = _post_json(
            {
                "model": MODEL,
                "messages": _baseline_messages() + [kept, tool_msg],
                "tools": TOOLS,
            }
        )
        msg = data["choices"][0]["message"]
        print(f"      ✓ 通过。content={ (msg.get('content') or '')[:200]!r}")
        print(f"      usage={data.get('usage')}")
    except urllib.error.HTTPError:
        print("      ✗ 竟然失败了 —— 与文档不符,记下来。")

    # (b) 丢掉 reasoning_content
    print("\n  (b) 丢掉 reasoning_content 后再回传:")
    dropped = {k: v for k, v in assistant_msg.items() if k != "reasoning_content"}
    print(f"      发出前的键: {sorted(dropped.keys())}")
    try:
        data = _post_json(
            {
                "model": MODEL,
                "messages": _baseline_messages() + [dropped, tool_msg],
                "tools": TOOLS,
            }
        )
        print("      ⚠ 竟然过了 —— 说明服务端还在热缓存里。")
        print("        文档说这是**偶发**的:热会话容忍,冷回放(会话恢复 / cache TTL 过期)才硬失败。")
        print("        所以「不带也能过」不能推出「不用存」—— 以文档为准,存。")
    except urllib.error.HTTPError as exc:
        print(f"      ✗ 如文档所说失败了(HTTP {exc.code})—— 这就是那个会偶发的 400。")

    # (c) 空字符串 vs 缺字段
    print("\n  (c) reasoning_content 置为空字符串 ''(而不是删掉该键):")
    empty = dict(assistant_msg)
    empty["reasoning_content"] = ""
    try:
        _post_json(
            {
                "model": MODEL,
                "messages": _baseline_messages() + [empty, tool_msg],
                "tools": TOOLS,
            }
        )
        print("      ✓ 通过。空串是被接受的 —— 所以 NULL 与 '' 必须区分开存。")
    except urllib.error.HTTPError as exc:
        print(f"      ✗ 空串也被拒(HTTP {exc.code})—— 那 schema 要再想。")


# ---------------------------------------------------------------- 步骤 3

def step3_stream_shapes() -> None:
    """流式:看 reasoning_content 与 tool_calls 参数分片的真实形状。"""
    print("\n【3】流式的真实分片形状")
    payload = {
        "model": MODEL,
        "messages": _baseline_messages(),
        "tools": TOOLS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    # index → {"id":..., "name":..., "args": "..."} —— 参数是分片来的,必须按 index 累积
    calls: dict[int, dict] = {}
    chunk_count = 0
    first_delta_keys: set = set()
    started = time.time()

    with _post(payload) as resp:
        print(f"  Content-Type: {resp.headers.get('Content-Type')}")
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[len("data:"):].strip()
            if body == "[DONE]":
                print("  收到 [DONE]")
                break
            chunk_count += 1
            try:
                chunk = json.loads(body)
            except json.JSONDecodeError:
                print(f"  ⚠ 非 JSON 的 data 行: {body[:120]}")
                continue

            if chunk.get("usage"):
                print(f"  末块 usage={chunk['usage']}")

            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                first_delta_keys.update(delta.keys())

                rc = delta.get("reasoning_content")
                if rc:
                    reasoning_parts.append(rc)
                c = delta.get("content")
                if c:
                    content_parts.append(c)

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = calls.setdefault(idx, {"id": None, "name": None, "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

    elapsed = time.time() - started
    print(f"  共 {chunk_count} 个 chunk,耗时 {elapsed:.1f}s")
    print(f"  delta 里出现过的键: {sorted(first_delta_keys)}")
    print(f"  reasoning_content 累积长度={len(''.join(reasoning_parts))}")
    print(f"  content 累积长度={len(''.join(content_parts))}")
    for idx, slot in sorted(calls.items()):
        joined = slot["args"]
        print(f"  tool_calls[{idx}] name={slot['name']!r} id={slot['id']!r}")
        print(f"     参数原文({len(joined)} 字符)={joined[:300]!r}")
        try:
            print(f"     ✓ 累积后能解析: {json.loads(joined)}")
        except json.JSONDecodeError as exc:
            print(f"     ✗ 累积后仍解析失败: {exc}")
    if calls:
        print("  → 结论:参数确实分片到达,必须按 index 累积拼接后再 json.loads。")


# ---------------------------------------------------------------- 步骤 4

def step4_timing(rounds: int = 3) -> None:
    """默认 effort 下的真实延迟与花费 —— 决定 wall-clock 预算和配额默认值。"""
    print(f"\n【4】默认 effort 下的延迟与花费(跑 {rounds} 次)")
    latencies, in_tokens, out_tokens = [], 0, 0
    for i in range(rounds):
        started = time.time()
        try:
            data = _post_json(
                {"model": MODEL, "messages": _baseline_messages(), "tools": TOOLS}
            )
        except urllib.error.HTTPError:
            print(f"  第 {i + 1} 次失败,跳过")
            continue
        elapsed = time.time() - started
        usage = data.get("usage") or {}
        latencies.append(elapsed)
        in_tokens += usage.get("prompt_tokens", 0)
        out_tokens += usage.get("completion_tokens", 0)
        print(
            f"  第 {i + 1} 次: {elapsed:.1f}s  "
            f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}  "
            f"reasoning={usage.get('completion_tokens_details')}"
        )
    if latencies:
        avg = sum(latencies) / len(latencies)
        print(f"\n  平均延迟 {avg:.1f}s(最大 {max(latencies):.1f}s)")
        print(f"  累计 prompt={in_tokens} completion={out_tokens}")
        print(
            "  → 据此定 AI_WALL_CLOCK_BUDGET:一轮问答最多 6 次调用,\n"
            f"     6 × {avg:.0f}s ≈ {6 * avg:.0f}s。若这个数太难看,考虑显式设 reasoning_effort。"
        )


def main() -> None:
    _require_key()
    print(f"探针目标:{_url()}   model={MODEL}")
    print("(key 已读到,不打印)")

    try:
        assistant_msg = step1_roundtrip()
    except urllib.error.HTTPError:
        sys.exit("步骤 1 就失败了 —— 先解决鉴权/模型名/余额,再往下走。")

    step2_reasoning_roundtrip(assistant_msg)
    step3_stream_shapes()
    step4_timing()

    print("\n完成。把上面的输出贴回来 —— 尤其:")
    print("  · 步骤 2(b) 到底过不过(热缓存效应有多强)")
    print("  · 步骤 3 里 tool_calls 参数的分片样子")
    print("  · 步骤 4 的平均延迟与 token")


if __name__ == "__main__":
    main()
