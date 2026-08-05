# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                   |
| --------------- | -------------------------- |
| Họ và tên       | Đoàn Minh Hiếu             |
| MSSV            | 2A202601733                |
| Khóa/Lớp        | K3                         |
| Vai trò chính   | Policy Agent / Business Rule Implementation |
| Ngày hoàn thành | 2026-08-05                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách           | Input nhận vào                          | Output bàn giao                    | Trạng thái       |
| ------------------ | --------------------------- | ---------------------------------------- | ---------------------------------- | ---------------- |
| Policy Agent       | `src/agents/policy_agent.py`| `EvidenceBundle` từ các agent khác      | `PolicyResult` theo CP0 contract   | Hoàn thành       |
| Package init       | `src/__init__.py`, `src/agents/__init__.py` | Python package imports           | Package importable                 | Hoàn thành       |
| Virtual environment| `.venv`                     | Python 3.11 interpreter                 | Local virtual environment          | Hoàn thành       |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                 | Thành viên/module được hỗ trợ | Kết quả                 |
| ------------------------- | ----------------------------- | ----------------------- |
| Kiểm tra tích hợp CP0     | Coordinator + schema          | Đảm bảo `PolicyResult` được dùng đúng |
| Gợi ý cấu trúc module     | Các agent khác                | Mở đường cho P2/P3/P4 không đọc CSV thô |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao          | Cách xác minh   |
| --------------------- | --------------------------- | ------------------------- | --------------- |
| Triển khai Policy Agent | `src/agents/policy_agent.py` | Decision tree EC_POLICY_V1 | `python -m src.main --validate-only` |
| Tạo môi trường ảo     | `.venv`                     | Python 3.11 venv          | `py -3.11 -m venv .venv` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

- `src/agents/policy_agent.py` tạo `PolicyResult` với `primary_issue`, `case_status`, `confidence`, `ranked_causes`, `responsible_parties`, `recommended_refund_brl`, và `resolution_actions`.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

- Policy Agent phải quyết định root cause và resolution action chỉ trên evidence đã được tổng hợp, không đọc CSV thô.
- Phải tuân thủ `EC_POLICY_V1` theo thứ tự ưu tiên và trả về kết quả hợp lệ theo contract CP0.

### Cách triển khai

- Xây dựng `analyze(evidence: EvidenceBundle) -> PolicyResult` trong `src/agents/policy_agent.py`.
- Áp thứ tự ưu tiên rule: `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`, `late_delivery_logistics`, `valid_split_payment`, `unsupported_late_claim`.
- Sử dụng `EvidenceBundle` để giới hạn phạm vi input và tránh truy cập dữ liệu thô.
- Chuyển output ra `PolicyResult` và để `Coordinator` lắp thành `OutputVerdict`.

### Input, output và contract

| Thành phần              | Mô tả                                  |
| ----------------------- | -------------------------------------- |
| Input                   | `EvidenceBundle` gồm `OrderSellerEvidence`, `DeliveryEvidence`, `PaymentEvidence` |
| Output                  | `PolicyResult` với các trường nội dung policy |
| Module phụ thuộc        | `src/schemas.py`, `src/coordinator.py` |
| Module sử dụng output   | `src/coordinator.py` để lắp `OutputVerdict` |
| Điều kiện lỗi cần xử lý | EvidenceBundle phải thuộc cùng `order_id`, `PolicyResult` phải hợp lệ |

### Cách xác minh

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m src.main --validate-only
```

- **Kết quả mong đợi:** `PolicyResult` được tạo mà không phá vỡ contract và `src.main` xác nhận tất cả input case hợp lệ.
- **Kết quả thực tế:** Môi trường ảo đã tạo thành công và module `src.agents.policy_agent` sẵn sàng tích hợp.
- **Artifact/log:** `src/agents/policy_agent.py`, `.venv`

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Policy Agent phải quyết định dựa trên evidence, không được đọc bất kỳ CSV thô nào.
- **Các phương án đã cân nhắc:**
  - 1) Để Policy Agent đọc CSV thô và tự xác minh cả điều kiện business.
  - 2) Cấu trúc evidence bundle và giữ Policy Agent chỉ xử lý rule logic.
- **Phương án đã chọn:** phương án 2, thiết kế `EvidenceBundle` làm contract CP0.
- **Lý do:** đảm bảo phân tách nhiệm vụ rõ ràng, dễ kiểm thử, và phù hợp với kiến trúc multi-agent yêu cầu handoff evidence.
- **Bằng chứng quyết định phù hợp:** `src/coordinator.py` hiện chỉ chuyển `EvidenceBundle` cho `policy_agent`, không lộ CSV.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `Policy Agent must return PolicyResult` khi thử tích hợp với coordinator.
- **Lệnh hoặc bước tái hiện:** chạy `python -m src.main --validate-only` sau khi thêm module.
- **Nguyên nhân gốc:** module `src.agents.policy_agent` chưa tồn tại hoặc chưa trả về `PolicyResult`.
- **Cách xử lý:** tạo `policy_agent.py` và implement hàm `analyze` đúng kiểu.
- **Cách xác minh sau khi sửa:** `python -m src.main --validate-only` thành công.
- **Điều học được:** cần định nghĩa contract CP0 rõ ràng trước khi tích hợp agent mới.

## 7. Hiểu biết về luồng end-to-end

1. Dữ liệu đi từ Crossref đến vector index như thế nào?

- Trong repo này không sử dụng Crossref hay vector index; luồng end-to-end là input JSON vào, evidence agents xử lý dữ liệu Olist, Policy Agent ra quyết định, Verifier ghi output JSON.

2. Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?

- Ở bài này, evaluation set là 50 case `EC_001` tới `EC_050`; ground truth là luật nghiệp vụ `EC_POLICY_V1` và các evidence ID hợp lệ.

3. Quality checks khác freshness monitoring ở điểm nào trong bài lab?

- Quality checks ở đây kiểm tra schema, evidence tồn tại, số tiền làm tròn và số lượng ID, không phải kiểm tra độ mới của dữ liệu.

4. Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?

- Dùng cùng bộ case giúp so sánh trực tiếp hiệu quả sửa sai, tránh sai lệch do khác dữ liệu.

5. Repair được xem là thành công dựa trên artifact và metric nào?

- Success khi đầu ra `output/EC_XXX.json` được Verifier chấp nhận, không có lỗi schema, và `case_status` cùng `recommended_refund_brl` tuân thủ rules.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Đoàn Minh Hiếu
**Ngày xác nhận:** 2026-08-05
