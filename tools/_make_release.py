"""创建 GitHub Release v1.0.0 并上传 Python 安装器附件。"""
import json
import subprocess
import sys
import time
import urllib.request

REPO = "yaohui686/Movie-Search"
ASSET = r"K:\DeepSeek聊天区域\_release_assets\python-3.13.9-amd64.exe"
ASSET_NAME = "python-3.13.9-amd64.exe"


def gcm_token() -> str:
    inp = f"protocol=https\nhost=github.com\n\n".encode()
    out = subprocess.run(["git", "credential", "fill"], input=inp,
                         capture_output=True, check=True).stdout.decode()
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("no token from credential manager")


def api_json(url: str, token: str, payload: dict | None = None,
             method: str = "POST", tries: int = 4) -> dict:
    body = json.dumps(payload).encode() if payload else None
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "release-script",
    }
    if body:
        hdrs["Content-Type"] = "application/json"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs,
                                         method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as exc:
            last = exc
            print(f"  尝试 {i+1}/{tries} 失败: {exc}")
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"API 调用失败: {last}")


def main() -> None:
    token = gcm_token()
    print("token 获取成功")

    rel = api_json(f"https://api.github.com/repos/{REPO}/releases", token, {
        "tag_name": "v1.0.0",
        "name": "v1.0.0 首个正式版",
        "body": (
            "## 使用\n\n"
            "1. 没装 Python？先下载下面的 `python-3.13.9-amd64.exe` 安装（第一屏勾选 "
            "**Add python.exe to PATH**）\n"
            "2. 点 Code → Download ZIP，解压后双击 `影视检索.cmd`\n\n"
            "更新见 README。仅用于个人学习与技术交流，请支持正版。\n"
        ),
        "draft": False,
        "prerelease": False,
    })
    print(f"Release 创建成功 id={rel['id']} url={rel['html_url']}")

    upload_url = rel["upload_url"].split("{")[0]
    data = open(ASSET, "rb").read()
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "User-Agent": "release-script",
    }
    url = f"{upload_url}?name={ASSET_NAME}"
    for i in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs,
                                         method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
                asset = json.loads(r.read())
            print(f"附件上传成功: {asset['name']} "
                  f"{asset['size']/1048576:.1f}MB 状态={asset['state']}")
            print("下载直链:")
            print(f"  {asset['browser_download_url']}")
            break
        except Exception as exc:
            print(f"  附件尝试 {i+1}/4 失败: {exc}")
            time.sleep(5 * (i + 1))
    else:
        sys.exit("附件上传失败")


if __name__ == "__main__":
    main()
