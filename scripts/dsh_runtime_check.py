"""确认这个平台上 dsh 的运行时**真的能起来**。不需要 API key,不打上游。

两种用法:

  1. **构建镜像时跑一次**(见 Dockerfile)。这是这条命令存在的**主要**理由:
     `deepseek-harness-sdk` 拉的 `deepseek-harness-runtime-bin` 是**按平台**分发的
     轮子(macos / manylinux_2_28 / win,各自 x86_64 或 aarch64)。架构对不上、
     或基础镜像的 glibc 低于 2.28 时,pip 装得下来,但可执行体跑不起来 ——
     而那个错误**不会在构建时出现**:它要等到第一个用户提问、第一次
     `initialize()` 才炸。那时候排查成本高得多(还得先想到「是镜像的问题」)。
     在构建时跑一次,这类问题就变成「镜像构建失败」,日志里直接有原因。

  2. **线上排查**:`docker exec -it <容器> python scripts/dsh_runtime_check.py`。
     它会把运行时路径、版本、自举出来的 profile 都打出来。

**这个脚本不需要 DEEPSEEK_API_KEY**:`initialize()` 只建 agent,不调上游。
所以它能在构建阶段跑(那时还没有任何密钥)。

退出码:0 = 能起来;非 0 = 起不来,原因打在 stderr。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    print(f"python      : {sys.executable} ({sys.version.split()[0]})")
    print(f"platform    : {sys.platform}")

    try:
        import deepseek_harness  # noqa: F401 —— 只要 import 得动
    except ImportError as exc:
        print(f"✗ 没装 deepseek-harness-sdk:{exc}", file=sys.stderr)
        print("  修:pip install deepseek-harness-sdk", file=sys.stderr)
        return 1
    # `deepseek_harness` 没有 `__version__`,版本要从包元数据里读。
    from importlib.metadata import version as _dist_version

    for dist in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
        try:
            print(f"{dist:12s}: {_dist_version(dist)}")
        except Exception:  # noqa: BLE001 —— 版本号读不到不该让检查失败
            print(f"{dist:12s}: (读不到版本)")

    # 运行时本体(那个单文件 Node 可执行体)。取不到就说明平台轮子没装上。
    try:
        from deepseek_harness_runtime import bundled_runtime_path

        binary = bundled_runtime_path()
    except Exception as exc:  # noqa: BLE001 —— 这里就是要把它变成一句人话
        print(f"✗ 找不到这个平台的 dsh 运行时:{exc}", file=sys.stderr)
        print(
            "  多半是这个架构没有对应的轮子,或 glibc < 2.28"
            "(基础镜像要用 debian 系,不能用 alpine)。",
            file=sys.stderr,
        )
        return 1

    size_mb = Path(binary).stat().st_size / 1e6
    print(f"runtime     : {binary} ({size_mb:.0f} MB)")

    # 用一个**临时**的 dsh_home:构建阶段不该在镜像里留下一次探测的痕迹。
    with tempfile.TemporaryDirectory(prefix="dsh-check-") as home:
        workspace = Path(home) / "workspace"
        workspace.mkdir()
        started = time.monotonic()
        try:
            from deepseek_harness import DeepSeekHarness

            harness = DeepSeekHarness(
                provider="deepseek-official",
                model=os.getenv("AI_MODEL", "deepseek-flash"),
                cwd=str(workspace),
                dsh_home=home,
                profile=os.getenv("DSH_PROFILE", "sdk-minimal"),
                # 不给 key:initialize 不打上游,给了反而容易让人以为它在联网。
                initialize_timeout_seconds=180,
            )
            harness.start()
            harness.close()
        except Exception as exc:  # noqa: BLE001
            print(f"✗ dsh 起不来:{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        elapsed = time.monotonic() - started
        # 自举出来的东西:干净目录能长出 profile 是我们依赖的性质
        # (镜像里的 .dsh 是空的,gitignore 保证它不会被拷进来)。
        profile = Path(home) / "profiles" / os.getenv("DSH_PROFILE", "sdk-minimal")
        print(f"bootstrap   : {elapsed:.1f}s,profile 自举{'成功' if profile.is_dir() else '**失败**'}")
        if not profile.is_dir():
            print("✗ 干净 dsh_home 没能自举出 profile", file=sys.stderr)
            return 1

    print("✓ dsh 运行时可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
