# Báo cáo cá nhân — K3 Day 9 Multi-Agent

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
|---|---|
| Họ và tên | Lưu Quang Nhật |
| MSSV | 2A202601920 |
| Lớp | K3 |
| Vai trò | Order & Seller Agent |
| Ngày hoàn thành | 05/08/2026 |

## 2. Phần việc thực hiện

Tôi phụ trách xây dựng Order & Seller Tool và Order & Seller Agent.

- Đọc dữ liệu từ `olist_orders_dataset.csv`, `olist_order_items_dataset.csv` và `olist_sellers_dataset.csv`.
- Tìm order, item và seller theo `claimed_order_id`.
- So sánh `order_delivered_carrier_date` với `shipping_limit_date` để xác định seller có bàn giao trễ hay không.
- Sinh các evidence ID hợp lệ từ dữ liệu CSV.
- Xây dựng agent sử dụng model local `qwen2.5:7b-instruct` qua Ollama.
- Kiểm tra kết quả model để ngăn ID không tồn tại hoặc kết luận sai dữ liệu.

Các file chính:

- `src/tools/order_seller_tool.py`
- `src/agents/order_seller_agent.py`

## 3. Input và output

Input của agent là một case có `claimed_order_id`.

Output bàn giao gồm:

- Trạng thái order.
- Danh sách item và seller liên quan.
- Cờ `seller_handoff_late`.
- Danh sách seller bàn giao trễ.
- Các evidence ID đã được xác minh.

Kết quả này được Coordinator chuyển cho Policy Agent để đưa ra quyết định cuối cùng.

## 4. Cách triển khai

Tool lọc dữ liệu chính xác từ CSV và thực hiện phép so sánh timestamp. Agent gọi tool, chọn các item và seller liên quan, sau đó trả kết quả theo `OrderSellerEvidence`.

Evidence ID được Python dựng trực tiếp từ kết quả tool thay vì để model tự nhập lại. Cách này tránh model viết sai các ID dài.

## 5. Kiểm thử

- Tool đã chạy thành công trên toàn bộ 99.441 order trong dataset.
- Agent được chạy bằng Ollama trên 50 case chính thức.
- Lần chạy đầu có 49/50 case pass; `EC_015` bị model viết sai một seller evidence ID.
- Sau khi chuyển sang sinh evidence bằng code, `EC_015` và các nhánh giao trễ, thiếu timestamp, không có item đều pass khi chạy lại bằng model thật.

## 6. Điều học được

LLM phù hợp để điều phối và chọn thông tin liên quan, nhưng các phép so sánh timestamp và evidence ID cần được xử lý bằng code để đảm bảo chính xác và có thể kiểm chứng.

## 7. Cam kết

Tôi xác nhận nội dung trên phản ánh đúng phần việc đã thực hiện. Báo cáo không chứa API key, token hoặc thông tin bí mật.

**Họ và tên:** Lưu Quang Nhật  
**MSSV:** 2A202601920
