#!/usr/bin/env python3
"""
baidu_cli.py
Liệt kê toàn bộ tệp & thư mục trong một link chia sẻ pan.baidu.com
và xuất ra .txt (dạng cây) hoặc .json (đã làm đẹp).

Cài đặt:   pip install requests
Ví dụ:
    python baidu_cli.py "https://pan.baidu.com/s/1z31pFzxq7YVp5KvSUupV7Q?pwd=14t9"
    python baidu_cli.py LINK -f json -o ketqua.json
    python baidu_cli.py LINK -p 14t9 -f both
    python baidu_cli.py            # chạy chế độ hỏi đáp
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

import requests

BASE = "https://pan.baidu.com"
APP_ID = "250528"
PAGE_SIZE = 100
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

ERRNO_MSG = {
    -12: "Sai mã trích xuất (pwd).",
    -9: "Sai mã trích xuất hoặc phiên xác thực hết hạn.",
    -62: "Baidu yêu cầu captcha (thử sai/quá nhiều lần). Hãy thử lại sau hoặc dùng --cookie.",
    -130: "Link bị hạn chế truy cập.",
    2: "Tham số không hợp lệ.",
    105: "Link không hợp lệ.",
    115: "Tệp bị cấm chia sẻ.",
    145: "Link chia sẻ đã hết hạn hoặc bị huỷ.",
    200025: "Cần nhập mã trích xuất.",
    200026: "Mã trích xuất sai.",
}


class BaiduError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Phân tích link
# --------------------------------------------------------------------------- #
def parse_link(link: str, pwd: str | None):
    """Trả về (surl_đầy_đủ_có_số_1, surl_không_có_số_1, pwd, thư_mục_con|None).

    Thư mục con lấy từ phần fragment của link, ví dụ:
    ...?pwd=xxxx#list/path=%2Fsharelink123-456%2F02.Cadence&parentPath=...
    """
    u = urlparse(link.strip())
    qs = parse_qs(u.query)
    pwd = pwd or (qs.get("pwd") or [None])[0]

    sub_path = None
    m_path = re.search(r"(?:^|[/&?])path=([^&]*)", u.fragment)
    if m_path:
        sub_path = unquote(m_path.group(1)) or None

    m = re.search(r"/s/(1[\w-]+)", u.path)
    if m:
        full = m.group(1)
    elif qs.get("surl"):  # dạng cũ: /share/init?surl=xxxx
        full = "1" + qs["surl"][0]
    else:
        raise BaiduError("Không nhận ra định dạng link pan.baidu.com.")
    return full, full[1:], pwd, sub_path


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class BaiduShare:
    def __init__(self, full, surl, pwd, cookie=None, delay=0.4, verbose=True):
        self.full, self.surl, self.pwd = full, surl, pwd
        self.delay, self.verbose = delay, verbose
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": f"{BASE}/s/{full}"})
        if cookie:
            for part in cookie.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    self.s.cookies.set(k, v, domain=".baidu.com")
        self.n_files = self.n_dirs = 0
        self.stop_flag = False   # GUI đặt True để huỷ
        self.max_depth = None    # None/0 = không giới hạn
        self.log_cb = None       # GUI nhận log qua callback

    def log(self, msg):
        if self.log_cb:
            self.log_cb(msg)
        elif self.verbose:
            print(msg, file=sys.stderr, flush=True)

    # -- gọi API và kiểm tra lỗi -------------------------------------------- #
    def _json(self, resp):
        try:
            return resp.json()
        except ValueError:
            raise BaiduError(
                f"Phản hồi không phải JSON (HTTP {resp.status_code}). "
                "Có thể bị chặn IP/anti-bot, thử lại sau hoặc dùng --cookie."
            )

    @staticmethod
    def _check(j):
        errno = j.get("errno", 0)
        if errno != 0:
            raise BaiduError(f"errno={errno}: {ERRNO_MSG.get(errno, 'Lỗi không xác định.')}")

    # -- xác thực mã trích xuất -------------------------------------------- #
    def verify(self):
        self.s.get(f"{BASE}/s/{self.full}", timeout=30)  # lấy cookie khởi tạo
        if not self.pwd:
            self.log("Không có mã trích xuất, bỏ qua bước verify.")
            return
        params = {
            "surl": self.surl,
            "t": int(time.time() * 1000),
            "channel": "chunlei",
            "web": 1,
            "app_id": APP_ID,
            "bdstoken": "",
            "logid": "",
            "clienttype": 0,
        }
        r = self.s.post(
            f"{BASE}/share/verify",
            params=params,
            data={"pwd": self.pwd, "vcode": "", "vcode_str": ""},
            timeout=30,
        )
        j = self._json(r)
        self._check(j)
        randsk = j.get("randsk")
        if not randsk:
            raise BaiduError("Verify thành công nhưng không nhận được randsk.")
        self.s.cookies.set("BDCLND", randsk, domain=".baidu.com")
        self.log("Xác thực mã trích xuất: OK")

    # -- liệt kê một thư mục (tự phân trang) -------------------------------- #
    def list_dir(self, path=None):
        items, page = [], 1
        while True:
            params = {
                "web": 1,
                "app_id": APP_ID,
                "channel": "chunlei",
                "clienttype": 0,
                "desc": 0,
                "showempty": 0,
                "page": page,
                "num": PAGE_SIZE,
                "order": "name",
                "shorturl": self.surl,
            }
            if path is None:
                params["root"] = 1
            else:
                params["dir"] = path

            last_err = None
            for attempt in range(3):
                try:
                    r = self.s.get(f"{BASE}/share/list", params=params, timeout=30)
                    j = self._json(r)
                    self._check(j)
                    break
                except (requests.RequestException, BaiduError) as e:
                    last_err = e
                    if isinstance(e, BaiduError) and "errno=" in str(e):
                        raise
                    time.sleep(1.5 * (attempt + 1))
            else:
                raise BaiduError(f"Thất bại sau 3 lần thử: {last_err}")

            lst = j.get("list") or []
            items.extend(lst)
            if len(lst) < PAGE_SIZE:
                break
            page += 1
            time.sleep(self.delay)
        return items

    # -- xác định thư mục bắt đầu quét -------------------------------------- #
    def resolve_start(self, sub_path):
        """Trả về (đường_dẫn_thật_trên_Baidu, đường_dẫn_tương_đối_so_với_gốc_share).

        sub_path có thể là:
          - đường dẫn thật:  /sharelink872451385-446348911506688/02.Cadence
          - đường dẫn tương đối: 02.Cadence  hoặc  /02.Cadence  hoặc  A/B
        """
        if not sub_path:
            return None, ""
        sub_path = sub_path.strip()
        m = re.match(r"^(/sharelink[^/]+)(/.*)?$", sub_path)
        if m:  # đã là đường dẫn thật
            return sub_path.rstrip("/"), (m.group(2) or "").rstrip("/")

        # đường dẫn tương đối -> ghép với tiền tố gốc của share
        rel = "/" + sub_path.strip("/")
        root_items = self.list_dir(None)
        if not root_items:
            raise BaiduError("Không đọc được thư mục gốc của link để xác định đường dẫn.")
        prefix = root_items[0]["path"].rsplit("/", 1)[0]
        return prefix + rel, rel

    # -- duyệt đệ quy ------------------------------------------------------- #
    def crawl(self, real_path=None, rel_path="", depth=0):
        if self.stop_flag:
            raise BaiduError("Đã huỷ bởi người dùng.")
        nodes = []
        for it in self.list_dir(real_path):
            if self.stop_flag:
                raise BaiduError("Đã huỷ bởi người dùng.")
            name = it.get("server_filename") or it.get("path", "").rsplit("/", 1)[-1]
            is_dir = str(it.get("isdir", 0)) == "1"
            rel = f"{rel_path}/{name}"
            mtime = it.get("server_mtime")
            node = {
                "name": name,
                "path": rel,
                "type": "folder" if is_dir else "file",
                "size": 0 if is_dir else int(it.get("size", 0) or 0),
                "modified": (
                    datetime.fromtimestamp(int(mtime)).strftime("%Y-%m-%d %H:%M:%S")
                    if mtime
                    else None
                ),
                "fs_id": str(it.get("fs_id", "")),
            }
            if is_dir:
                self.n_dirs += 1
                self.log(f"  [DIR ] {rel}")
                if self.max_depth and depth + 1 >= self.max_depth:
                    node["children"] = []
                    node["truncated"] = True  # chưa quét sâu hơn
                else:
                    time.sleep(self.delay)
                    node["children"] = self.crawl(it["path"], rel, depth + 1)
                    node["size"] = sum(c["size"] for c in node["children"])
                    node["item_count"] = len(node["children"])
            else:
                self.n_files += 1
            nodes.append(node)
        return nodes


# --------------------------------------------------------------------------- #
# Xuất kết quả
# --------------------------------------------------------------------------- #
def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024


def render_tree(nodes, prefix="") -> list[str]:
    lines = []
    for i, n in enumerate(nodes):
        last = i == len(nodes) - 1
        branch = "└── " if last else "├── "
        if n["type"] == "folder":
            lines.append(f"{prefix}{branch}📁 {n['name']}/  ({human_size(n['size'])})")
            lines += render_tree(n["children"], prefix + ("    " if last else "│   "))
        else:
            lines.append(f"{prefix}{branch}{n['name']}  ({human_size(n['size'])})")
    return lines


def build_report(link, surl, tree, n_files, n_dirs, start_path=""):
    return {
        "source": link,
        "short_url": "1" + surl,
        "start_path": start_path or "/",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": n_files,
        "total_folders": n_dirs,
        "total_size": sum(n["size"] for n in tree),
        "total_size_human": human_size(sum(n["size"] for n in tree)),
        "items": tree,
    }


def write_json(report, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_txt(report, path):
    head = [
        f"Nguồn        : {report['source']}",
        f"Thư mục quét : {report['start_path']}",
        f"Xuất lúc     : {report['exported_at']}",
        f"Số tệp       : {report['total_files']}",
        f"Số thư mục   : {report['total_folders']}",
        f"Tổng dung lượng: {report['total_size_human']}",
        "=" * 60,
        ".",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(head + render_tree(report["items"])) + "\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Liệt kê tệp/thư mục của link chia sẻ Baidu Pan.")
    ap.add_argument("link", nargs="?", help="Link pan.baidu.com/s/... (có thể kèm ?pwd=xxxx)")
    ap.add_argument("-p", "--pwd", help="Mã trích xuất (nếu link chưa có ?pwd=)")
    ap.add_argument("-f", "--format", choices=["txt", "json", "both"], default=None,
                    help="Định dạng xuất (mặc định: suy ra từ -o, nếu không thì json)")
    ap.add_argument("-o", "--output", help="Tên file xuất (đuôi .txt/.json)")
    ap.add_argument("-d", "--path", help="Chỉ quét 1 thư mục con. Có thể là đường dẫn tương đối "
                    "(vd: 02.Cadence hoặc A/B) hoặc đường dẫn thật /sharelinkXXX/... "
                    "(mặc định: tự lấy từ phần #list/path=... của link nếu có)")
    ap.add_argument("--depth", type=int, default=0,
                    help="Độ sâu tối đa (0 = không giới hạn, 1 = chỉ cấp đầu tiên)")
    ap.add_argument("--cookie", help="Chuỗi cookie Baidu (tuỳ chọn, ví dụ BDUSS=...; STOKEN=...)")
    ap.add_argument("--delay", type=float, default=0.4, help="Giây nghỉ giữa các request (mặc định 0.4)")
    ap.add_argument("-q", "--quiet", action="store_true", help="Không in tiến trình")
    args = ap.parse_args()

    link = args.link or input("Nhập link Baidu Pan: ").strip()
    try:
        full, surl, pwd, sub = parse_link(link, args.pwd)
        sub = args.path or sub  # --path ưu tiên hơn phần #list/path= trong link
        if not pwd and not args.link:
            pwd = input("Nhập mã trích xuất (Enter nếu không có): ").strip() or None
        if not sub and not args.link:
            sub = input("Chỉ quét thư mục con? Nhập đường dẫn (Enter = quét toàn bộ): ").strip() or None
        if not args.format and not args.output and not args.link:
            args.format = (input("Xuất định dạng [json/txt/both] (mặc định json): ").strip() or "json")

        client = BaiduShare(full, surl, pwd, cookie=args.cookie,
                            delay=args.delay, verbose=not args.quiet)
        client.max_depth = args.depth or None
        client.verify()
        real_start, rel_start = client.resolve_start(sub)
        client.log(f"Đang quét: {rel_start or '/ (toàn bộ link)'}")
        tree = client.crawl(real_start, rel_start)
    except (BaiduError, requests.RequestException) as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        sys.exit(1)

    report = build_report(link, surl, tree, client.n_files, client.n_dirs, rel_start)

    # Xác định định dạng & tên file
    fmt = args.format
    base = args.output
    if base and not fmt:
        fmt = "txt" if base.lower().endswith(".txt") else "json"
    fmt = fmt or "json"
    if base:
        stem = re.sub(r"\.(txt|json)$", "", base, flags=re.I)
    else:
        stem = f"baidu_{surl}"
        if rel_start:
            tail = re.sub(r'[\\/:*?"<>|\s]+', "_", rel_start.strip("/").replace("/", "_"))
            stem += f"_{tail}"

    outs = []
    if fmt in ("json", "both"):
        write_json(report, stem + ".json")
        outs.append(stem + ".json")
    if fmt in ("txt", "both"):
        write_txt(report, stem + ".txt")
        outs.append(stem + ".txt")

    print(f"\nXong: {client.n_files} tệp, {client.n_dirs} thư mục, "
          f"tổng {report['total_size_human']}")
    for o in outs:
        print(f"→ Đã ghi: {o}")


if __name__ == "__main__":
    main()