# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                   |
| --------------- | -------------------------- |
| Họ và tên       | Đinh Lê Quỳnh Phương       |
| MSSV            | 2A202601865                |
| Khóa/Lớp        | K3                         |
| Vai trò chính   | P4 — Payment Agent         |
| Ngày hoàn thành | 2026-08-05                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | --------------- | ---------- |
| Payment Agent | `src/agents/payment_agent.py` / `analyze()` | `InputCase` (claimed_order_id), `OlistDataLoader` | `PaymentEvidence` (payment_total_brl, item_total_brl, freight_total_brl, valid_split_payment, payment_ids, evidence_ids) | Hoàn thành |

Chỉ nhận ownership cho phần bạn trực tiếp thực hiện. Liên hệ rõ phần việc của bạn với đầu vào, đầu ra và các thành viên phụ thuộc vào phần đó.

Module P4 (`payment_agent.py`) nhận đầu vào từ Coordinator (P1) song song với P2 và P3. Output `PaymentEvidence` được P1 gộp vào `EvidenceBundle` để chuyển tiếp cho Policy Agent (P5). Verifier Agent (P6) dùng `payment_ids` và `evidence_ids` để kiểm tra tính hợp lệ.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Đọc `schemas.py` và `coordinator.py` để hiểu contract CP0, đảm bảo interface `analyze()` khớp với những gì Coordinator expect | P1 (Coordinator) | Payment Agent tích hợp không cần sửa phía Coordinator |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Implement `analyze(case, loader)` đọc payment và item rows, tính tổng tiền bằng `Decimal`, phát hiện `valid_split_payment` | `src/agents/payment_agent.py` | `PaymentEvidence` dataclass với đầy đủ 7 fields theo contract | `python -m src.agents.payment_agent b81ef226f3fe1789b1e8b2acac839d17` → JSON output đúng |
| Build `payment_ids` (format `<order_id>:<seq>`) và `evidence_ids` (format `payment:<order_id>:<seq>`) đúng theo README mục 5 | `src/agents/payment_agent.py` | Các ID có thể tra ngược CSV, pass Verifier | Kiểm tra bằng mắt: ID khớp đúng với dữ liệu trong `olist_order_payments_dataset.csv` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

Chạy `python -m src.agents.payment_agent b81ef226f3fe1789b1e8b2acac839d17` trả về:

```json
{
  "order_id": "b81ef226f3fe1789b1e8b2acac839d17",
  "payment_total_brl": 99.33,
  "item_total_brl": 79.8,
  "freight_total_brl": 19.53,
  "valid_split_payment": false,
  "payment_evidence_ids": ["payment:b81ef226f3fe1789b1e8b2acac839d17:1"]
}
```

Số liệu khớp với dữ liệu thô trong CSV (`payment_value=99.33`, `price=79.8`, `freight_value=19.53`), xác nhận logic tính toán đúng.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Payment Agent giải quyết bài toán **đối soát tài chính**: với một `order_id`, hệ thống cần biết (1) khách hàng đã thanh toán bao nhiêu, (2) giá trị thực tế của đơn hàng (item + freight) là bao nhiêu, và (3) nếu có nhiều phương thức thanh toán thì tổng có khớp không. Đây là dữ liệu tài chính quan trọng để Policy Agent xác định có hoàn tiền không và hoàn bao nhiêu.

### Cách triển khai

Agent hoạt động hoàn toàn **deterministic** (không dùng LLM) vì bài toán là phép tính số học thuần túy:

1. **Đọc dữ liệu qua `OlistDataLoader`** (không đọc CSV trực tiếp): `loader.order_payments(order_id)` và `loader.order_items(order_id)` trả về tuple các row đã được index sẵn từ lúc khởi động.

2. **Tính tổng bằng `Decimal`** thay vì `float` để tránh sai số floating-point: `money(sum(Decimal(r["price"]) for r in item_rows))`. Hàm `money()` từ `schemas.py` dùng `ROUND_HALF_UP` để đảm bảo làm tròn nhất quán.

3. **Phát hiện `valid_split_payment`** theo EC_POLICY_V1:
   - Điều kiện 1: `len(payment_rows) >= 2` (có ít nhất 2 payment row)
   - Điều kiện 2: `abs(payment_total - item_freight_total) <= Decimal("0.10")` (sai số ≤ 0.10 BRL)
   - Cả hai điều kiện phải đồng thời thỏa mãn.

4. **Build evidence IDs** theo hai định dạng khác nhau:
   - `payment_ids` = `"<order_id>:<seq>"` → cho block `affected_entities` của output JSON
   - `evidence_ids` = `"payment:<order_id>:<seq>"` → cho block `evidence_ids` của output JSON

### Input, output và contract

| Thành phần | Mô tả |
| ---------- | ----- |
| Input | `InputCase` (frozen dataclass từ `schemas.py`) chứa `claimed_order_id`; `OlistDataLoader` chứa toàn bộ CSV đã được index |
| Output | `PaymentEvidence` (frozen dataclass từ `schemas.py`) với 7 fields: `order_id`, `item_total_brl`, `freight_total_brl`, `payment_total_brl`, `valid_split_payment`, `payment_ids`, `evidence_ids` |
| Module phụ thuộc | `src/schemas.py` (PaymentEvidence, money, MAX_ENTITY_IDS, MAX_EVIDENCE_IDS), `src/data_loader.py` (OlistDataLoader) |
| Module sử dụng output | `src/coordinator.py` (gộp vào EvidenceBundle), `src/agents/policy_agent.py` (đọc valid_split_payment và các tổng tiền), `src/agents/verifier_agent.py` (kiểm tra payment_ids tồn tại trong CSV) |
| Điều kiện lỗi cần xử lý | Order không có payment row (trả về tổng = 0, valid_split_payment = False); Order không có item row (trả về item_total = freight_total = 0) |

### Cách xác minh

```bash
cd d:\K3-Day9-Multi-Agent-A2A
python -m src.agents.payment_agent b81ef226f3fe1789b1e8b2acac839d17
```

- **Kết quả mong đợi:** JSON object với `payment_total_brl=99.33`, `item_total_brl=79.8`, `freight_total_brl=19.53`, `valid_split_payment=false` (vì chỉ có 1 payment row).
- **Kết quả thực tế:** Khớp đúng với mong đợi, số liệu tra ngược CSV đều đúng.
- **Artifact/log:** Output in ra stdout, không có file log riêng (agent chạy deterministic, không cần trace LLM).

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Khi tính tổng tiền, có hai lựa chọn kiểu dữ liệu: `float` (Python built-in, đơn giản) hoặc `Decimal` (chuẩn tài chính, chính xác hơn).
- **Các phương án đã cân nhắc:**
  - *Phương án A — `float` + `round(x, 2)`*: Đơn giản, dễ viết. Nhưng `float` có sai số IEEE 754 (ví dụ: `0.1 + 0.2 = 0.30000000000000004`), có thể gây ra kết quả sai khi so sánh với ngưỡng 0.10 BRL.
  - *Phương án B — `Decimal` + `money()` từ `schemas.py`*: Chính xác tuyệt đối cho số thập phân, `ROUND_HALF_UP` nhất quán, khớp với kiểu dữ liệu mà `PaymentEvidence` dataclass yêu cầu.
- **Phương án đã chọn:** Phương án B — `Decimal` + `money()`.
- **Lý do:** `PaymentEvidence` dataclass trong `schemas.py` khai báo các field tiền tệ là `Decimal`. Nếu dùng `float`, Python sẽ báo type mismatch khi `__post_init__` gọi `money()` để chuẩn hóa lại. Ngoài ra, việc so sánh `abs(payment_total - item_freight_total) <= Decimal("0.10")` đòi hỏi độ chính xác tuyệt đối — sai số 0.01 BRL do floating-point có thể khiến một case valid bị đánh là invalid.
- **Bằng chứng quyết định phù hợp:** Smoke test với order `b81ef226f3fe1789b1e8b2acac839d17` cho kết quả `payment_total_brl=99.33`, `item_total_brl=79.8`, `freight_total_brl=19.53` — tổng item+freight = 99.33 khớp chính xác với payment_total, không có sai số.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Sau khi `git pull` từ remote, Coordinator expect `payment_agent.analyze(case, loader)` trả về `PaymentEvidence` từ `schemas.py`, nhưng file P4 ban đầu expose hàm `run(claimed_order_id: str)` trả về `EvidencePayment` (dataclass tự định nghĩa). Pipeline crash với `AgentIntegrationError: Payment Agent must return PaymentEvidence`.
- **Lệnh hoặc bước tái hiện:** Đọc `src/coordinator.py` dòng 179: `if not isinstance(payment, PaymentEvidence)`.
- **Nguyên nhân gốc:** File P4 được viết trước khi `schemas.py` và `coordinator.py` được commit lên repo nhóm. Interface CP0 chưa được chốt tại thời điểm bắt đầu viết code, dẫn đến mismatch giữa local implementation và contract thực tế của nhóm.
- **Cách xử lý:** Rewrite hoàn toàn `payment_agent.py`: đổi tên hàm từ `run()` sang `analyze()`, thêm tham số `loader: OlistDataLoader`, thay `EvidencePayment` bằng `PaymentEvidence` từ `schemas.py`, chuyển kiểu tiền từ `float`/`round()` sang `Decimal`/`money()`, tách `payment_ids` và `evidence_ids` thành hai field riêng theo đúng contract.
- **Cách xác minh sau khi sửa:** Import thành công `from src.agents.payment_agent import analyze`; hàm trả về instance `PaymentEvidence` pass `isinstance` check trong Coordinator.
- **Điều học được:** Trong multi-agent project, phải đọc kỹ contract (schemas.py) **trước** khi viết implementation. Contract-first development tránh được việc phải rewrite toàn bộ sau khi tích hợp.

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của bạn (áp dụng cho bài lab Day 9 — Multi-Agent Dispute Resolution):

1. **Luồng dữ liệu từ CSV đến output JSON:** `main.py` đọc từng file `input/EC_XXX.json`, tạo `InputCase`. `OlistDataLoader` load và index toàn bộ 9 CSV một lần khi khởi động. Coordinator gọi song song 3 agent (P2, P3, P4), mỗi agent truy xuất dữ liệu qua loader và trả về evidence dataclass. Coordinator gộp 3 evidence vào `EvidenceBundle` chuyển cho Policy Agent (P5). P5 áp rule EC_POLICY_V1 ra `PolicyResult`. Coordinator lắp `OutputVerdict` từ evidence + policy, Verifier (P6) kiểm tra lần cuối, rồi ghi file `output/EC_XXX.json`.

2. **Evidence và root cause:** Mỗi agent chỉ trả về dữ liệu thuần domain của mình (payment agent không biết delivery late hay không). Policy Agent tổng hợp tất cả evidence để chọn root cause theo thứ tự ưu tiên trong bảng EC_POLICY_V1: canceled/unavailable → seller late → logistics late → valid_split_payment → unsupported claim.

3. **Kiểm soát chất lượng:** Verifier Agent kiểm tra (1) evidence ID có thật trong CSV không, (2) số tiền làm tròn đúng 2 chữ số, (3) không vượt giới hạn số lượng ID/evidence/root-cause/action, (4) `confidence` trong `[0, 1]`. Nếu fail → Coordinator raise lỗi, không ghi file output.

4. **Tách biệt domain:** Mỗi agent chỉ được đọc CSV trong phạm vi domain của mình (Payment Agent không đọc `orders.csv`, `sellers.csv`). Điều này đảm bảo không có agent nào "nhìn trộm" thông tin của domain khác và tự suy diễn quyết định ngoài phạm vi.

5. **Handoff bằng chứng:** Agent không truyền raw CSV data sang agent khác. Mỗi handoff là một frozen dataclass được validate bởi `__post_init__`, đảm bảo data integrity tại từng bước truyền.

**Câu trả lời:** (Xem giải thích tổng hợp 5 điểm ở trên)

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Đinh Lê Quỳnh Phương
**Ngày xác nhận:** 2026-08-05
