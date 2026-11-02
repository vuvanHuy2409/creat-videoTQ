# Lưu trữ SQLite và thư viện giọng đọc

Ứng dụng dùng SQLite có sẵn trong Python, không cần cài máy chủ cơ sở dữ liệu. Tệp mặc định là `data/studio.sqlite3`. Có thể đổi bằng `STUDIO_DB_PATH` trong `.env` hoặc môi trường chạy; đường dẫn tương đối tính từ thư mục dự án.

## Đánh giá dữ liệu cần lưu

Có **7 nhóm nghiệp vụ**, được tổ chức thành **10 bảng**:

| Nhóm | Bảng | Dữ liệu và mục đích |
| --- | --- | --- |
| 1. Thư viện giọng | `voices` | Tên, ngôn ngữ, nhà cung cấp, Voice ID, giới tính, ghi chú, trạng thái ẩn; tham chiếu ổn định cho giọng tự thêm. |
| 2. Cài đặt | `app_settings` | Giọng mặc định Việt/Nhật, tốc độ cơ bản/tối đa, phông/cỡ chữ, đường dẫn và cấu hình xử lý không bí mật. |
| 3. Dự án | `projects` | Thư mục, ngôn ngữ, trạng thái và dấu vết nội dung nguồn/bản dịch để đồng bộ. |
| 4. Lời thoại và biên tập | `transcript_segments`, `translation_drafts`, `translation_revisions` | Mốc thời gian, lời gốc, bản dịch; bản nháp chưa hoàn chỉnh và các phiên bản để khôi phục. |
| 5. Lịch sử thực thi | `runs`, `run_events` | Lần chạy, tiến trình, lựa chọn khi chạy, bước xử lý, lỗi và kết quả. |
| 6. Tệp và cache | `artifacts` | Đường dẫn, loại tệp, kích thước, thời gian sửa và metadata của video/audio/phụ đề/cache TTS. |
| 7. Nghe thử | `voice_previews` | Giọng được chọn và giọng thực tế, câu mẫu, tốc độ, thời lượng, đường dẫn WAV và số lần sử dụng cache. |

Video, âm thanh, phông và phụ đề vẫn nằm trên đĩa; SQLite lưu thông tin và đường dẫn. Khóa API vẫn được đọc từ `.env`/môi trường, không đưa vào bảng cài đặt. Metadata và thông báo lỗi đi qua bộ lọc thông tin bí mật trước khi ghi.

## Thêm và dùng giọng đọc

1. Mở **Cài đặt → Thư viện giọng đọc** hoặc nút thư viện trong phòng lồng tiếng.
2. Bấm **Thêm mới**; nhập tên dễ nhớ, ngôn ngữ, dịch vụ, Voice ID và ghi chú nếu cần.
3. Bấm **Lưu giọng vào SQLite**, chọn giọng rồi **Nghe thử** để đánh giá với tốc độ mong muốn.
4. Đặt làm mặc định hoặc chọn giọng tại phòng lồng tiếng. Lựa chọn mặc định Việt và Nhật được lưu riêng.

LucyLab hỗ trợ tiếng Việt theo tích hợp hiện tại. Azure hỗ trợ tiếng Việt và Nhật; Voice ID phải khớp ngôn ngữ (`vi-VN-…` hoặc `ja-JP-…`). Đây là đăng ký giọng có sẵn tại nhà cung cấp; cần Voice ID hợp lệ và API đã cấu hình để tổng hợp âm thanh.

Giọng tự thêm có mã ổn định dạng `voice:<id>`. Sửa tên/Voice ID giữ nguyên mã; thay Voice ID làm mới cache nghe thử/TTS tương ứng. Không cho thêm trùng tên trong cùng ngôn ngữ hoặc trùng Voice ID trong cùng dịch vụ/ngôn ngữ. Ẩn giọng chỉ bỏ khỏi danh sách chọn và gỡ mặc định; có thể chọn bản ghi đã ẩn rồi lưu để khôi phục. Giọng có sẵn được quản lý qua cài đặt dịch vụ.

CLI nhận cùng tham chiếu đã lưu, ví dụ:

```bash
python pipeline_vi.py --file input/video.mp4 --voice 'voice:<id>'
python pipeline.py --file input/video.mp4 --voice 'voice:<id>'
```

Thay `<id>` bằng mã thật trong cơ sở dữ liệu. Các lựa chọn cũ như `male`, `female`, `ja-JP-KeitaNeural` tiếp tục hoạt động. Khi không truyền `--voice`, CLI dùng giọng mặc định đã lưu.

## Đồng bộ và khôi phục bản dịch

Khi mở ứng dụng, dữ liệu cũ được nhập từ `.env`, thư mục dự án, JSON và cache nghe thử. Lệnh nhập thủ công có thể chạy nhiều lần:

```bash
python -m src.legacy_import --backup
```

Lệnh tạo bản sao SQLite trong `data/backups/` trước khi nhập, không sửa JSON hay media gốc. Có thể thao tác tương tự trong **Cài đặt → Dữ liệu SQLite & sao lưu → Nhập dự án từ tệp**. Lịch sử từ `report.json` được ghi rõ là nhập báo cáo cũ, không giả lập một lần chạy mới.

Bản nháp được lưu sau khi ngừng gõ khoảng 0,9 giây và khi rời màn hình/đóng ứng dụng bình thường. JSON đang viết dở vẫn được giữ. Nếu tệp bản dịch đổi bên ngoài trong lúc có bản nháp chưa áp dụng, bản nháp được ưu tiên và nội dung tệp được giữ thành phiên bản trong lịch sử. **Lịch sử bản dịch** đưa phiên bản được chọn trở lại trình soạn thảo; không tự ghi đè tệp xuất hay tự chạy TTS. Bấm bắt đầu TTS mới kiểm tra bản dịch đầy đủ, ghi JSON và tiếp tục quy trình hiện có.

SQLite là nguồn cài đặt không bí mật sau lần nhập đầu. Giao diện ghi đồng bộ các cài đặt vào `.env` để tương thích; sửa riêng `.env` không thay giá trị SQLite đã có. CLI vẫn ưu tiên biến môi trường được truyền trực tiếp, ví dụ `TTS_SPEED=1.2 python pipeline_vi.py …`.

## Sao lưu và vận hành

Mở **Cài đặt → Dữ liệu SQLite & sao lưu → Sao lưu SQLite…** và chọn một tệp mới. Ứng dụng dùng API backup của SQLite, kiểm tra toàn vẹn sau khi sao lưu và không ghi đè tệp đã tồn tại. Bản sao chứa dữ liệu đã commit kể cả khi WAL đang được dùng. Bản sao SQLite không chứa media hay khóa API; muốn chuyển cả dự án cần sao chép thêm thư mục media và cấu hình tương ứng.

Để mở một bản sao, đóng các phiên ứng dụng/pipeline rồi đặt `STUDIO_DB_PATH` trỏ tới bản sao đó. Đường dẫn media được lưu tuyệt đối; nếu chuyển sang máy khác, cần giữ hoặc điều chỉnh đường dẫn dữ liệu.

Mỗi thao tác dùng kết nối riêng, bật khóa ngoại và thời gian chờ khóa 15 giây. Chế độ WAL cho phép giao diện và pipeline làm việc với cùng cơ sở dữ liệu. Khi mở ứng dụng, lần chạy có PID không còn tồn tại được đánh dấu gián đoạn; tệp đầu ra và cơ chế resume hiện tại vẫn được giữ. Tệp runtime và thư mục backup đã được loại khỏi Git.

Tham khảo: [SQLite WAL](https://www.sqlite.org/wal.html), [Python SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
