# Auto-Dub Studio

Ứng dụng desktop chuyển lời thoại trong video sang **tiếng Việt hoặc tiếng Nhật**, có giao diện Tkinter và dòng lệnh. Hỗ trợ video trên máy, YouTube, TikTok và Douyin; nhận dạng lời nói bằng Azure, nhập bản dịch thủ công, tạo giọng đọc và ghép video bằng FFmpeg.

**Quy trình:** Chọn video → Nhận dạng lời nói → Điền bản dịch → Tạo giọng đọc → Xuất video.

Ứng dụng dừng sau nhận dạng để bạn kiểm tra và dịch nội dung. Azure ASR/TTS, LucyLab và Gemini (nếu bật) sử dụng tài khoản API riêng và có thể phát sinh phí của nhà cung cấp.

## 1. Chuẩn bị

- Python **3.12** và Tkinter. Môi trường phát triển hiện dùng Python 3.12 trên macOS; Windows/Linux cần kiểm tra Tkinter, FFmpeg và Azure Speech SDK tương ứng.
- FFmpeg và FFprobe trong `PATH`. Để vẽ phụ đề lên video, FFmpeg cần filter `subtitles` (libass) hoặc `drawtext`.
- Tài khoản **Azure Speech** với key và region để nhận dạng lời nói và tạo giọng đọc Azure.
- Tài khoản **LucyLab** nếu dùng giọng tiếng Việt của LucyLab. Có thể chọn giọng Azure tiếng Việt thay thế.
- Internet để gọi API, tải video và tải model EasyOCR/Demucs ở lần chạy đầu. OCR và tách nhạc nền cần thêm thời gian, RAM và dung lượng đĩa.

### macOS với Homebrew

```bash
brew install python@3.12 python-tk@3.12 ffmpeg-full
```

`ffmpeg-full` có thể không tự được thêm vào `PATH`. Chạy trước khi mở ứng dụng:

```bash
export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
python3.12 -m tkinter
ffmpeg -version
ffprobe -version
```

Lệnh Tkinter phải mở được cửa sổ mẫu. Ứng dụng cũng tự tìm FFmpeg từ Homebrew khi ghép phụ đề; các bước xử lý âm thanh vẫn cần `ffmpeg` trong `PATH`.

### Windows / Linux

Cài Python 3.12, FFmpeg/FFprobe và Tkinter cho hệ điều hành đang dùng. Trên Linux, Tkinter thường nằm trong gói `python3-tk`. Kiểm tra bằng `python -m tkinter`, `ffmpeg -version` và `ffprobe -version` trước khi tiếp tục. Các lệnh dưới đây dùng `python`; trên Linux/macOS có thể cần thay bằng `python3.12`.

## 2. Tải và cài ứng dụng

```bash
git clone --depth 1 https://github.com/vuvanHuy2409/creat-videoTQ.git
cd creat-videoTQ
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Trên **Windows PowerShell**, thay lệnh tạo/kích hoạt môi trường và sao chép cấu hình bằng:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

Sau đó chạy các lệnh `python -m pip ...` và `python -m playwright ...` như trên. Chromium dùng cho tải video Douyin; không cần mở trình duyệt khi chỉ xử lý video trên máy.

## 3. Cấu hình tài khoản

Mở `.env`, thay các giá trị mẫu bằng thông tin tài khoản của bạn:

```dotenv
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=your_azure_region
TTS_VOICE=ja-JP-KeitaNeural

# Điền nếu dùng giọng LucyLab
VIETNAMESE_API_KEY=
VIETNAMESE_VOICEID_MALE=
VIETNAMESE_VOICEID_FEMALE=

# Giọng Azure dự phòng cho tiếng Việt
AZURE_VI_TTS_VOICE=vi-VN-HoaiMyNeural

# Tuỳ chọn: tạo tiêu đề, mô tả và thumbnail bằng Gemini
GOOGLE_API_KEY=

OUTPUT_DIR=./output
DEFAULT_SOURCE_LANG=zh-CN
TTS_SPEED=1.0
TTS_OVERFLOW_MAX_SPEED=1.4
VIDEO_CRF=18
VIDEO_PRESET=medium
```

- **Azure:** key và region phải thuộc cùng tài nguyên Speech. Tiếng Việt có thể chọn trực tiếp giọng `azure:vi-VN-HoaiMyNeural` trong CLI hoặc giọng Azure trong giao diện.
- **LucyLab:** nhập key và Voice ID thực tế trong tài khoản. Có thể cấu hình thêm `VIETNAMESE_API_KEY_2` … `VIETNAMESE_API_KEY_5`; ứng dụng thử key kế tiếp khi gặp lỗi quota/xác thực phù hợp, rồi dùng Azure dự phòng nếu đã cấu hình.
- **Gemini:** chỉ dùng cho nội dung bổ sung, không phải bước dịch lời thoại. Có thể đặt `CONTENT_MODEL_ID` / `IMAGE_MODEL_ID` phù hợp với tài khoản.
- **Chất lượng:** khi chỉ ghép audio, ứng dụng sao chép luồng video. OCR/vẽ phụ đề phải mã hoá lại; `VIDEO_CRF=0` trong mẫu mặc định tạo file lossless rất lớn. Có thể đổi sang `18` trước lần mở đầu để giảm dung lượng.

Khởi chạy:

```bash
python app_gui.py
```

Lần đầu, các cài đặt không bí mật trong `.env` được nhập vào SQLite. Từ đó hãy chỉnh trong **Cài đặt**: sửa `.env` riêng sẽ không ghi đè giá trị đã lưu trong SQLite. API key vẫn được lưu tại `.env`; giữ tệp này trên máy cá nhân.

## 4. Hướng dẫn dùng giao diện

1. **Tạo dự án:** dán liên kết hoặc chọn video trên máy. Chọn ngôn ngữ nguồn, ngôn ngữ đích và giọng đọc. Dùng **Nghe thử giọng đọc** để kiểm tra giọng và tốc độ trước khi chạy.
2. **Nhận dạng lời nói:** bắt đầu xử lý và chờ tải video, tách âm thanh, nhận dạng. Nếu tải trực tuyến không thành công, có thể dùng video đã tải về máy.
3. **Nhập bản dịch:** tại **Viết bản dịch của bạn**, sao chép mẫu JSON rồi điền `text_vi` cho tiếng Việt hoặc `text_jp` cho tiếng Nhật. Giữ nguyên `id`, `text`, `start`, `end`, `duration`, số lượng và thứ tự phân đoạn. Mỗi phân đoạn cần có bản dịch không rỗng; nên viết ngắn để vừa thời lượng đọc.
4. **Tạo giọng và xuất:** bật **Vẽ cứng phụ đề lên video (Burn Subtitles)** nếu muốn chữ xuất hiện trực tiếp trên video, chỉnh phông/màu/nền rồi bấm **Tạo giọng đọc & xuất video**.
5. **Lấy kết quả:** mở video thành phẩm hoặc thư mục dự án từ màn hình hoàn tất. Dự án cũ có thể mở lại từ thư viện để sửa bản dịch và chạy tiếp.

### Phụ đề và xử lý chữ cũ

- Có 12 phông đi kèm, hỗ trợ tiếng Việt; Noto Sans JP dùng cho chữ Nhật/Trung. Chọn cỡ **12–160 px**, màu chữ/viền/nền, độ đậm nền và bảng 15 màu nhanh trong **Cài đặt → Phông phụ đề** hoặc cấu hình phụ đề riêng của dự án.
- **Xóa chữ bằng OCR và đặt phụ đề vào vị trí gốc** dùng EasyOCR và xử lý ảnh cục bộ; chức năng làm mờ chữ Trung quét dải **20–40% tính từ đáy video**. Chữ ngoài dải có thể còn nguyên; nên xem lại kết quả trước khi xuất bản.
- Không bật đồng thời hai chế độ làm sạch chữ. OCR không thể khôi phục chính xác phần nền vốn bị chữ gốc che mất.
- Tốc độ đọc cơ bản và giới hạn tăng tốc từ **0.5–2.0**. Câu quá dài sau khi đạt giới hạn có thể chồng lên câu kế tiếp; rút gọn bản dịch để cải thiện nhịp đọc.

### Giọng đọc và bản dịch đã lưu

Trong **Cài đặt → Thư viện giọng đọc**, có thể thêm Voice ID Azure/LucyLab, nghe thử và đặt giọng mặc định riêng cho Việt/Nhật. Đây là chọn giọng đã có ở nhà cung cấp. Giọng tự thêm có tham chiếu `voice:<id>` dùng được trong CLI trên cùng cơ sở dữ liệu.

Bản nháp tự lưu khi ngừng gõ và khi đóng ứng dụng bình thường. **Lịch sử bản dịch** cho phép đưa phiên bản cũ trở lại trình soạn thảo; bấm tạo giọng mới kiểm tra và áp dụng bản dịch đó.

## 5. Dùng dòng lệnh

Kích hoạt `.venv` và cấu hình `.env` trước khi chạy. Ví dụ tiếng Việt bằng Azure, dùng nhạc gốc giảm âm lượng để bắt đầu nhanh:

```bash
python pipeline_vi.py --file "/path/to/video.mp4" --source-lang zh \
  --voice azure:vi-VN-HoaiMyNeural --bg-mode duck --burn-subtitles
```

Các ví dụ khác:

```bash
# Tiếng Việt qua LucyLab (đã cấu hình key và Voice ID)
python pipeline_vi.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --source-lang en --voice male

# Tiếng Nhật qua Azure
python pipeline.py --file "/path/to/video.mp4" --source-lang zh --voice ja-JP-KeitaNeural

# Xem toàn bộ tuỳ chọn
python pipeline_vi.py --help
python pipeline.py --help
```

Lần đầu pipeline dừng ở bước dịch và ghi **`TRANSLATE_PENDING.txt` trong thư mục kết quả**. Đây là trạng thái chờ bạn dịch. Sao chép `transcript_original.json` thành `transcript_vi.json` hoặc `transcript_jp.json` rồi thêm trường dịch vào từng phân đoạn, ví dụ:

```json
[
  {
    "id": 0,
    "text": "Hello everyone.",
    "start": 0.0,
    "end": 2.0,
    "duration": 2.0,
    "text_vi": "Xin chào mọi người."
  }
]
```

Ví dụ chỉ minh hoạ cấu trúc; hãy dùng đúng ID và timeline trong transcript của video. Sau khi hoàn thành toàn bộ bản dịch, chạy tiếp với **đúng thư mục dự án** được in trong log:

```bash
python pipeline_vi.py --resume "output/VN/THU_MUC_DU_AN" \
  --file "/path/to/video.mp4" --source-lang zh \
  --voice azure:vi-VN-HoaiMyNeural --bg-mode duck --burn-subtitles
```

Nhánh Nhật dùng `pipeline.py`, `transcript_jp.json` và trường `text_jp`. Chỉ dùng **một** nguồn `--file` hoặc `--url`; khi resume nên dùng lại video nguồn đã tải. Truyền lại các lựa chọn xử lý mong muốn như giọng, nhạc nền và burn phụ đề.

| Tuỳ chọn | Ý nghĩa |
| --- | --- |
| `--bg-mode demucs` | Tách giọng gốc, giữ nhạc/hiệu ứng; mặc định, xử lý lâu hơn. Nếu tách thất bại, pipeline có thể dùng nền im lặng; kiểm tra log. |
| `--bg-mode duck` | Giảm âm lượng toàn bộ audio gốc; vẫn có thể nghe lời gốc nhỏ bên dưới. |
| `--bg-mode none` | Chỉ giữ giọng đọc mới. |
| `--skip-video` | Chỉ xuất audio và phụ đề. |
| `--burn-subtitles` | Vẽ phụ đề vào video. |
| `--ocr-replace` | Làm sạch chữ Trung bằng OCR ở nhánh tiếng Việt. |
| `--ai-blur` | Làm mờ chữ Trung bằng EasyOCR. |
| `--subtitle-font "Phudu" --subtitle-size 56` | Chọn phông đi kèm và cỡ chữ. |
| `--output-dir "output/custom"` | Đổi thư mục đầu ra. |

### Xử lý danh sách video

```bash
cp list_video.example.json list_video.json
# Sửa video_url, voice_type và giữ status là "waiting" cho video cần chạy
python batch_run_json.py --json list_video.json --source-lang zh

# Hoặc dùng Excel: hàng 1 là tiêu đề, cột A là URL, cột B trạng thái để trống
python batch_run_vi.py --excel "/path/to/video_links.xlsx" --source-lang zh --voice male
```

Batch cũng dừng từng video ở `translation_pending` / `TRANSLATION_PENDING`. Điền bản dịch rồi tiếp tục từng dự án bằng GUI hoặc `--resume`; chạy lại batch không tự hoàn tất các mục đang chờ dịch. `batch_run.py` là nhánh Excel tiếng Nhật.

Các tiện ích: `download_video.py` tải video; `get_youtube_script.py` lấy phụ đề YouTube có sẵn; `run_content_gen.py` tạo nội dung bổ sung từ transcript. Dùng `--help` với hai tiện ích đầu để xem tham số.

## 6. Kết quả và sao lưu

Thư mục mỗi dự án thường gồm:

```text
output/VN/<du_an>/
├── <video_nguon>.mp4
├── original_audio.wav
├── vocals.wav / no_vocals.wav       # Khi tách Demucs thành công
├── transcript_original.json
├── TRANSLATE_PENDING.txt           # Hướng dẫn bước dịch
├── transcript_vi.json / .srt        # Nhánh Nhật dùng transcript_jp
├── subtitle_style.json
├── segments/                       # Giọng đọc từng phân đoạn
├── dubbed_audio.wav
├── dubbed_video.mp4
└── report.json
```

- `data/studio.sqlite3`: cài đặt, thư viện giọng, bản nháp, lịch sử và thông tin dự án; tự tạo khi chạy.
- **Cài đặt → Dữ liệu SQLite & sao lưu → Sao lưu SQLite…**: tạo bản sao cơ sở dữ liệu an toàn, kể cả khi SQLite dùng WAL.
- Sao lưu cả **thư mục output, bản sao SQLite và `.env`** để giữ đầy đủ dự án. Bản sao SQLite không chứa video/audio hoặc API key. Khi chuyển máy, kiểm tra lại đường dẫn media.
- Video, audio, database, bản sao lưu, cookie và `playwright_profile/` là dữ liệu trên máy; đã được loại khỏi Git. Chi tiết kỹ thuật: [Lưu trữ SQLite](docs/sqlite-storage.md).

## 7. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Thiếu `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | Điền `.env` ở gốc dự án rồi mở lại ứng dụng. |
| Không mở được giao diện / thiếu `_tkinter` | Cài Tkinter tương ứng phiên bản Python; chạy `python -m tkinter` để kiểm tra. |
| Không tìm thấy Chromium | Chạy `python -m playwright install chromium` trong `.venv`. |
| Douyin yêu cầu đăng nhập/xác minh | Hoàn thành trong cửa sổ trình duyệt nếu xuất hiện rồi thử lại, hoặc chọn video trên máy. |
| Không tìm thấy FFmpeg/FFprobe | Thêm thư mục chứa hai binary vào `PATH`, mở lại terminal và ứng dụng. |
| Không vẽ được phụ đề | Kiểm tra bản FFmpeg thực sự được dùng có filter phụ đề bằng lệnh bên dưới. Có thể đặt `FFMPEG_BIN` tới bản đầy đủ trong môi trường chạy. |
| LucyLab hết credit / sai Voice ID | Kiểm tra tài khoản, key và Voice ID; chọn giọng Azure hoặc cấu hình Azure dự phòng. |
| Azure TTS timeout | Thử lại; kiểm tra kết nối và các biến `AZURE_VI_TTS_*` trong `.env.example`. |
| Pipeline chờ bản dịch | Điền đủ `text_vi`/`text_jp`, giữ nguyên timeline, sau đó chạy `--resume`. |
| Video quá lớn | Đặt `VIDEO_CRF=18` trong `.env` trước lần mở đầu; nếu đã có SQLite, truyền biến môi trường khi chạy như ví dụ bên dưới. `0` là lossless. |
| Sửa `.env` nhưng lựa chọn cũ vẫn còn | Cập nhật trong Cài đặt vì các giá trị không bí mật đã lưu ở SQLite. |

Ghi đè chất lượng cho lần chạy trên macOS/Linux: `VIDEO_CRF=18 python app_gui.py`. PowerShell: `$env:VIDEO_CRF="18"; python app_gui.py`. Biến môi trường được truyền trực tiếp có ưu tiên cao hơn SQLite.

Kiểm tra filter bằng đúng binary mà bước render chọn:

```bash
python -c "from src.video_merger import FFMPEG_BIN, _has_filter; print(FFMPEG_BIN); print('subtitles:', _has_filter('subtitles')); print('drawtext:', _has_filter('drawtext'))"
```

## 8. Phát triển và kiểm thử

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Test dùng SQLite tạm riêng và mô phỏng API. Một số test cần Tkinter/FFmpeg; môi trường không có giao diện hoặc filter cần thiết có thể bỏ qua các kiểm tra tương ứng. Test không thay thế việc kiểm tra tải video và API thật với tài khoản của bạn.

Mã chính: `app_gui.py` và các tệp `*_ui.py` cho desktop, `pipeline_vi.py` / `pipeline.py` cho CLI, `src/` cho xử lý và lưu trữ, `tests/` cho kiểm thử. Phông đi kèm và giấy phép nằm trong [assets/fonts](assets/fonts/README.md). Bộ huấn luyện YOLO, kết quả thử nghiệm, font trùng và giao diện web cũ đã được bỏ khỏi bản phân phối desktop.

## Giấy phép

Mã nguồn theo [MIT License](LICENSE). Phông đi kèm có giấy phép SIL Open Font License riêng trong `assets/fonts/licenses/`.
