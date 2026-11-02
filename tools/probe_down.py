"""检查各资源站 vod_down_url 下载直链的覆盖情况。

加新源后跑一遍，看新源给不给整文件直链：
    python tools\\probe_down.py [片名]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from filmsearch.http import get_json  # noqa: E402

CFG = ROOT / "cfg" / "app.json"


def main() -> None:
    keyword = " ".join(sys.argv[1:]) or "泰坦尼克号"
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    ok = 0
    for src in cfg["resource_sources"]:
        api = src["api"].rstrip("/")
        try:
            data = get_json(
                api + "/api.php/provide/vod/?ac=detail&wd=" + keyword,
                timeout=8, retries=0,
            )
            lst = data.get("list") or []
        except Exception as exc:
            print(f"{src['name']:<6} FAIL {type(exc).__name__}")
            continue
        if not lst:
            print(f"{src['name']:<6} 无命中")
            continue
        for it in lst[:2]:
            df = it.get("vod_down_from") or ""
            du = it.get("vod_down_url") or ""
            items = [x for x in du.split("#") if x] if du else []
            first = items[0] if items else ""
            if items:
                ok += 1
            print(f"{src['name']:<6} {it.get('vod_name')}  "
                  f"down_from={df!r}  down条数={len(items)}")
            print(f"   样例: {first[:150]}")
    print(f"\n带直链的条目：{ok}")


if __name__ == "__main__":
    main()
