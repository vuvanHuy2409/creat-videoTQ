# Auto-Translade-video 🎬🤖

Hệ thống tự động chuyển ngữ và lồng tiếng video đa nền tảng (**YouTube / Douyin / TikTok / Video local**) sang **Tiếng Việt** hoặc **Tiếng Nhật**. Hệ thống có cả giao diện đồ họa (GUI) trực quan và giao diện dòng lệnh (CLI), hỗ trợ tự động tách nhạc nền (BGM), dịch tự động qua API (OpenAI/Gemini/Skill) và lồng tiếng khớp timeline.

---

## 🌟 Chức năng nổi bật & Quy trình 5 bước tự động

Hệ thống hoạt động theo quy trình 5 bước khép kín. Trên giao diện GUI, khi nhập link video và bắt đầu, hệ thống sẽ **tự động chạy xuyên suốt từ Bước 1 đến Bước 5** mà không cần dừng lại can thiệp:

1. **Bước 1: Nguồn Video (Download)**
   - Hỗ trợ tải video tự động từ link YouTube, Douyin (bao gồm bypass captcha/browser profile qua Playwright), TikTok và hơn 1000+ website hỗ trợ bởi `yt-dlp`.
   - Hỗ trợ tải hoặc kéo thả trực tiếp file video local (`.mp4`, `.mkv`, `.avi`, `.mov`).
   - Tự động cắt video theo mốc thời gian (Trim start/end).

2. **Bước 2: Tách Âm Thanh & Nhận Diện Giọng Nói (ASR)**
   - **Tách BGM**: Sử dụng **Demucs** (AI tách lời bài hát/nhạc nền chất lượng cao) hoặc chế độ **Audio Ducking** (giảm âm lượng giọng cũ) để giữ nguyên nhạc nền và hiệu ứng âm thanh gốc.
   - **Cắt xén phụ đề cứng (Subtitle Crop)**: Cho phép kéo chọn vùng chứa phụ đề gốc trên video để xóa/che trước khi ghi phụ đề mới.
   - **ASR**: Sử dụng **Azure Speech Service** để chuyển lời nói gốc thành dữ liệu phụ đề mốc thời gian (mã hóa JSON & SRT).

3. **Bước 3: Dịch Thuật Tự Động (AI Translation)**
   - **Tự động dịch API**: Tự động chuyển phụ đề từ ngôn ngữ gốc (Anh, Trung, Nhật, ...) sang Tiếng Việt / Tiếng Nhật qua API OpenAI / Gemini.
   - **Tự động chuyển bước**: Sau khi dịch xong bằng API, hệ thống **ngay lập tức chuyển sang Bước 4 & Bước 5** mà không bắt người dùng dừng lại thao tác thủ công.
   - **Chỉnh sửa thủ công (Tùy chọn)**: Cho phép xem trước và chỉnh sửa lại câu từ trong file JSON dịch nếu muốn.

4. **Bước 4: Tổng Hợp Giọng Nói (TTS) & Ghép Video (Render)**
   - **Lồng tiếng Việt**: Kết nối API **LucyLab / VietSpeech** với các giọng đọc nam/nữ AI tự nhiên (`male`, `female`).
   - **Lồng tiếng Nhật**: Kết nối **Azure Neural Voice** (mặc định giọng `ja-JP-KeitaNeural`).
   - **Khớp Timeline (Time-sync)**: Tự động điều chỉnh tốc độ đọc (atempo) khớp chính xác từng phân đoạn âm thanh gốc.
   - **Subtitles Overlay**: Tự động gắn phụ đề dịch thẳng vào video kết xuất.

5. **Bước 5: Hoàn Tất & Xuất Kết Quả**
   - Kết xuất video lồng tiếng thành phẩm (`dubbed_video.mp4`).
   - Phát âm thanh thông báo và hỗ trợ xem video trực tiếp, mở thư mục kết quả.
   - Tự động tạo tiêu đề, mô tả, thẻ tag YouTube và gợi ý thumbnail bằng Gemini AI.

---

## 🖥 Giao Diện Đồ Họa (GUI App)

Ứng dụng có giao diện Đồ họa đẹp mắt và dễ sử dụng bằng Python Tkinter (`app_gui.py`):

```bash
python app_gui.py
```

### Các tính năng trên GUI:
- **Tự động hóa hoàn toàn**: Nhập link video -> Nhấn bắt đầu -> Nhận video thành phẩm.
- **Quản lý dự án**: Tự động lưu tiến độ vào `output/`, cho phép tải lại dự án cũ để làm tiếp bất cứ lúc nào.
- **Bộ chọn dải phụ đề trực quan**: Kéo thả trên khung xem trước video để đặt vị trí xóa phụ đề cũ.
- **Cấu hình giọng đọc & ngôn ngữ linh hoạt**.

---

## 🛠 Cài Đặt & Cấu Hình

### 1. Yêu cầu môi trường
- Python 3.10 trở lên
- `ffmpeg` đã cài đặt và được thêm vào PATH
- Tài khoản API:
  - **Azure Speech Service** (bắt buộc cho ASR & TTS Tiếng Nhật)
  - **LucyLab API** (bắt buộc nếu lồng Tiếng Việt) – [lucylab.io](https://lucylab.io)
  - **OpenAI API Key / Google Gemini API Key** (dịch thuật tự động và tạo metadata)

### 2. Cài đặt thư viện

```bash
# Clone repository
git clone https://github.com/vuvanHuy2409/repo_video.git
cd repo_video

# Cài đặt thư viện Python
pip install -r requirements.txt

# Cài đặt trình duyệt Playwright (dùng để tải Douyin/TikTok)
playwright install chromium

# Tạo file môi trường cấu hình
cp .env.example .env
```

### 3. Cấu hình file `.env`

Mở file `.env` và điền các khóa API tương ứng:

```ini
# --- Azure Speech (ASR + Nhật TTS) ---
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=japaneast

# --- Vietnamese TTS (LucyLab) ---
VIETNAMESE_API_KEY=your_lucylab_api_key
LUCYLAB_API_URL=https://api.lucylab.io/json-rpc
VIETNAMESE_VOICEID_MALE=2LLtWibYKJaiLFeqVkzPGY
VIETNAMESE_VOICEID_FEMALE=mhsL3CPLxmLYdSTKp3GANz

# --- AI Translator (OpenAI / Gemini) ---
OPENAI_API_KEY=your_openai_api_key
GOOGLE_API_KEY=your_google_api_key

# --- Cấu hình chung ---
OUTPUT_DIR=./output
DEFAULT_SOURCE_LANG=zh-CN
AUDIO_SLOW_FACTOR=0.82
```

---

## 💻 Sử Dụng Qua Dòng Lệnh (CLI)

Nếu không dùng GUI, bạn có thể chạy các script dòng lệnh trực tiếp:

### 1. Lồng tiếng Việt (`pipeline_vi.py`)

```bash
# Tải và lồng tiếng từ Douyin / YouTube
python pipeline_vi.py --url "https://v.douyin.com/..." --source-lang zh --voice male

# Xử lý file video local
python pipeline_vi.py --file input/video.mp4 --source-lang zh --voice female

# Tùy chỉnh chế độ nhạc nền (BGM)
python pipeline_vi.py --url ... --bg-mode demucs     # Tách AI bằng Demucs (Mặc định)
python pipeline_vi.py --url ... --bg-mode duck       # Giảm âm lượng nhạc gốc
python pipeline_vi.py --url ... --bg-mode none       # Loại bỏ hoàn toàn nhạc gốc
```

### 2. Lồng tiếng Nhật (`pipeline.py`)

```bash
python pipeline.py --url "https://www.youtube.com/watch?v=..." --source-lang en --voice ja-JP-KeitaNeural
```

### 3. Tiếp tục dự án chưa hoàn thành (`--resume`)

```bash
python pipeline_vi.py --resume "output/VN/20260726_120000_vi" --file input/video.mp4
```

### 4. Xử lý hàng loạt (Batch Processing)

```bash
# Đọc danh sách video từ Excel để lồng Tiếng Việt
python batch_run_vi.py --excel output/video_link.xlsx

# Đọc từ file JSON
python batch_run_json.py --json list_video.json
```

---

## 📂 Cấu Trúc Thư Mục Output

Mỗi dự án sau khi xử lý sẽ tạo ra một thư mục riêng trong `output/`:

```
output/VN/20260726_120000_vi/
├── <video_id>.mp4                  # Video gốc đã download
├── original_audio.wav              # File âm thanh gốc (16kHz mono)
├── no_vocals.wav                   # Nhạc nền đã tách giọng (Demucs)
├── vocals.wav                      # Giọng nói gốc đã tách
├── transcript_original.json        # Phụ đề nhận diện từ Azure ASR
├── transcript_original.srt         # File srt gốc
├── transcript_vi.json              # Phụ đề bản dịch Tiếng Việt (hoặc transcript_jp.json)
├── transcript_vi.srt               # File srt bản dịch
├── segments/                       # Thư mục chứa audio TTS của từng câu
│   ├── seg_000.wav
│   └── seg_001.wav
├── dubbed_audio.wav                # Âm thanh hoàn chỉnh (Giọng TTS + Nhạc BGM)
└── dubbed_video.mp4                # Video lồng tiếng thành phẩm cuối cùng
```

---

## 📜 Giấy Phép (License)

Dự án được phát hành theo giấy phép [MIT License](LICENSE).
