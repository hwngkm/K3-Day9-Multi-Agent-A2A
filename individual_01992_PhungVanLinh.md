# Báo cáo cá nhân — K3 Day 9 Multi-Agent

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | Phùng Văn Linh |
| Mã học viên | 2A202601992 |
| Cohort | 3 |
| Vai trò chính | P6 — Verifier, QA/Logging và đóng gói submission |
| Ngày hoàn thành | 05/08/2026 |

## 2. Vai trò và phạm vi công việc

Tôi phụ trách cổng kiểm định cuối của pipeline. P6 không đưa ra quyết định chính sách thay P5 mà kiểm tra độc lập verdict đã lắp ráp trước khi Coordinator ghi kết quả. Phần việc của tôi gồm:

- Kiểm tra `case_id`, schema, giá trị enum và các giới hạn số lượng trong output.
- Đối chiếu order, item, seller, payment và evidence ID trực tiếp với CSV gốc.
- Tính lại primary issue theo thứ tự ưu tiên của `EC_POLICY_V1` để phát hiện quyết định sai.
- Tính lại item total, freight total, payment total và refund; kiểm tra tiền không âm và đã làm tròn hai chữ số.
- Kiểm tra responsible party, root cause, case status và resolution action khớp policy.
- Chỉ cho phép ghi output khi Verifier trả `passed=True`; trường hợp lỗi phải có lý do kiểm tra được trong trace.
- Kiểm tra batch đủ 50 case, tạo evidence log, metadata và gói nộp bài.

Các artifact chính:

| Module/deliverable | File/hàm phụ trách | Input | Output |
|---|---|---|---|
| Verifier Agent | `src/agents/verifier_agent.py` — `verify()` | `InputCase`, `OutputVerdict`, `OlistDataLoader` | `VerificationResult(passed, errors)` |
| Kiểm thử tích hợp | `tests/test_full_pipeline.py` | pipeline và 50 input case | xác nhận DAG, handoff, 50 output, ZIP và trace |
| Evidence log | `logging/trace.jsonl` | kết quả chạy batch mới nhất | 50 dòng trạng thái có thể audit |
| Runtime metadata | `logging/metadata.json` | model/framework/runtime thật | thông tin môi trường đã dùng |
| Gói bàn giao | `submission.zip` | output đã pass cùng tài liệu/log | artifact nộp bài theo cấu trúc yêu cầu |

## 3. Kết quả theo vai trò

Verifier thực hiện defense-in-depth thay vì tin vào dataclass hoặc kết quả P5. Các nhóm kiểm tra chính gồm:

1. **Tính đúng của quyết định:** `_expected_primary_issue()` đọc lại order, item và payment; áp dụng đúng thứ tự `canceled`, `unavailable`, late seller, late logistics, split payment và claim không được hỗ trợ.
2. **Tính thật của bằng chứng:** `_validate_evidence_id()` chỉ chấp nhận các ID dựng được từ CSV của đúng order; policy evidence phải trùng root-cause được chọn.
3. **Tính đúng của entity:** `_validate_entity_ids()` so sánh toàn bộ tập order/item/seller/payment với dữ liệu nguồn và kiểm tra giới hạn tối đa 5 ID mỗi tập.
4. **Tính đúng của tiền:** Verifier tự cộng giá item, freight và payment bằng `Decimal`, làm tròn hai chữ số, sau đó kiểm tra refund theo strategy của policy.
5. **Tính đúng của hành động:** case status, cause, responsible party và action phải đồng nhất với `EC_POLICY_V1`.
6. **Khả năng audit:** lỗi được gom đầy đủ thay vì dừng ở lỗi đầu, giúp trace chỉ ra chính xác điều cần sửa.

Kết quả bàn giao cuối được xác minh bằng test tích hợp: DAG có P2/P3/P4 ở lớp evidence chạy độc lập, tiếp theo là P5 Policy, P6 Verifier và Explanation; batch chỉ được xuất bản sau khi toàn bộ 50 case vượt qua gate.

## 4. Giải thích kỹ thuật

### Input, output và handoff

P6 nhận ba đối tượng: case ban đầu, verdict đã được Coordinator lắp ráp và data loader có quyền đọc CSV. P6 trả `VerificationResult`, không sửa âm thầm verdict. Nếu `passed=False`, Coordinator phát sinh `VerificationFailedError`, ghi tình trạng thất bại vào trace và không xuất bộ output chưa đạt.

Luồng quyết định:

```text
P2 Order/Seller ─┐
P3 Delivery ─────┼─> EvidenceBundle -> P5 Policy -> OutputVerdict
P4 Payment ──────┘                              |
                                                 v
                                      P6 independent verify
                                         | fail      | pass
                                         v           v
                                    trace lỗi     ghi output + trace
```

### Vì sao P6 phải đọc lại CSV

Nếu Verifier chỉ kiểm tra JSON schema, một output vẫn có thể đúng định dạng nhưng chứa evidence không tồn tại, sai seller chịu trách nhiệm hoặc refund sai. Vì vậy P6 tái tính các sự kiện quan trọng từ nguồn dữ liệu gốc, độc lập với evidence trung gian. Cách làm này biến Verifier thành một cổng kiểm định thực sự, đồng thời giảm false positive — điều kiện hard gate của bài.

### Chính sách xuất bản nguyên tử

Pipeline ghi trace của lượt chạy mới nhất và chỉ thay đổi `output/` sau khi tất cả case pass. Từng JSON được ghi qua file tạm rồi `os.replace`, giúp tránh file dở dang khi tiến trình bị ngắt. ZIP cũng được tạo tạm và thay thế nguyên tử sau khi hoàn tất.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Schema validation ở `src/schemas.py` đã chặn nhiều dữ liệu sai, nhưng không thể chứng minh evidence ID tồn tại hoặc số tiền khớp CSV.
- **Phương án cân nhắc:** chỉ validate schema; kiểm tra ngẫu nhiên một số case; hoặc tái tính độc lập toàn bộ 50 case.
- **Phương án chọn:** tái tính độc lập mọi case bằng Python và `Decimal` ở P6.
- **Lý do:** deterministic, có thể lặp lại, không phụ thuộc LLM và bao phủ đúng các hard gate về evidence, money, limits và policy consistency.
- **Đánh đổi:** P6 có thêm quyền đọc toàn bộ CSV và logic policy bị lặp một phần; đổi lại, sai khác giữa P5 và dữ liệu nguồn sẽ bị phát hiện trước khi ghi file.

## 6. Lỗi/blocker và cách xử lý

Một rủi ro quan trọng là output có thể đúng schema nhưng policy evidence hoặc responsible party không khớp dữ liệu thật. Cách xử lý là để Verifier thu thập toàn bộ lỗi trong một lượt, kiểm tra membership của từng evidence ID, tính lại seller của order và so sánh chính xác party/action/cause với policy. Sau khi sửa, chạy test tích hợp toàn pipeline và kiểm tra trực tiếp danh sách thành viên trong ZIP, số JSON và số dòng trace.

## 7. Hiểu biết về luồng end-to-end

1. `src/main.py` đọc 50 input, xác nhận `case_id` khớp tên file và order tồn tại.
2. Coordinator đọc registry và dispatch P2 Order/Seller, P3 Delivery, P4 Payment ở lớp evidence.
3. Ba kết quả được gom thành `EvidenceBundle`; P5 áp dụng `EC_POLICY_V1` và tạo `PolicyResult`.
4. Coordinator lắp `OutputVerdict`; P6 đọc lại CSV và policy để kiểm định độc lập.
5. Chỉ verdict pass mới được ghi vào `output/`. Trace lưu agent đã gọi, issue, refund, trạng thái và lỗi verifier nếu có.
6. Metadata ghi model/framework/runtime. Cuối cùng hệ thống đóng gói artifact và kiểm tra lại đúng 50 file JSON.

Vai trò P6 nằm ở ranh giới giữa “kết quả được đề xuất” và “kết quả được phép xuất bản”. Đây là nơi bảo đảm handoff từ các agent trước có thể kiểm tra, tiền không sai và output không vi phạm hard gate.

## 8. Cách xác minh

```powershell
python -m unittest tests.test_full_pipeline -v
```

Các tiêu chí cần đạt:

- 50 file từ `EC_001.json` đến `EC_050.json`, không thiếu hoặc thừa JSON.
- 50 dòng trong `trace.jsonl`, mỗi case có trạng thái `written` và không có verifier error.
- Mọi JSON parse được và vượt qua Verifier.
- `metadata.json` khai báo model dưới hoặc bằng 10B cùng framework/runtime.
- `submission.zip` chứa `output/`, `architecture.md`, `trace.jsonl`, `metadata.json` đúng vị trí.

## 9. Cam kết

- [x] Báo cáo phản ánh đúng phần việc P6 và mức hiểu của tôi.
- [x] Tôi hiểu luồng end-to-end và các handoff, không chỉ riêng module Verifier.
- [x] Kết quả “pass” chỉ được ghi nhận sau kiểm thử có thể lặp lại.
- [x] Báo cáo và gói nộp không chứa `.env`, API key, token hoặc secret.
- [x] Nội dung không sao chép nguyên văn báo cáo của thành viên khác.

**Họ và tên:** Phùng Văn Linh  
**Mã học viên:** 2A202601992  
**Cohort:** 3  
**Ngày xác nhận:** 05/08/2026
