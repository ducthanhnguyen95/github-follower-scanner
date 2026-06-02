#!/usr/bin/env python3
"""Quét follower của NHIỀU tài khoản GitHub và xuất email công khai.

GitHub API chỉ trả trường `email` khi người dùng đặt email ở chế độ public.
Hầu hết follower sẽ không có email — kết quả thường thưa.

Tính năng:
    - tự động dùng Search API tìm top N user nhiều follower nhất (mặc định 100)
      mỗi lần chạy, rồi quét follower của tất cả họ; hoặc chỉ định --users
    - quét follower của nhiều owner trong cùng một lần chạy, gộp & dedup email
    - retry tự động khi timeout / lỗi mạng / 403 / 429 (exponential backoff)
    - lưu progress TỪNG BƯỚC (append mỗi profile vào file .jsonl)
    - resume: chạy lại tiếp tục đúng chỗ đã dừng (bỏ qua owner & profile đã xong)
    - xuất đồng thời 2 file: .txt (email cách nhau dấu ',') và .csv (cột no,email)

CẢNH BÁO QUY MÔ:
    Top 100 user nhiều follower nhất có hàng trăm nghìn follower mỗi người.
    Quét hết là bất khả thi trong thời gian ngắn. Dùng --max-per-user và/hoặc
    --limit để giới hạn. Script có thể dừng bất cứ lúc nào và resume sau.

Cách dùng:
    export GITHUB_TOKEN=ghp_...                 # bắt buộc nên có (rate limit)
    python3 github_follower_emails.py                      # auto top 100
    python3 github_follower_emails.py --auto-top 20 --max-per-user 500
    python3 github_follower_emails.py --users torvalds,gaearon
    python3 github_follower_emails.py --fresh             # quét lại từ đầu
    python3 github_follower_emails.py --rebuild-output    # chỉ gen file
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator, TextIO

API_BASE = "https://api.github.com"
DEFAULT_OUTPUT = "github_follower_emails.txt"
DEFAULT_PROGRESS = ".github_follower_emails_progress.jsonl"
DEFAULT_AUTO_TOP = 100
MAX_RETRIES = 6
REQUEST_TIMEOUT = 60


def _headers(token: str | None) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-follower-emails-script",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(url: str, token: str | None) -> tuple[dict | list, dict[str, str]]:
    """GET có retry: timeout / lỗi mạng / 403 / 429 đều thử lại với backoff."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers=_headers(token))
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return json.loads(body), hdrs
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in (403, 429) and attempt < MAX_RETRIES:
                wait = min(2**attempt, 90)
                print(
                    f"  HTTP {exc.code}, retry {attempt}/{MAX_RETRIES} sau {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                last_err = exc
                continue
            raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < MAX_RETRIES:
                wait = min(2**attempt, 90)
                print(
                    f"  Lỗi mạng ({exc}), retry {attempt}/{MAX_RETRIES} sau {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                last_err = exc
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc
    raise RuntimeError(f"Request failed for {url}: {last_err}")


def _wait_for_rate_limit(headers: dict[str, str]) -> None:
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    if remaining is None or reset is None:
        return
    if int(remaining) > 0:
        return
    wait = max(int(reset) - int(time.time()) + 1, 1)
    print(f"Rate limit hết — chờ {wait}s...", file=sys.stderr)
    time.sleep(wait)


def top_users_by_followers(token: str | None, count: int) -> list[str]:
    """Dùng Search API lấy top `count` user nhiều follower nhất (tối đa 100)."""
    count = max(1, min(count, 100))  # search trả tối đa 100/trang
    params = urllib.parse.urlencode(
        {
            "q": "followers:>1000 type:user",
            "sort": "followers",
            "order": "desc",
            "per_page": count,
            "page": 1,
        }
    )
    url = f"{API_BASE}/search/users?{params}"
    data, hdrs = _request(url, token)
    _wait_for_rate_limit(hdrs)
    items = data.get("items", []) if isinstance(data, dict) else []
    return [it["login"] for it in items if it.get("login")]


def load_progress(
    path: Path,
) -> tuple[set[str], list[str], set[str], set[str]]:
    """Đọc progress .jsonl → (processed_logins, emails, seen_emails, owners_done)."""
    processed: set[str] = set()
    emails: list[str] = []
    seen_emails: set[str] = set()
    owners_done: set[str] = set()
    if not path.exists():
        return processed, emails, seen_emails, owners_done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # dòng ghi dở do crash — bỏ qua
            owner_done = rec.get("owner_done")
            if owner_done:
                owners_done.add(owner_done)
                continue
            login = rec.get("login")
            if not login:
                continue
            processed.add(login)
            email = rec.get("email")
            if email and email not in seen_emails:
                seen_emails.add(email)
                emails.append(email)
    return processed, emails, seen_emails, owners_done


def append_progress(handle: TextIO, login: str, email: str | None) -> None:
    """Ghi 1 dòng progress ngay lập tức (từng bước) và flush xuống disk."""
    handle.write(json.dumps({"login": login, "email": email}) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def append_owner_done(handle: TextIO, owner: str) -> None:
    """Đánh dấu 1 owner đã quét xong để resume bỏ qua."""
    handle.write(json.dumps({"owner_done": owner}) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _csv_path_for(txt_path: Path) -> Path:
    return txt_path.with_suffix(".csv")


def write_output(path: Path, emails: list[str]) -> None:
    """Ghi đồng thời 2 định dạng:
    - .txt: tất cả email trên 1 dòng, cách nhau dấu ','
    - .csv: bảng 2 cột (no, email), mỗi email 1 dòng
    """
    path.write_text(",".join(emails), encoding="utf-8")
    csv_path = _csv_path_for(path)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["no", "email"])
        for i, email in enumerate(emails, 1):
            writer.writerow([i, email])


def iter_followers(username: str, token: str | None) -> Iterator[str]:
    page = 1
    while True:
        url = f"{API_BASE}/users/{username}/followers?per_page=100&page={page}"
        data, hdrs = _request(url, token)
        _wait_for_rate_limit(hdrs)
        if not data:
            break
        for item in data:
            yield item["login"]
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)


def fetch_public_email(login: str, token: str | None) -> str | None:
    url = f"{API_BASE}/users/{login}"
    data, hdrs = _request(url, token)
    _wait_for_rate_limit(hdrs)
    email = data.get("email")
    if email and isinstance(email, str) and email.strip():
        return email.strip()
    return None


def scan_owner(
    owner: str,
    token: str | None,
    limit: int | None,
    max_per_user: int | None,
    processed: set[str],
    emails: list[str],
    seen_emails: set[str],
    output_path: Path,
    progress_handle: TextIO,
) -> bool:
    """Quét follower của 1 owner.

    Trả về True nếu owner đã quét xong (hoặc đạt max_per_user) → có thể đánh dấu
    owner_done. Trả về False nếu dừng do đạt --limit toàn cục (chưa xong owner).
    """
    per_user = 0
    for login in iter_followers(owner, token):
        if limit is not None and len(processed) >= limit:
            return False
        if max_per_user is not None and per_user >= max_per_user:
            break  # đủ cho owner này

        if login in processed:
            continue

        email = fetch_public_email(login, token)
        processed.add(login)
        per_user += 1

        append_progress(progress_handle, login, email)

        if email and email not in seen_emails:
            seen_emails.add(email)
            emails.append(email)
            write_output(output_path, emails)
            print(f"  [{len(processed)}] @{owner} ▸ {login}: {email}", file=sys.stderr)
        elif len(processed) % 100 == 0:
            print(
                f"  ... đã quét {len(processed)} profile, {len(emails)} email",
                file=sys.stderr,
            )
        time.sleep(0.1)

    return True


def resolve_owners(args: argparse.Namespace, token: str | None) -> list[str]:
    """Danh sách owner cần quét: --users nếu có, ngược lại auto top N qua Search."""
    if args.users:
        owners = [u.strip() for u in args.users.split(",") if u.strip()]
        print(f"Owner thủ công ({len(owners)}): {', '.join(owners)}", file=sys.stderr)
        return owners

    print(
        f"Tìm top {args.auto_top} user nhiều follower nhất qua Search API...",
        file=sys.stderr,
    )
    owners = top_users_by_followers(token, args.auto_top)
    print(f"Đã lấy {len(owners)} owner: {', '.join(owners[:10])}...", file=sys.stderr)
    return owners


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quét follower của nhiều tài khoản GitHub, xuất email (.txt + .csv)."
    )
    parser.add_argument(
        "--users",
        default=None,
        help="Danh sách username cách nhau dấu ',' (bỏ qua = auto top N qua Search API)",
    )
    parser.add_argument(
        "--auto-top",
        type=int,
        default=DEFAULT_AUTO_TOP,
        help=f"Số user top-follower cần tìm tự động (mặc định {DEFAULT_AUTO_TOP}, tối đa 100)",
    )
    parser.add_argument(
        "--max-per-user",
        type=int,
        default=None,
        help="Giới hạn số follower quét MỖI owner (khuyến nghị đặt khi auto-top)",
    )
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help=f"File .txt (mặc định: {DEFAULT_OUTPUT}; .csv sinh kèm)")
    parser.add_argument("--progress", default=DEFAULT_PROGRESS, help=f"File lưu tiến trình (mặc định: {DEFAULT_PROGRESS})")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn TỔNG số profile quét toàn cục")
    parser.add_argument("--fresh", action="store_true", help="Bỏ progress cũ, quét lại từ đầu")
    parser.add_argument(
        "--rebuild-output",
        action="store_true",
        help="Chỉ tái tạo file email từ progress (không gọi mạng) rồi thoát",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    progress_path = Path(args.progress)

    if args.rebuild_output:
        _, emails, _, _ = load_progress(progress_path)
        write_output(output_path, emails)
        print(
            f"Tái tạo {len(emails)} email → {output_path} & {_csv_path_for(output_path)}",
            file=sys.stderr,
        )
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print(
            "Cảnh báo: chưa set GITHUB_TOKEN — giới hạn 60 request/giờ.\n"
            "  export GITHUB_TOKEN=ghp_...\n",
            file=sys.stderr,
        )

    if args.fresh and progress_path.exists():
        progress_path.unlink()

    processed, emails, seen_emails, owners_done = load_progress(progress_path)
    if processed or owners_done:
        print(
            f"Resume: {len(processed)} profile, {len(emails)} email, "
            f"{len(owners_done)} owner đã xong.",
            file=sys.stderr,
        )
    else:
        write_output(output_path, emails)

    try:
        owners = resolve_owners(args, token)
    except Exception as exc:
        print(f"Không lấy được danh sách owner: {exc}", file=sys.stderr)
        return 1

    if args.users is None and args.max_per_user is None:
        print(
            "LƯU Ý: đang auto top-follower mà KHÔNG đặt --max-per-user — "
            "mỗi owner có thể có hàng trăm nghìn follower. Cân nhắc --max-per-user "
            "hoặc --limit. Có thể Ctrl+C bất cứ lúc nào, progress được lưu từng bước.",
            file=sys.stderr,
        )

    with progress_path.open("a", encoding="utf-8") as progress_handle:
        try:
            for owner in owners:
                if owner in owners_done:
                    print(f"Bỏ qua @{owner} (đã xong).", file=sys.stderr)
                    continue
                if args.limit is not None and len(processed) >= args.limit:
                    print(f"Đạt --limit {args.limit} profile, dừng.", file=sys.stderr)
                    break

                print(f"== Quét follower của @{owner} ==", file=sys.stderr)
                done = scan_owner(
                    owner,
                    token,
                    args.limit,
                    args.max_per_user,
                    processed,
                    emails,
                    seen_emails,
                    output_path,
                    progress_handle,
                )
                if done:
                    append_owner_done(progress_handle, owner)
                    owners_done.add(owner)
                else:
                    break  # đạt --limit toàn cục
        except KeyboardInterrupt:
            write_output(output_path, emails)
            print(
                f"\nĐã dừng. Progress đã lưu ({len(processed)} profile, "
                f"{len(emails)} email). Chạy lại để tiếp tục.",
                file=sys.stderr,
            )
            return 130
        except Exception as exc:
            write_output(output_path, emails)
            print(
                f"\nLỗi: {exc}\nProgress đã lưu ({len(processed)} profile, "
                f"{len(emails)} email). Chạy lại để tiếp tục.",
                file=sys.stderr,
            )
            return 1
        finally:
            write_output(output_path, emails)

    print(
        f"\nHoàn tất: {len(emails)} email từ {len(owners_done)} owner → "
        f"{output_path} & {_csv_path_for(output_path)}",
        file=sys.stderr,
    )
    if not emails:
        print(
            "Không tìm thấy email public nào. "
            "Điều này bình thường — GitHub ẩn email đa số user.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
