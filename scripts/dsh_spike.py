"""DeepSeek Harness(dsh)真机探针 —— 换内核之前必须眼见为实的东西。

**不进生产镜像**:Dockerfile 只 COPY `scripts/docker-entrypoint.sh` 这一个文件
(见 Dockerfile),所以 scripts/ 下其余脚本都不会被复制进镜像。

## 为什么要有这个脚本

我读完了官方文档和 dsh 自己的测试,但仍然有三件事**文档没说清、或者说了我不该直接信**:

  1. **流式粒度** —— 这是换不换的胜负手。SDK 的 `run()` 是阻塞的,只给一个
     `on_notification` 回调;官方测试里出现的事件是 `assistant/message` 直接带**整段**
     content。若真机也只有回合级事件,那前端就从「逐字冒」退化成「转圈然后整段出现」。
     **必须量**:每条通知的到达时刻,以及有没有 delta 形状的东西。
     (dsh 内部有 text-delta / reasoning-delta,问题是它们到不到 SDK 这一层。)
  2. **工具集里有没有 shell**。bundled 的 sdk-minimal 带持久化 bash 且 pin 了
     danger-full-access —— 那是「容器内任意代码执行」,和我们现在的**全只读**工具是
     两种安全姿态。要亲眼看到工具被调用的样子。
  3. **能不能真跑起来**:macOS arm64 上 boot 一次,以及 `dsh_home` / workspace 的准备。

## 用法

    no_proxy='*' /tmp/dsh-spike-venv/bin/python scripts/dsh_spike.py

key 从环境变量或仓库根的 .env 读,**只读不打印**。
profile 用 sdk-minimal(工具最少,最好观察);home 与 workspace 都是一次性的 /tmp 目录。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DSH_HOME = Path("/tmp/dsh-spike-home")
WORKSPACE = Path("/tmp/dsh-spike-workspace")
MODEL = os.getenv("AI_MODEL", "deepseek-v4-flash")
PROFILE = os.getenv("DSH_PROFILE", "sdk-minimal")


def _load_key() -> None:
    """把 .env 里的 DEEPSEEK_API_KEY 读进环境(dsh 的 runtime 从环境变量取)。不打印。"""
    if os.getenv("DEEPSEEK_API_KEY"):
        print("key: 已在环境变量里")
        return
    env_file = REPO / ".env"
    if not env_file.exists():
        sys.exit("既没有 DEEPSEEK_API_KEY 环境变量,也没有 .env —— 先配 key。")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k == "DEEPSEEK_API_KEY" and v:
            os.environ["DEEPSEEK_API_KEY"] = v
            print(f"key: 从 .env 读到(长度 {len(v)},不打印)")
            return
    sys.exit(".env 里没有 DEEPSEEK_API_KEY。")


def _prepare_dirs() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    # 放两个探针文件,好让「有没有 shell」这件事可观察
    (WORKSPACE / "hello.txt").write_text("这是 dsh 探针的 workspace。\n")
    (WORKSPACE / "counter.py").write_text("print(1 + 1)\n")
    DSH_HOME.mkdir(parents=True, exist_ok=True)
    print(f"workspace: {WORKSPACE}")
    print(f"dsh_home : {DSH_HOME}")


# ------------------------------------------------------------------ 通知记录

# 这些词出现在事件类型里,就说明有比「回合」更细的粒度
_DELTA_HINTS = ("delta", "partial", "chunk", "stream", "token", "typing")


def _describe(notification) -> tuple:
    """把一条通知压成 (一句话描述, 是否疑似增量)。"""
    method = getattr(notification, "method", "?")
    payload = getattr(notification, "payload", {}) or {}
    event = payload.get("event") if isinstance(payload, dict) else None

    if isinstance(event, dict):
        etype = event.get("type", "?")
        data = event.get("data") or {}
        # 有没有把正文/思考整段带过来?带了多长?
        hint = ""
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    hint = f"  content整段长度={sum(len(t) for t in texts)}"
            text = data.get("text") or data.get("delta")
            if isinstance(text, str):
                hint = f"  text长度={len(text)}"
        desc = f"{method} / {etype}{hint}"
        low = f"{etype}".lower()
    else:
        desc = f"{method} / payload_keys={sorted(payload.keys())[:6]}"
        low = method.lower()

    return desc, any(h in low for h in _DELTA_HINTS)


def _run_one(harness, prompt: str, label: str) -> None:
    """跑一轮,把每条通知的到达时刻打出来 —— 时刻才是流式的证据。"""
    print(f"\n{'=' * 70}\n{label}\n  prompt: {prompt!r}\n{'=' * 70}")
    t0 = time.time()
    timeline: list[tuple[float, str, bool]] = []

    def on_notification(n) -> None:
        desc, is_delta = _describe(n)
        timeline.append((time.time() - t0, desc, is_delta))

    try:
        result = harness.run(prompt, on_notification=on_notification)
    except Exception as exc:  # noqa: BLE001 —— 探针就是要看它怎么炸
        print(f"  ✗ 抛异常 {type(exc).__name__}: {exc}")
        for t, desc, _ in timeline:
            print(f"    t+{t:6.2f}s  {desc}")
        return

    elapsed = time.time() - t0
    print(f"\n  --- 通知时间线(共 {len(timeline)} 条,总耗时 {elapsed:.1f}s)---")
    prev = 0.0
    for t, desc, is_delta in timeline:
        gap = t - prev
        prev = t
        mark = "◆" if is_delta else " "
        print(f"   {mark} t+{t:6.2f}s (+{gap:5.2f}s)  {desc}")

    deltas = [d for _, d, is_d in timeline if is_d]
    print(f"\n  → 疑似增量事件: {len(deltas)} 条")
    if not deltas:
        print("  → ⚠ 没有任何增量形状的事件:只有回合级粒度。")
    else:
        for d in deltas[:10]:
            print(f"      {d}")

    print(f"\n  事件类型({len(result.events)} 条):")
    seen: dict[str, int] = {}
    for ev in result.events:
        seen[ev.get("type", "?")] = seen.get(ev.get("type", "?"), 0) + 1
    for t, n in seen.items():
        print(f"    {n:>3} × {t}")

    print(f"\n  finish_reason={result.finish_reason!r}")
    print(f"  final_response({len(result.final_response)} 字):")
    print(f"    {result.final_response[:600]!r}")

    # usage 在事件里的位置 —— 配额记账要用
    for ev in result.events:
        data = ev.get("data") or {}
        if isinstance(data, dict) and ("usage" in data or "tokens" in str(ev.get("type"))):
            blob = json.dumps(data, ensure_ascii=False)
            if "usage" in blob or "token" in blob.lower():
                print(f"  usage 相关事件 {ev.get('type')}: {blob[:300]}")
                break


def main() -> None:
    _load_key()
    _prepare_dirs()

    print("\n--- 探测 SDK 的公开名字 ---")
    try:
        import deepseek_harness as dh

        names = [n for n in dir(dh) if not n.startswith("_")]
        print(f"  deepseek_harness 导出: {names}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"  ✗ import 就失败了: {type(exc).__name__}: {exc}")

    from deepseek_harness import DeepSeekHarness

    print(f"\n--- 启动(boot)profile={PROFILE} model={MODEL} ---")
    t_boot = time.time()
    try:
        harness = DeepSeekHarness(
            provider="deepseek-official",
            model=MODEL,
            cwd=str(WORKSPACE),
            dsh_home=str(DSH_HOME),
            profile=PROFILE,
        )
        harness.start()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ boot 失败 {type(exc).__name__}: {exc}")
        print("  (第一件事:看 DSH_HOME 下生成了什么)")
        for p in sorted(DSH_HOME.rglob("*"))[:40]:
            print(f"    {p.relative_to(DSH_HOME)}")
        sys.exit(1)
    print(f"  ✓ boot 成功,耗时 {time.time() - t_boot:.1f}s")

    try:
        # 第一轮:纯聊天,不带工具 —— 看最简单的回合长什么样
        _run_one(harness, "用一句话回答:1 加 1 等于几?", "【1】纯对话一轮(观察粒度)")

        # 第二轮:逼它用工具(工作目录里我放了 hello.txt / counter.py)
        _run_one(
            harness,
            "当前工作目录里有哪些文件?请用你能用的工具看一眼再回答,并说明你用了哪个工具。",
            "【2】需要动手的一轮(观察工具有没有 shell)",
        )
    finally:
        harness.close()
        print("\n已关闭。")

    print("\n--- 完成。要看的结论 ---")
    print("  1. 时间线里 t+ 是否**连续多个小间隔** → 有逐字流式;只有一大跳 → 回合级")
    print("  2. 【2】里有没有和 shell/bash/文件 有关的工具事件")
    print("  3. 事件类型里 assistant/message 是不是**整段**到达")


if __name__ == "__main__":
    main()
