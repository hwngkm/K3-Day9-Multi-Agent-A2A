# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                |
| --------------- | ----------------------- |
| Họ và tên       | Nguyễn Ngọc Sơn         |
| MSSV            | 2A202601948             |
| Khóa/Lớp        | K3                      |
| Vai trò chính   | P3 — Delivery Agent     |
| Ngày hoàn thành | 2026-08-05              |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable      | File/hàm phụ trách                                    | Input nhận vào                         | Output bàn giao                                    | Trạng thái   |
| ----------------------- | ----------------------------------------------------- | -------------------------------------- | -------------------------------------------------- | ------------ |
| Delivery Tool (P3)      | `src/tools/delivery_tool.py` — `query_delivery()`     | `claimed_order_id`, `OlistDataLoader`  | `DeliveryToolResponse` (timestamps + boolean flag) | Hoàn thành   |
| Delivery Agent (P3)     | `src/agents/delivery_agent.py` — `analyze()`          | `InputCase`, `OlistDataLoader`         | `DeliveryEvidence` (handoff cho Coordinator)       | Hoàn thành   |
| metadata.json           | `logging/metadata.json`                               | —                                      | Ghi model name, param size, framework, runtime     | Hoàn thành   |

Chỉ nhận ownership cho phần bạn trực tiếp thực hiện. Liên hệ rõ phần việc của bạn với đầu vào, đầu ra và các thành viên phụ thuộc vào phần đó.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                                | Thành viên/module được hỗ trợ              | Kết quả và bằng chứng                                                     |
| ---------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| Cập nhật `src/tools/__init__.py`         | P2 (Order & Seller Tool) — module tools    | Export `DeliveryTool`, `query_delivery` vào package chung cho Coordinator |
| Refactor theo contract-first P2 pattern  | P1 (Coordinator) — tích hợp pipeline      | `analyze()` interface khớp `EvidenceAnalyzer` type, không cần sửa P1     |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện                                     | File/hàm/artifact liên quan              | Kết quả bàn giao                                         | Cách xác minh                        |
| ---------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------- | ------------------------------------ |
| Implement `DeliveryTool` — lookup CSV, tính `carrier_delivered_late` | `src/tools/delivery_tool.py`  | Tool trả đúng boolean và timestamps cho 5 case mẫu       | `python test_delivery_tool.py`       |
| Implement `analyze()` — 2-turn tool-calling loop với Qwen  | `src/agents/delivery_agent.py`           | `DeliveryEvidence` dataclass đúng schema CP0             | Import và unit test thủ công         |
| Hallucination guard `_validated_evidence()`                | `src/agents/delivery_agent.py`           | `AgentOutputError` nếu LLM override boolean của tool     | Review code logic                    |

Output cụ thể: chạy `python test_delivery_tool.py` trên 5 case đầu cho kết quả như sau:

```
EC_001.json: status=delivered  evaluable=True   late=True   delivered=2017-12-15  estimated=2017-12-12
EC_002.json: status=delivered  evaluable=True   late=False  delivered=2018-01-31  estimated=2018-02-09
EC_003.json: status=canceled   evaluable=False  late=None
EC_004.json: status=delivered  evaluable=True   late=False  delivered=2018-06-20  estimated=2018-07-04
EC_005.json: status=unavailable evaluable=False late=None
```

Tool phân loại đúng 3 nhánh: late/on-time/non-evaluable (canceled, unavailable).

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Delivery Agent (P3) cần xác định cờ `carrier_delivered_late` — tức là order có được giao tới tay khách sau ngày `order_estimated_delivery_date` hay không. Cờ này là input bắt buộc cho Policy Agent (P5) để phân loại `late_delivery_logistics` vs `unsupported_late_claim`. Pipeline cần đảm bảo:
- Tính toán phải **deterministic** — không để LLM tự suy diễn từ chuỗi ngày tháng
- LLM có **agentic behavior** thật sự — không phải rubber-stamp kết quả có sẵn
- Agent không được đọc payment data (giới hạn domain theo contract CP0)

### Cách triển khai

Tách hai layer rõ ràng:

**Layer 1 — `DeliveryTool` (Python thuần, không LLM):**
- `query_delivery()` đọc `orders_by_id` để lấy 3 timestamp: `order_delivered_customer_date`, `order_estimated_delivery_date`, `order_delivered_carrier_date`
- Tính `carrier_delivered_late = delivered_customer > estimated_delivery` khi cả hai có giá trị
- Trả về `None` (không thể đánh giá) khi status là `canceled`/`unavailable` hoặc thiếu timestamp
- Đọc thêm `items_by_order_id` chỉ để build evidence IDs (`item:<order_id>:<seq>`) — không dùng thông tin item/seller

**Layer 2 — `delivery_agent.py` (tool-calling loop, 2 turns):**
- **Turn 1**: LLM nhận case context, gọi tool `query_delivery` (đúng 1 lần)
- **Tool execution**: Python chạy `DeliveryTool.lookup()` — deterministic, authoritative
- **Turn 2**: Feed kết quả tool về, LLM xác nhận boolean và viết `reasoning` 1 câu, trả về JSON theo `AGENT_OUTPUT_SCHEMA`
- **Guard**: `_validated_evidence()` so sánh `carrier_delivered_late` của LLM với tool — nếu khác → `AgentOutputError`

Điểm then chốt: boolean luôn do Python tính, LLM chỉ được "confirm" — không thể hallucinate sai kết quả số tiền hay thời gian.

### Input, output và contract

| Thành phần              | Mô tả                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------- |
| Input                   | `InputCase` (case_id, claimed_order_id) + `OlistDataLoader` (đã load 9 CSV)          |
| Output                  | `DeliveryEvidence(order_id, carrier_delivered_late, 3 timestamps, evidence_ids)`       |
| Module phụ thuộc        | `src/schemas.py` (DeliveryEvidence), `src/data_loader.py` (OlistDataLoader)           |
| Module sử dụng output   | `src/coordinator.py` — `_collect_evidence()` → `EvidenceBundle` → Policy Agent (P5)  |
| Điều kiện lỗi cần xử lý | Order không tồn tại → `AgentOutputError`; LLM không gọi tool → `AgentRuntimeError`; LLM override boolean → `AgentOutputError`; Ollama offline → `AgentRuntimeError` với hướng dẫn pull model |

### Cách xác minh

```bash
# Chạy từ root repo
python test_delivery_tool.py
```

- **Kết quả mong đợi:** 5 dòng output, mỗi dòng có `late=True/False/None` khớp với so sánh timestamp thủ công.
- **Kết quả thực tế:** EC_001 → `late=True` (giao 2017-12-15 > estimate 2017-12-12 ✓); EC_002 → `late=False` (giao 2018-01-31 < estimate 2018-02-09 ✓); EC_003/005 → `late=None` do canceled/unavailable ✓.
- **Artifact/log:** `test_delivery_tool.py` tại root repo (không chứa secret).

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Phiên bản đầu dùng Groq API (llama-3.1-8b-instant) và để LLM tự tính `carrier_delivered_late` từ chuỗi timestamp rồi agent chỉ "rubber-stamp" lại. Có nguy cơ LLM trả sai (hallucinate ngày tháng) và bị Groq rate-limit trong điều kiện thi đấu chạy 50 case liên tục.
- **Các phương án đã cân nhắc:**
  1. Giữ Groq + rubber-stamp: LLM nhận boolean đã tính sẵn, chỉ viết reasoning — nhanh code nhưng không phải "agent" thật, dễ bị rate-limit.
  2. Chuyển sang Ollama (local) + tool-calling loop: LLM tự gọi tool, nhận raw timestamps, xác nhận kết quả — không rate-limit, có agentic behavior thật.
- **Phương án đã chọn:** Phương án 2 — Ollama + Qwen2.5-7b-instruct, tool-calling 2-turn loop.
- **Lý do:** (1) Groq free tier bị rate-limit ~30 req/min, 50 case × 3 agent song song sẽ fail; (2) Pattern tool-calling khớp đúng thiết kế P2 đã có, tái dùng được `OllamaChatClient` và `_validated_evidence` guard; (3) Qwen2.5-7b hỗ trợ tool-calling natively qua Ollama, không cần thư viện ngoài; (4) Correctness cao hơn: Python tính boolean, LLM không thể sai số.
- **Bằng chứng:** Commit `6f08133` (Groq) → `5c20ad5` (Ollama/Qwen); `test_delivery_tool.py` chạy thành công trên 5 case với kết quả đúng.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Groq API rate-limit — khi chạy thử nhiều case liên tiếp, Groq trả `429 Too Many Requests` sau khoảng 20-30 request.
- **Lệnh hoặc bước tái hiện:** Gọi `_call_llm()` liên tiếp với nhiều `order_id` khác nhau trong vòng lặp, Groq từ chối sau ~1 phút.
- **Nguyên nhân gốc:** Groq free tier giới hạn ~30 request/phút và ~14,400 token/phút cho model llama-3.1-8b-instant. Coordinator chạy 3 agent song song (`ThreadPoolExecutor`) → 3x request/case → hết quota nhanh.
- **Cách xử lý:** Chuyển toàn bộ sang Ollama local + Qwen2.5:7b-instruct. Đồng thời refactor từ rubber-stamp pattern sang proper tool-calling loop để LLM có agentic behavior thật (gọi `query_delivery`, nhận raw data, xác nhận boolean, viết reasoning).
- **Cách xác minh sau khi sửa:** `python test_delivery_tool.py` — tool layer chạy không cần LLM, output đúng; agent layer dùng Ollama local (không có quota). Commit `5c20ad5` trên branch `nguyen_ngoc_son`.
- **Điều học được:** Trong competition chạy batch 50 case, cần ưu tiên provider local (Ollama) để tránh rate-limit. Tool-calling pattern còn có lợi thêm: boolean được tính bởi Python (deterministic), LLM không thể hallucinate sai kết quả ngày tháng.

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của mình:

1. **Input → Agent → Handoff:** `main.py` đọc `input/EC_XXX.json`, tạo `InputCase`, truyền vào `Coordinator.run_case()`. Coordinator dispatch song song 3 agent (P2 Order&Seller, P3 Delivery, P4 Payment) qua `ThreadPoolExecutor`. Mỗi agent chỉ đọc domain CSV của mình qua `OlistDataLoader`, trả về evidence dataclass riêng. Ba evidence gộp vào `EvidenceBundle`.

2. **EvidenceBundle → PolicyResult:** Policy Agent (P5) nhận `EvidenceBundle` — **không đọc CSV thô** — áp bảng rule EC_POLICY_V1 theo thứ tự ưu tiên (canceled > unavailable > late_seller > late_logistics > split_payment > valid). Trả về `PolicyResult` (primary_issue, refund, action).

3. **PolicyResult → OutputVerdict → Verifier → file:** Coordinator gọi `assemble_verdict()` lắp JSON từ evidence + policy. Verifier Agent (P6) check evidence ID có thật trong CSV, số tiền làm tròn đúng, giới hạn số lượng ID. Nếu pass → ghi `output/EC_XXX.json` + append `trace.jsonl`.

4. **Vai trò Delivery Agent trong luồng:** Cờ `carrier_delivered_late` từ P3 là input then chốt để P5 phân biệt `late_delivery_logistics` (carrier giao muộn, seller giao đúng hạn) với `unsupported_late_claim` (giao đúng hạn — bác khiếu nại) hay `late_delivery_seller` (khi P2 báo `seller_handoff_late=True`).

5. **Tại sao tách tool layer ra khỏi agent?** Để đảm bảo tính deterministic: timestamp comparison do Python tính (không float, không locale), LLM chỉ được "confirm" và viết explanation. Verifier có thể cross-check evidence ID có trong CSV mà không cần tin vào LLM output.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Ngọc Sơn
**Ngày xác nhận:** 2026-08-05
