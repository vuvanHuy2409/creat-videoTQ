let currentWorkDir = "";
let ws = null;

document.addEventListener('DOMContentLoaded', () => {
    // Tab Switching
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(item.getAttribute('data-tab'));
        });
    });

    // Initialize WebSockets for logs
    connectWebSocket();
    loadProjects();
});

function switchTab(tabId) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    
    const navItem = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
    if (navItem) navItem.classList.add('active');
    
    document.getElementById(`tab-${tabId}`).classList.add('active');

    if (tabId === 'dashboard') loadProjects();
    if (tabId === 'settings') loadSettings();
}

function goToStep(step) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.step-pane').forEach(p => p.classList.remove('active'));
    
    document.getElementById(`step-nav-${step}`).classList.add('active');
    document.getElementById(`step-pane-${step}`).classList.add('active');
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.borderColor = isError ? 'var(--error)' : 'var(--border)';
    if(isError) toast.style.color = 'var(--error)';
    else toast.style.color = 'var(--text)';
    
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

function connectWebSocket() {
    ws = new WebSocket(`ws://${window.location.host}/ws/logs`);
    ws.onmessage = (event) => {
        // Appends to both ASR and TTS terminal depending on active step
        const asrLogs = document.getElementById('terminal-asr');
        const ttsLogs = document.getElementById('terminal-tts');
        
        const activePane = document.querySelector('.step-pane.active').id;
        if (activePane === 'step-pane-2') {
            asrLogs.textContent += event.data;
            asrLogs.scrollTop = asrLogs.scrollHeight;
        } else if (activePane === 'step-pane-4') {
            ttsLogs.textContent += event.data;
            ttsLogs.scrollTop = ttsLogs.scrollHeight;
        }

        // Auto parse work dir
        const matchAbs = event.data.match(/(?:Output folder|Resuming work directory):\s*(.+)/);
        if (matchAbs) {
            currentWorkDir = matchAbs[1].trim();
            console.log("Work dir found:", currentWorkDir);
        }

        // Checking for completion
        if (event.data.includes("TIẾN TRÌNH KẾT THÚC VỚI MÃ 0")) {
            if (activePane === 'step-pane-2') {
                document.getElementById('btn-next-step3').disabled = false;
                showToast("Nhận diện & Dịch hoàn tất!");
                // Pretend to fetch JSON
                document.getElementById('transcript_json').placeholder = "Tính năng tự động tải JSON đang hoàn thiện. Vui lòng dán file transcript_vi.json vào đây.";
            } else if (activePane === 'step-pane-4') {
                document.getElementById('btn-finish').disabled = false;
                showToast("Ghép video hoàn tất!");
            }
        }
    };
    ws.onclose = () => {
        setTimeout(connectWebSocket, 5000);
    };
}

async function loadProjects() {
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        const container = document.getElementById('projects-container');
        container.innerHTML = '';
        
        data.projects.forEach(p => {
            container.innerHTML += `
                <div class="card project-card" onclick="alert('Đường dẫn: ${p.path.replace(/\\/g, '\\\\')}')">
                    <h3>${p.name}</h3>
                    <div class="meta">
                        ${p.has_video ? '🎥 Có Video Gốc<br>' : ''}
                        ${p.has_vi_json ? '📝 Đã dịch sub<br>' : ''}
                        ${p.has_dubbed ? '✅ Đã hoàn thành lồng tiếng' : '⏳ Đang chờ lồng tiếng'}
                    </div>
                </div>
            `;
        });
    } catch (e) {
        console.error(e);
    }
}

async function loadSettings() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        if (data.GOOGLE_API_KEY) {
            document.getElementById('gemini_key').value = data.GOOGLE_API_KEY;
        }
        if (data.OPENAI_API_KEY) {
            document.getElementById('openai_key').value = data.OPENAI_API_KEY;
        }
    } catch (e) {
        console.error(e);
    }
}

async function saveSettings() {
    const geminiKey = document.getElementById('gemini_key').value;
    const openaiKey = document.getElementById('openai_key').value;
    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({settings: {GOOGLE_API_KEY: geminiKey, OPENAI_API_KEY: openaiKey}})
        });
        showToast("Lưu cài đặt thành công!");
    } catch (e) {
        showToast("Lỗi khi lưu cài đặt", true);
    }
}

async function testGemini() {
    const key = document.getElementById('gemini_key').value;
    showToast("Đang kiểm tra API...");
    try {
        const res = await fetch('/api/test-gemini', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({GOOGLE_API_KEY: key})
        });
        const data = await res.json();
        if (res.ok) {
            alert(data.message);
        } else {
            alert("Lỗi: " + data.error);
        }
    } catch (e) {
        alert("Lỗi mạng: " + e.message);
    }
}

async function startASR() {
    const videoPath = document.getElementById('video_path').value;
    if (!videoPath) {
        showToast("Vui lòng nhập đường dẫn video", true);
        return;
    }
    
    // Go to step 2
    goToStep(2);
    document.getElementById('terminal-asr').textContent = "Đang khởi động tiến trình...\n";
    document.getElementById('btn-next-step3').disabled = true;
    currentWorkDir = "";

    try {
        await fetch('/api/start-asr', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                video_path: videoPath,
                target_lang: document.getElementById('target_lang').value,
                burn_subtitles: document.getElementById('burn_subtitles').checked,
                ocr_replace: false, 
                ai_blur: document.getElementById('ai_blur').checked
            })
        });
    } catch (e) {
        showToast("Lỗi khi gọi API", true);
    }
}

async function startTTS() {
    if (!currentWorkDir) {
        showToast("Không tìm thấy thư mục dự án", true);
        return;
    }

    const transcript = document.getElementById('transcript_json').value;
    if (!transcript) {
        showToast("Vui lòng nhập nội dung JSON", true);
        return;
    }

    // Go to step 4
    goToStep(4);
    document.getElementById('terminal-tts').textContent = "BẮT ĐẦU TTS & GHÉP VIDEO...\n";
    document.getElementById('btn-finish').disabled = true;
    
    try {
        await fetch('/api/start-tts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                work_dir: currentWorkDir,
                target_lang: document.getElementById('target_lang').value,
                voice: document.getElementById('voice_selection').value,
                burn_subtitles: document.getElementById('burn_subtitles').checked,
                ocr_replace: false,
                ai_blur: document.getElementById('ai_blur').checked,
                subtitle_size: document.getElementById('subtitle_size').value,
                transcript_json: transcript
            })
        });
    } catch (e) {
        showToast("Lỗi khi gọi API TTS", true);
    }
}
