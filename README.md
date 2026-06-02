# GitHub Follower Emails

Script `github_follower_emails.py` quét **follower của nhiều tài khoản GitHub** và xuất các **email công khai** ra 2 file: `.txt` (cách nhau dấu `,`) và `.csv` (cột `no,email`).

Mỗi lần chạy, script có thể **tự động dùng GitHub Search API** để tìm top N user nhiều follower nhất rồi quét follower của họ, hoặc bạn chỉ định danh sách user thủ công.

Để quét nhanh hơn, script lấy email qua **GraphQL theo lô** — gộp tới 50 user trong **1 request** thay vì 1 request/user như REST, nhanh hơn hàng chục lần. Nếu không có token, script tự lùi về REST từng user.

---

## ⚠️ Lưu ý quan trọng

- **GitHub ẩn email của hầu hết user.** Trường `email` chỉ trả về khi user **tự bật public** trên profile → kết quả thường **thưa** (phần lớn không có email).
- **Lấy email dùng GraphQL (cần token).** GraphQL API **bắt buộc có token**; không có token script vẫn chạy nhưng lùi về REST từng user (chậm, 60 req/giờ).
- **Quy mô top-follower rất lớn.** Top 100 user nhiều follower nhất có **100k–200k+ follower mỗi người**. Quét hết là **bất khả thi** trong thời gian ngắn (token GitHub giới hạn ~5.000 request/giờ). **Luôn dùng `--max-per-user` và/hoặc `--limit`.**
- Chỉ thu thập **dữ liệu công khai**. Cân nhắc quyền riêng tư và điều khoản dịch vụ của GitHub trước khi dùng email cho mục đích liên hệ.

---

## 1. Yêu cầu

- Python 3 (không cần cài thêm package — chỉ dùng thư viện chuẩn).
- (Khuyến nghị) **GitHub Personal Access Token** để nâng giới hạn request.

### Tạo token

GitHub → **Settings** → **Developer settings** → **Personal access tokens** → tạo token (classic không cần scope cũng đủ cho dữ liệu public; hoặc fine-grained với quyền `read:user`).

### Set token

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx
```

| Có token | Không token |
|----------|-------------|
| ~5.000 request/giờ | chỉ **60** request/giờ |
| Search: 30 req/phút | Search: 10 req/phút |
| Email: GraphQL theo lô (50 user/request) | REST từng user (1 request/user) |

---

## 2. Chạy nhanh

```bash
# Auto tìm top 100 user nhiều follower nhất, mỗi owner lấy tối đa 500 follower
python3 github_follower_emails.py --max-per-user 500
```

Kết quả mặc định:

- `github_follower_emails.txt` — `email1,email2,email3,...`
- `github_follower_emails.csv` — bảng `no,email`

---

## 3. Các cách dùng

### Auto top-follower (mặc định)

```bash
# Top 100 (mặc định) — NÊN giới hạn để khả thi
python3 github_follower_emails.py --max-per-user 500

# Top 20, mỗi owner tối đa 1000 follower
python3 github_follower_emails.py --auto-top 20 --max-per-user 1000
```

### Chỉ định owner thủ công

```bash
python3 github_follower_emails.py --users torvalds,gaearon,eddiejaoude
```

### Test nhanh

```bash
# Giới hạn tổng 50 profile toàn cục
python3 github_follower_emails.py --users eddiejaoude --limit 50
```

### Gen lại file từ progress (không gọi mạng)

```bash
python3 github_follower_emails.py --rebuild-output
```

### Quét lại từ đầu

```bash
python3 github_follower_emails.py --fresh
```

---

## 4. Tham số

| Tham số | Mặc định | Mô tả |
|---------|----------|-------|
| `--users` | _(none)_ | Danh sách username cách nhau dấu `,`. Bỏ qua = auto top N qua Search API |
| `--auto-top` | `100` | Số user top-follower cần tìm tự động (tối đa 100) |
| `--max-per-user` | _(none)_ | Giới hạn số follower quét **mỗi owner** (khuyến nghị khi auto-top) |
| `--limit` | _(none)_ | Giới hạn **tổng** số profile quét toàn cục |
| `-o`, `--output` | `github_follower_emails.txt` | File `.txt` (file `.csv` sinh kèm cùng tên) |
| `--progress` | `.github_follower_emails_progress.jsonl` | File lưu tiến trình |
| `--fresh` | `false` | Xoá progress cũ, quét lại từ đầu |
| `--rebuild-output` | `false` | Chỉ tái tạo `.txt` + `.csv` từ progress rồi thoát |

---

## 5. File kết quả

### `.txt`

```
a@example.com,b@example.com,c@example.com
```

### `.csv`

```csv
no,email
1,a@example.com
2,b@example.com
3,c@example.com
```

> Email được **dedup toàn cục** giữa tất cả owner.

---

## 6. Resume, retry & an toàn dữ liệu

Script được thiết kế để chạy lâu và an toàn khi bị gián đoạn:

- **Lưu progress từng bước** — mỗi profile được ghi ngay (append + `fsync`) vào file `.jsonl`.
- **Resume tự động** — chạy lại sẽ bỏ qua owner đã xong (`owner_done`) và profile đã quét.
- **Retry** — timeout / lỗi mạng / HTTP 403 / 429 được thử lại với backoff lũy thừa (tối đa 6 lần).
- **Luôn ghi file kết quả** — `.txt` + `.csv` được cập nhật mỗi khi có email mới, và trong khối `finally` khi thoát (kể cả timeout, lỗi, hay `Ctrl+C`).
- **Dừng bất cứ lúc nào** — nhấn `Ctrl+C`; lần chạy sau tiếp tục đúng chỗ.

Nếu tiến trình bị **kill cứng** (`kill -9`) khiến file kết quả chưa kịp ghi, tái tạo lại từ progress:

```bash
python3 github_follower_emails.py --rebuild-output
```

> Khi một owner đạt `--max-per-user`, owner đó được đánh dấu **đã xong**. Muốn quét sâu hơn cho cùng owner sau này, dùng `--fresh` hoặc xoá dòng `owner_done` tương ứng trong file progress.

---

## 7. Ví dụ output khi chạy

```
Tìm top 20 user nhiều follower nhất qua Search API...
Đã lấy 20 owner: torvalds, karpathy, gustavoguanabara, claude, yyx990803...
== Quét follower của @torvalds ==
  [12] @torvalds ▸ someuser: someuser@example.com
  ... đã quét 100 profile, 7 email
== Quét follower của @karpathy ==
  ...
Hoàn tất: 42 email từ 5 owner → github_follower_emails.txt & github_follower_emails.csv
```
