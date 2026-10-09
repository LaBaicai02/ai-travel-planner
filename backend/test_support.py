"""Fail fast when the execution environment blocks Windows asyncio's loopback."""
import subprocess
import sys


def check_local_socketpair():
    try:
        subprocess.run(
            [sys.executable, "-B", "-c", "import socket; a,b=socket.socketpair(); a.close(); b.close()"],
            check=True, capture_output=True, timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Python socketpair 本地回环预检超过 5 秒；测试尚未进入业务代码。"
            "请在允许本地回环连接的执行环境或外部 PowerShell 中运行测试。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Python socketpair 本地回环预检失败：" + exc.stderr.decode(errors="replace")) from exc
