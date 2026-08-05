# Architecture — K3 Day 09 Multi-Agent E-commerce Dispute Resolution

## 1. Sơ đồ agent & luồng handoff

```
Input case (claimed_order_id)
        │
        ▼
   Coordinator ── dispatch song song ──┬──> Order & Seller Agent ──┐
                                       ├──> Delivery Agent ────────┤
                                       └──> Payment Agent ─────────┤
                                                                    ▼
                                                        (evidence gộp lại)
                                                                    │
                                                                    ▼
                                                            Policy Agent
                                                     (áp EC_POLICY_V1, chọn
                                                      root cause / refund / action)
                                                                    │
                                                                    ▼
                                                    Coordinator lắp JSON output
                                                                    │
                                                                    ▼
                                                            Verifier Agent
                                                (check evidence tồn tại thật trong
                                                 CSV, format ID, làm tròn tiền,
                                                 giới hạn số lượng, confidence)
                                                    │ fail             │ pass
                                                    ▼                  ▼
                                        trả lại Policy/Coordinator   ghi output/EC_XXX.json
                                        để sửa                       + append trace.jsonl
```

Nguyên tắc: mỗi agent chỉ xử lý domain của mình rồi handoff bằng chứng (evidence) cho agent kế tiếp — không dồn toàn bộ suy luận vào một prompt duy nhất.

## 2. Vai trò & quyền truy cập

| Agent | Vai trò | Được đọc | Không được đọc |
|---|---|---|---|
| **Coordinator** | Nhận case, điều phối gọi các agent, tổng hợp output cuối. Không tự suy luận rule nghiệp vụ. | input case JSON, output các agent khác | — |
| **Order & Seller Agent** | Kiểm tra trạng thái đơn, item, seller, mốc bàn giao (`shipping_limit_date`). Trả về cờ `seller_handoff_late`. | `orders.csv`, `order_items.csv`, `sellers.csv` | dữ liệu payment |
| **Delivery Agent** | So sánh thời điểm giao thực tế (`order_delivered_customer_date`) với hạn giao (`order_estimated_delivery_date`). Trả về cờ `carrier_delivered_late`. | `orders.csv`, `order_items.csv` | dữ liệu payment |
| **Payment Agent** | Đối soát tổng payment với tổng item + freight, phát hiện split payment hợp lệ. | `order_payments.csv`, `order_items.csv` | dữ liệu seller/delivery thô |
| **Policy Agent** | Áp bảng rule ưu tiên `EC_POLICY_V1` lên evidence nhận được, chọn root cause, responsible party, refund, action. | **chỉ** evidence do 3 agent trên trả về (không đọc CSV thô) | mọi CSV thô |
| **Verifier Agent** | Xác minh evidence ID có thật trong CSV, số tiền làm tròn đúng, giới hạn số lượng ID/evidence/root-cause/action, `confidence` trong `[0,1]` trước khi ghi file. | evidence, output cuối, toàn bộ CSV | — |

## 3. Phân công 6 người (độc lập theo contract-first)

Ý tưởng cốt lõi: schema Input/Output và bảng rule nghiệp vụ đã có sẵn trong `README.md` (không phụ thuộc nội dung 50 case thật), nên Order & Seller / Delivery / Payment Agent có thể code và test độc lập với `order_id` tự chọn từ CSV **trước khi** input thật được công bố lúc 9h30. Policy Agent và Verifier Agent code trước dựa trên **mock evidence/output** đúng theo schema đã chốt, chỉ tích hợp thật ở checkpoint sau.

| # | Người | Sở hữu (file) | Input | Output (theo contract chốt ở CP0) | Phụ thuộc |
|---|---|---|---|---|---|
| P1 | Coordinator / Data Lead | `schemas.py`, `data_loader.py`, `coordinator.py`, `main.py` | input case JSON | gọi các agent khác, lắp output cuối | Chốt contract đầu tiên (CP0) để 5 người còn lại tách việc được ngay |
| P2 | Order & Seller Agent | `agents/order_seller_agent.py` | `claimed_order_id` | order_status, item list, seller_id, cờ `seller_handoff_late` | Không phụ thuộc ai — chỉ cần schema đã chốt |
| P3 | Delivery Agent | `agents/delivery_agent.py` | `claimed_order_id` | cờ `carrier_delivered_late` | Không phụ thuộc ai |
| P4 | Payment Agent | `agents/payment_agent.py` | `claimed_order_id` | tổng payment, tổng item+freight, cờ `valid_split_payment`, `payment_evidence_ids` | Không phụ thuộc ai |
| P5 | Policy Agent | `agents/policy_agent.py` | evidence từ P2+P3+P4 (dev bằng mock evidence) | root_cause_analysis, responsible_parties, financial_resolution, resolution_actions | Cần schema evidence chốt ở CP0; code trước bằng mock |
| P6 | Verifier + QA/Logging/Submission | `agents/verifier_agent.py`, `logging/metadata.json`, `logging/trace.jsonl`, script check + đóng gói zip | output cuối (dev bằng output mẫu tự tạo) | pass/fail + lý do lỗi; log trace; file nộp | Chỉ cần schema output (đã có sẵn trong README mục 6) |

Cấu trúc thư mục code đề xuất:
```
src/
  schemas.py              # InputCase, EvidenceOrderSeller, EvidenceDelivery,
                           # EvidencePayment, PolicyResult, OutputVerdict
  data_loader.py           # load + join 9 CSV, index theo order_id
  agents/
    order_seller_agent.py
    delivery_agent.py
    payment_agent.py
    policy_agent.py
    verifier_agent.py
  coordinator.py
  main.py                    # entrypoint: input/*.json -> output/*.json
```

## 4. Checkpoint nội bộ & % thành công tối thiểu

| Checkpoint | Khung giờ | Mục tiêu | % thành công tối thiểu |
|---|---|---|---|
| **CP0 — Chốt contract** | 9h00–9h15 (đầu CP1) | Cả 6 người thống nhất: schema Input/Output, schema evidence giữa các agent, cấu trúc thư mục, quyền truy cập từng agent | **100%** — gate bắt buộc, chưa chốt xong thì chưa tách việc |
| **CP1b — Build độc lập trên data tự chọn** | 9h15–9h30 (cuối CP1, trước khi input thật ra) | Mỗi người tự chọn 3–5 `order_id` từ CSV làm input giả để dựng khung logic agent mình | ≥50% nhánh rule/logic đã viết ra được, chạy không crash trên mẫu tự chọn |
| **CP2a — Tích hợp lần 1** | 9h30–10h15 (đầu CP2, ngay khi có input thật) | P1 nối toàn bộ pipeline, chạy full 50 case thật lần đầu | Pipeline chạy hết 50 case không crash; ≥60% case ra JSON đúng schema (chưa cần đúng nội dung) |
| **CP2b — Refine theo rule** | 10h15–11h30 | Từng người rà lại module mình theo đúng bảng rule ưu tiên, sửa case sai | ≥90% case đúng schema; tự chấm tay mẫu 10–15 case ước lượng ≥75% đúng root cause/số tiền |
| **CP2c — Verifier full-check** | 11h30–12h15 | P6 chạy Verifier trên toàn bộ 50 case: evidence ID có thật trong CSV, số tiền làm tròn đúng, giới hạn số lượng, confidence hợp lệ | **100% case pass Verifier** (không case nào bị hard-gate = 0 điểm) |
| **CP2d — Freeze & log** | 12h15–12h30 (cuối CP2) | Chốt `output/`, ghi `trace.jsonl` (không append, lượt chạy mới nhất), điền `metadata.json` (model, param size, framework, runtime) | **100%** — đúng 50 file `EC_001.json`…`EC_050.json`, log đầy đủ |
| **CP3 — Nộp bài** | 12h30–13h00 | Nén `output/` thành zip, kiểm tra không dính source/`.env`/audit file | **100%** — zip hợp lệ đúng yêu cầu mục 8 README |

Ghi chú: % ở CP2a/CP2b là mốc tối thiểu để biết nhóm có đang trễ tiến độ hay không; càng gần CP2c/CP2d/CP3 càng phải chạm 100% vì đó là điều kiện tránh bị hard-gate 0 điểm hoặc bị loại vì sai định dạng nộp bài.

## 5. Việc cần làm ngay (trước khi input ra lúc 9h30)

- Chọn model ≤10B cho các agent (constraint bắt buộc — README mục 9): ví dụ Qwen2.5-7B-Instruct / Llama-3.1-8B qua Ollama hoặc provider bất kỳ. Tên model phải hard-code trong source code (không để trong `.env`), đồng thời ghi lại trong `logging/metadata.json`.
- P1 tạo `src/schemas.py` trước tiên — đây là gate của CP0, mở khóa cho 5 người còn lại tách việc.

## 6. Runtime mới — Registry, Task Graph và Model Gateway

Runtime không import agent module trực tiếp trong Coordinator. Thay vào đó,
`config/agents.json` đăng ký agent, callable, stage và dependency; `TaskGraph`
topo-sort cấu hình này trước khi thực thi.

```
config/agents.json                 policy/EC_POLICY_V1.json
        │                                       │
        ▼                                       ▼
 AgentRegistry ──> TaskGraph             Policy Agent
        │              │                       │
        │       layer 1 (song song)            │
        │   ┌───────┬────────┬───────┐          │
        │   ▼       ▼        ▼       │          │
        │  P2      P3       P4       │          │
        │   └─────── EvidenceBundle ─┴──────────┘
        │                     │
        │                 layer 2: P5
        │                     │
        │                 layer 3: P6
        │                     │ pass only
        │                 layer 4: Explanation Agent
        │                     │
        └────────────── HandoffEnvelope + Audit Timeline
```

- **AgentRegistry:** đổi agent implementation bằng configuration, không cần
  sửa Coordinator.
- **HandoffEnvelope:** mỗi task có agent name, dependency, payload/evidence,
  thời điểm start/end, duration và status thành công/lỗi.
- **TaskGraph:** P2/P3/P4 chỉ chạy song song trong layer đầu; P5 cần đủ ba
  evidence; P6 cần policy; Explanation Agent chỉ chạy sau Verifier pass.
- **Policy config:** thứ tự rule, cause, party, action, refund strategy nằm ở
  `policy/EC_POLICY_V1.json`. `schemas.py` vẫn validate các canonical label.
- **Model Gateway:** chỉ Explanation Agent được phép gọi
  `Qwen/Qwen2.5-3B-Instruct` (3.09B) qua Ollama-compatible endpoint. Khi chưa
  bật local Qwen, gateway trả deterministic fallback và không ảnh hưởng output.
- **Audit Timeline:** `logging/audit_timeline.json` là dữ liệu máy đọc và
  `logging/audit_timeline.html` là bảng trực quan để thuyết trình.

## 7. Điểm mới — Evidence Receipt audit layer

Sau khi Verifier pass, hệ thống tạo một **Evidence Receipt** riêng trong
`logging/decision_certificates.jsonl`; file này không nằm trong `output.zip`.

```
OutputVerdict đã pass Verifier
        │
        ├── JSON nộp bài: output/EC_XXX.json
        │
        └── Evidence Receipt (audit/presentation)
              - selected_policy + giải thích ngắn
              - evidence IDs đầy đủ, có thể truy ngược CSV
              - verifier_gate = passed
              - SHA-256 của JSON output
```

Lớp này biến từng verdict thành một "biên nhận quyết định": người thuyết
trình có thể chứng minh rule nào được kích hoạt, evidence nào hỗ trợ kết luận,
và JSON nộp bài chưa bị thay đổi sau khi kiểm chứng. Decision logic vẫn là
EC_POLICY_V1 xác định; model nhẹ chỉ dành cho lớp giải thích, không được phép
thay đổi refund, party hay action.
