"""
launch.py - one-click launcher for the stock analysis app
"""
import subprocess
import webbrowser
import threading
import socket
import time
import sys
import os
import urllib.request
import urllib.error

os.chdir(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_PORT = 8501
PORT_RANGE = 20


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port():
    for p in range(DEFAULT_PORT, DEFAULT_PORT + PORT_RANGE):
        if _port_free(p):
            return p
    msg = "端口 {}-{} 全部被占用。请关掉占用进程后再试。"
    print(msg.format(DEFAULT_PORT, DEFAULT_PORT + PORT_RANGE - 1))
    input("按回车退出...")
    sys.exit(1)


def wait_and_open(url):
    """等 Streamlit 就绪后打开浏览器；绕过代理访问 localhost"""
    # 构建不走代理的 opener
    no_proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(no_proxy_handler)

    opened = False
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            r = opener.open(url, timeout=2)
            if r.status == 200:
                webbrowser.open(url)
                opened = True
                return
        except Exception:
            time.sleep(0.5)

    # 保底：即使检测不到也强制打开浏览器，也许服务已经就绪了
    if not opened:
        print("健康检查超时，尝试强制打开浏览器...")
        webbrowser.open(url)


def check_dependencies():
    missing = []
    for pkg in ["streamlit", "yfinance", "pandas", "numpy", "plotly", "ta", "akshare"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("")
        print("缺少依赖包: " + ", ".join(missing))
        print("请运行: pip install " + " ".join(missing))
        print("或者: pip install -r requirements.txt")
        input("按回车退出...")
        sys.exit(1)


def main():
    print("Python " + sys.version)
    check_dependencies()

    port = pick_port()
    url = "http://localhost:" + str(port)
    if port != DEFAULT_PORT:
        print("默认端口被占用，已切换到 " + str(port))
    print("启动股票分析模型，稍候自动打开 " + url + " ...")

    threading.Thread(target=wait_and_open, args=(url,), daemon=True).start()

    env = os.environ.copy()
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 确保 localhost 不走代理
    no_proxy = env.get("NO_PROXY", "")
    if "localhost" not in no_proxy:
        env["NO_PROXY"] = (no_proxy + ",localhost,127.0.0.1").strip(",")
        env["no_proxy"] = env["NO_PROXY"]

    port_str = str(port)
    cmd = [sys.executable, "-m", "streamlit", "run", "app.py"]
    cmd += ["--server.port", port_str]
    cmd += ["--server.headless", "true"]
    cmd += ["--browser.gatherUsageStats", "false"]

    try:
        proc = subprocess.run(cmd, env=env)
    except FileNotFoundError:
        print("")
        print("找不到 streamlit。请先运行: pip install -r requirements.txt")
        input("按回车退出...")
        sys.exit(1)
    except KeyboardInterrupt:
        return

    if proc.returncode != 0:
        print("")
        print("streamlit 异常退出，返回码 " + str(proc.returncode) + "。请检查上方报错。")
        input("按回车退出...")
        sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
