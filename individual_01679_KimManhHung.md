# Báo cáo cá nhân — Day 9: Multi-Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Kim Mạnh Hùng |
| MSSV | 2A202601679 |
| Khóa/Lớp | K3 |
| Vai trò chính | P1 — Coordinator / Data Lead |
| Ngày hoàn thành | 2026-08-05 |

## 2. Vai trò và phạm vi công việc

Tôi phụ trách P1: chốt contract đầu vào/đầu ra để các agent còn lại có thể làm
việc độc lập, sau đó điều phối, tích hợp và kiểm chứng toàn bộ luồng 50 case.
Coordinator không tự quyết định nghiệp vụ thay P2–P6; nó gọi agent, gom
evidence và lắp JSON theo contract đã chốt.

| Module/deliverable | File hoặc thành phần phụ trách | Input | Output bàn giao |
| --- | --- | --- | --- |
| Contract CP0 | `src/schemas.py` | README và input case | schema canonical cho evidence, policy và verdict |
| Data access | `src/data_loader.py` | 9 CSV Olist | index theo order, item, payment và seller |
| Điều phối | `src/coordinator.py` | InputCase, registry, handoff | OutputVerdict đã qua Verifier |
| CLI và đóng gói | `src/main.py` | `input/` | output, trace, audit, ZIP đúng 50 JSON |
| Runtime tích hợp | `config/agents.json`, `src/agent_runtime.py` | cấu hình agent/dependency | AgentRegistry và TaskGraph |
| Audit và test | `src/audit_timeline.py`, `tests/test_full_pipeline.py` | handoff và output | timeline HTML/JSON, test end-to-end |

Ngoài phạm vi P1 ban đầu, tôi hỗ trợ tích hợp an toàn đóng góp P2/P3/P4:
giữ các data tool của nhóm để demo tool-calling, nhưng giữ đường ra nộp bài là
deterministic nhằm bảo toàn kết quả đã kiểm chứng.

## 3. Kết quả đã bàn giao

| Nhiệm vụ | Kết quả cụ thể | Cách xác minh |
| --- | --- | --- |
| Chốt schema | Label, evidence ID, giới hạn số lượng và money rounding thống nhất | Verifier kiểm tra schema trên 50 case |
| Điều phối song song | P2/P3/P4 cùng layer; P5 chạy sau EvidenceBundle; P6 chạy sau P5 | TaskGraph và audit timeline |
| Chính sách cấu hình | Rule, root cause, action và refund strategy nằm trong `policy/EC_POLICY_V1.json` | Policy Agent và Verifier cùng đối chiếu policy |
| Cổng kiểm chứng | Không ghi output nếu evidence ID, party, tiền hoặc action không khớp | `VerificationResult(passed=True)` |
| Artifact nộp bài | ZIP chứa đúng `output/EC_001.json` đến `output/EC_050.json` | kiểm tra `namelist()` của ZIP |

Bản nộp đã được kiểm tra đủ 50 file output và đạt kết quả chấm 100 điểm.

## 4. Giải thích phần kỹ thuật

### Vấn đề cần giải quyết

Bài toán không chỉ yêu cầu phân loại khiếu nại giao hàng. Mỗi kết quả phải có
bằng chứng truy ngược được từ CSV, số tiền chính xác đến hai chữ số thập phân,
và JSON đúng schema. Một prompt LLM duy nhất có thể hallucinate mã seller,
payment hoặc refund; vì vậy phần khó của P1 là tạo contract đủ chặt để các
agent độc lập vẫn lắp được vào pipeline chung.

### Cách triển khai

1. `OlistDataLoader` đọc CSV một lần và tạo index. Agent chỉ truy cập dữ liệu
   đúng domain đã được phân quyền.
2. `AgentRegistry` đọc `config/agents.json`; `TaskGraph` topo-sort dependency.
   Layer đầu gồm P2, P3, P4 và được chạy bằng `ThreadPoolExecutor`.
3. Mỗi agent trả `HandoffEnvelope`: case ID, agent name, evidence, thời gian,
   dependency và status. Coordinator tạo `EvidenceBundle` khi đủ ba handoff.
4. P5 đọc `EC_POLICY_V1.json` theo thứ tự ưu tiên để tạo `PolicyResult`.
   P1 chuyển kết quả đó thành `OutputVerdict` mà không tự thêm rule nghiệp vụ.
5. P6 đọc lại CSV và policy trước khi `main.py` ghi file. Mọi case fail bị
   chặn khỏi ZIP.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input P1 | InputCase chứa `case_id`, `claimed_order_id`, policy version |
| Input từ agent | OrderSellerEvidence, DeliveryEvidence, PaymentEvidence, PolicyResult |
| Output P1 | OutputVerdict theo README; trace, audit timeline và ZIP |
| Module phụ thuộc | Data Loader, AgentRegistry, các agent P2–P6 |
| Module dùng output | Verifier, hệ thống chấm điểm và giao diện nộp bài |
| Lỗi được xử lý | thiếu order, sai stage, handoff thiếu, verifier fail, ZIP sai 50 file |

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Hệ thống cần thể hiện multi-agent nhưng kết quả phải lặp lại
  được và đạt điểm tối đa.
- **Phương án 1:** Cho tất cả agent gọi LLM để suy luận ngày tháng, số tiền và
  quyết định. Cách này có tính trình diễn nhưng rủi ro hallucination và phụ
  thuộc model runtime.
- **Phương án 2:** Dùng agent chuyên trách deterministic cho dữ liệu/policy,
  chỉ dùng Qwen 2.5 3B sau Verifier để tạo explanation.
- **Phương án chọn:** Phương án 2 — kiến trúc hybrid multi-agent.
- **Lý do:** Mọi quyết định nộp bài đều kiểm chứng được bằng CSV và policy;
  Qwen vẫn dùng model dưới 10B nhưng không được quyền đổi facts, refund/action.
- **Bằng chứng:** 50 case pass Verifier, ZIP đúng layout và điểm chấm đạt 100.

## 6. Một lỗi/blocker đã xử lý

- **Triệu chứng:** Pull từ `main` tạo conflict ở P2/P3/P4/Policy/metadata do
  các nhánh khác chọn Qwen 7B/Ollama làm runtime trong khi pipeline đã có đường
  quyết định deterministic.
- **Nguyên nhân gốc:** Các nhánh phát triển độc lập dựa trên cùng contract,
  nhưng khác lựa chọn runtime và confidence.
- **Cách xử lý:** Merge có chọn lọc: giữ `OrderSellerTool` và `DeliveryTool`
  để tái sử dụng/demo; giữ agent deterministic, policy config và Qwen 3B ở
  đường nộp bài. Sau merge chạy lại compile, full-flow test và kiểm tra ZIP.
- **Kết quả:** Tích hợp được đóng góp của nhóm mà không làm thay đổi output
  đã đạt 100 điểm.

## 7. Hiểu biết về luồng end-to-end

`main.py` đọc 50 file input và tạo InputCase. Coordinator lấy agent từ registry,
chạy P2/P3/P4 song song để thu evidence từ các domain khác nhau. Khi đủ
evidence, P5 chọn rule trong `EC_POLICY_V1.json`; Coordinator lắp verdict;
P6 kiểm chứng độc lập rồi mới ghi JSON. Verdict pass được đưa vào ZIP với
prefix `output/`. Song song với artifact nộp bài, hệ thống tạo HandoffEnvelope,
audit timeline và Evidence Receipt để có thể giải thích lại quyết định.

Qwen/Qwen2.5-3B-Instruct chỉ được phép chạy sau Verifier để tạo lời giải thích.
`confidence = 1.0` là độ chắc chắn của rule đã được kiểm chứng bằng CSV, không
phải xác suất dự đoán cảm tính của LLM.

## 8. Cách chạy và xác minh

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

Kết quả mong đợi: 3/3 test PASS, bao gồm chạy full 50 case trong thư mục tạm,
kiểm tra TaskGraph, Verifier, audit và cấu trúc ZIP.

Để kiểm tra ZIP nộp bài:

```powershell
@'
import zipfile

with zipfile.ZipFile("output_final.zip") as archive:
    names = archive.namelist()

expected = [f"output/EC_{i:03d}.json" for i in range(1, 51)]
print("PASS" if names == expected else "FAIL")
'@ | python -
```

## 9. Cam kết

- [x] Báo cáo phản ánh đúng phần việc P1 — Coordinator / Data Lead.
- [x] Có thể giải thích luồng end-to-end và contract giữa các agent.
- [x] Không ghi nhận kết quả chưa được kiểm chứng.
- [x] Không chứa API key, token hoặc secret.
- [x] Nội dung được viết riêng cho vai trò P1.

**Họ và tên:** Kim Mạnh Hùng
**Ngày xác nhận:** 2026-08-05
