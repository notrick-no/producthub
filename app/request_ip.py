"""从请求里取客户端 IP(登录审计用)。

线上请求是经 Railway 的代理转发进来的,所以直连地址(`request.client.host`)是**代理的**
内网地址,真实地址在 `X-Forwarded-For` 里。这个头是个逗号分隔的链,每一跳往后面追加:

    X-Forwarded-For: <客户端自己填的>, <第一跳代理>, <最后一跳代理追加的真实地址>

这里**取最右一个**。原因是左边那些是客户端随手就能伪造的 —— 而 uvicorn 自带的
`--proxy-headers --forwarded-allow-ips=*` 取的正是最左那个(见
`uvicorn/middleware/proxy_headers.py` 的 `get_trusted_client_address`,always_trust 分支
直接返回 `x_forwarded_for_hosts[0]`)。对一份审计记录来说,记一个客户端能自己决定的
值等于没记。最右边那个由最后一跳追加,客户端改不了 —— 前提是服务只能经由那一跳访问,
线上成立。

**因此也不启用 uvicorn 的 `--proxy-headers`**:它只在「直连对端」命中 trusted_hosts 时
才生效,默认是 127.0.0.1,而容器里对端是代理,**默认配置等于什么都没做**;放开成 `*`
又会退回上面那个取最左的可伪造值。既然要自己解析,就不引那个中间件了。
"""

IP_MAX_LEN = 45  # IPv6 字面量上限,与 models.LoginEvent.ip 的列宽一致


def _strip_port(value: str) -> str:
    """去掉地址后面可选的端口。

    IPv6 字面量带端口必须写成 `[::1]:1234` —— 方括号不是装饰,没有它冒号就分不清
    是地址的一部分还是端口分隔符。所以先认方括号;只有**恰好一个冒号**时才按端口切
    (裸 IPv4 / 主机名的情况),其余原样返回 —— 直接 `split(":")` 会把裸 IPv6 切碎。
    """
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host if port.isdigit() else value
    return value


def client_ip(request) -> str | None:
    """本次请求的客户端 IP;取不到就 None(审计记录里留空,不编一个)。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # 从右往左找第一个非空项:空的尾巴(代理写了 "1.2.3.4, ")不该赢过真实值
        for part in reversed(forwarded.split(",")):
            ip = _strip_port(part)
            if ip:
                return ip[:IP_MAX_LEN]
    # 本机开发(Vite 直连 8000)没有代理,也就没有 XFF,走这里
    return request.client.host if request.client else None
