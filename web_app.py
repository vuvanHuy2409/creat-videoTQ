import os
import re
import json
import asyncio
import subprocess
import threading
import sys
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Lấy cài đặt từ config
from config import CONTENT_MODEL_ID

BASE_DIR = Path(__file__).parent.resolve()
ENV_PATH = BASE_DIR / ".env"

def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

def save_env(new_data):
    existing_lines = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    written = set()
    out = []
    for line in existing_lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=")[0].strip()
            if k in new_data:
                out.append(f"{k}={new_data[k]}")
                written.add(k)
            else:
                out.append(line)
        else:
            out.append(line)
    for k, v in new_data.items():
        if k not in written:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")

app = FastAPI(title="Auto-Translade-Video Web App")

# Thư mục web (Frontend)
WEB_DIR = Path(__file__).parent / "web"
WEB_DIR.mkdir(exist_ok=True)

# Mount thư mục web để phục vụ HTML/CSS/JS
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# Biến toàn cục để stream log
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()


@app.get("/")
async def get_index():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Không tìm thấy thư mục web/index.html</h1>")


@app.get("/api/config")
async def get_config():
    return load_env()


class ConfigUpdate(BaseModel):
    settings: Dict[str, str]

@app.post("/api/config")
async def update_config(data: ConfigUpdate):
    save_env(data.settings)
    return {"status": "success"}


@app.post("/api/test-gemini")
async def test_gemini(data: Dict[str, str]):
    api_key = data.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(status_code=400, content={"error": "Thiếu API Key"})
    
    try:
        # Chuẩn mới: Truyền thẳng qua HTTP REST
        if api_key.startswith("AQ."):
            import urllib.request
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONTENT_MODEL_ID}:generateContent"
            payload = {"contents": [{"parts": [{"text": "Hãy trả lời: 'API hoạt động rất tốt!' bằng tiếng Việt."}]}]}
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'))
            req.add_header("Content-Type", "application/json")
            req.add_header("x-goog-api-key", api_key)
            
            with urllib.request.urlopen(req, timeout=15) as res:
                resp_data = json.loads(res.read().decode('utf-8'))
                ai_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
                return {"message": f"Gemini API (Chuẩn mới) phản hồi thành công!\n\nAI trả lời: {ai_text}"}
        else:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=CONTENT_MODEL_ID,
                contents="Hãy trả lời: 'API hoạt động rất tốt!' bằng tiếng Việt.",
            )
            return {"message": f"Gemini API phản hồi thành công!\n\nAI trả lời: {response.text}"}
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, 'read'):
            err_msg += "\n" + e.read().decode('utf-8')
        return JSONResponse(status_code=500, content={"error": f"Lỗi: {err_msg}"})


@app.get("/api/projects")
async def get_projects():
    # Quét thư mục output
    output_dir = BASE_DIR / "output"
    if not output_dir.exists():
        return {"projects": []}
        
    projs = []
    for d in output_dir.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            video_file = None
            for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
                p = d / f"video{ext}"
                if p.exists():
                    video_file = p.name
                    break
            
            vi_json = (d / "transcript_vi.json").exists()
            dubbed = (d / "dubbed_video.mp4").exists()
            
            projs.append({
                "name": d.name,
                "path": str(d.absolute()),
                "has_video": video_file is not None,
                "has_vi_json": vi_json,
                "has_dubbed": dubbed
            })
    return {"projects": sorted(projs, key=lambda x: x["name"], reverse=True)}


class RunASRRequest(BaseModel):
    video_path: str
    target_lang: str
    burn_subtitles: bool
    ocr_replace: bool
    ai_blur: bool

def run_process_and_stream(cmd, env):
    async def _stream():
        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True, 
            cwd=str(BASE_DIR), 
            env=env,
            bufsize=1,
            universal_newlines=True
        )
        
        for line in proc.stdout:
            await manager.broadcast(line)
        
        proc.wait()
        await manager.broadcast(f"\n--- TIẾN TRÌNH KẾT THÚC VỚI MÃ {proc.returncode} ---\n")
    
    loop = asyncio.get_event_loop()
    loop.create_task(_stream())


@app.post("/api/start-asr")
async def start_asr(req: RunASRRequest):
    target = req.target_lang
    script_name = "pipeline_vi.py" if "Việt" in target else "pipeline.py"
    
    cmd = [sys.executable, str(BASE_DIR / script_name), "--file", req.video_path, "--voice", "female"]
    if req.burn_subtitles:
        cmd += ["--burn-subtitles"]
    if req.ocr_replace:
        cmd += ["--ocr-replace"]
    if req.ai_blur:
        cmd += ["--ai-blur"]

    await manager.broadcast(f"$ {' '.join(cmd)}\n")
    
    env = {**os.environ, **load_env()}
    env["PYTHONUNBUFFERED"] = "1"
    run_process_and_stream(cmd, env)
    return {"status": "started"}


class RunTTSRequest(BaseModel):
    work_dir: str
    target_lang: str
    voice: str
    burn_subtitles: bool
    ocr_replace: bool
    ai_blur: bool
    subtitle_size: str
    transcript_json: str

@app.post("/api/start-tts")
async def start_tts(req: RunTTSRequest):
    work_dir = Path(req.work_dir)
    if not work_dir.exists():
        return JSONResponse(status_code=400, content={"error": "Không tìm thấy thư mục dự án"})
        
    try:
        data = json.loads(req.transcript_json)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Lỗi định dạng JSON: {e}"})
        
    # Ghi file json
    target = req.target_lang
    is_vi = "Việt" in target
    trans_file_name = "transcript_vi.json" if is_vi else "transcript_jp.json"
    vi_path = work_dir / trans_file_name
    vi_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # Tìm video
    src = ""
    for f in work_dir.iterdir():
        if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and not f.name.startswith("dubbed"):
            src = str(f)
            break
            
    script_name = "pipeline_vi.py" if is_vi else "pipeline.py"
    cmd = [sys.executable, str(BASE_DIR / script_name), "--resume", str(work_dir), "--voice", req.voice]
    if src:
        cmd += ["--file", src]
    if req.burn_subtitles:
        cmd += ["--burn-subtitles"]
    if req.ocr_replace:
        cmd += ["--ocr-replace"]
    if req.ai_blur:
        cmd += ["--ai-blur"]
        
    cmd += ["--subtitle-size", req.subtitle_size]

    await manager.broadcast(f"$ {' '.join(cmd)}\n")
    
    env = {**os.environ, **load_env()}
    env["PYTHONUNBUFFERED"] = "1"
    run_process_and_stream(cmd, env)
    return {"status": "started"}

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
