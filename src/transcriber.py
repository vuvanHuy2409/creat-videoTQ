"""Nhận dạng giọng nói liên tục bằng Azure Speech và lưu transcript có mốc thời gian."""

import json
import os
import time
import wave
import azure.cognitiveservices.speech as speechsdk
import config
from src.utils import setup_logging

logger = setup_logging("transcriber")


def transcribe(audio_path: str, language: str) -> list[dict]:
    """Chạy Azure ASR trên WAV mono PCM và trả danh sách segment theo thời gian."""
    # SDK của Azure kiểm tra đường dẫn và định dạng chặt hơn FFmpeg. Chuẩn hóa
    # đường dẫn trước khi tạo AudioConfig để GUI và CLI có hành vi giống nhau.
    audio_path = os.path.abspath(os.path.expanduser(str(audio_path)))
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"ASR audio file not found: {audio_path}")
    if os.path.getsize(audio_path) == 0:
        raise ValueError(f"ASR audio file is empty: {audio_path}")
    try:
        with wave.open(audio_path, "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError(
                    "ASR audio must be a mono 16-bit PCM WAV "
                    f"(got channels={wav.getnchannels()}, "
                    f"sample_width={wav.getsampwidth()}): {audio_path}"
                )
    except wave.Error as exc:
        raise ValueError(f"ASR audio is not a readable PCM WAV: {audio_path}") from exc

    speech_config = speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = language
    speech_config.request_word_level_timestamps()
    speech_config.output_format = speechsdk.OutputFormat.Detailed

    try:
        audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Azure Speech could not open ASR audio file '{audio_path}'. "
            "Re-run the audio extraction step to regenerate a mono 16-bit WAV."
        ) from exc

    # Các callback của Speech SDK chạy bất đồng bộ; chúng cùng ghi vào danh sách
    # segment và dùng cờ ``done`` để vòng lặp chính biết khi nào kết thúc.
    segments = []
    done = False
    segment_id = 0
    errors = []

    def on_recognized(evt):
        nonlocal segment_id
        result = evt.result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech and result.text.strip():
            start = result.offset / 10_000_000
            duration = result.duration / 10_000_000
            end = start + duration
            segment_id += 1
            segment = {
                "id": segment_id,
                "text": result.text,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
            }
            segments.append(segment)
            logger.info(f"Segment {segment_id}: [{start:.1f}s-{end:.1f}s] {result.text[:50]}...")

    def on_canceled(evt):
        nonlocal done
        details = evt.result.cancellation_details
        if details.reason == speechsdk.CancellationReason.EndOfStream:
            logger.info("Recognition reached end of stream.")
        elif details.reason == speechsdk.CancellationReason.Error:
            error_msg = f"ASR error: {details.error_details}"
            logger.error(error_msg)
            errors.append(error_msg)
        else:
            logger.warning(f"Recognition canceled: {details.reason}")
        done = True

    def on_session_stopped(evt):
        nonlocal done
        logger.info("Recognition session stopped.")
        done = True

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_session_stopped)

    logger.info(f"Starting transcription: {audio_path} (language: {language})")
    recognizer.start_continuous_recognition()

    while not done:
        time.sleep(0.5)

    recognizer.stop_continuous_recognition()

    if errors:
        raise RuntimeError(f"Transcription failed: {'; '.join(errors)}")

    logger.info(f"Transcription complete: {len(segments)} raw segments")

    # Chia segment dài thành các chunk có thời lượng xấp xỉ MAX_SEGMENT_DURATION.
    segments = split_long_segments(segments, max_duration=10.0)
    logger.info(f"After splitting: {len(segments)} segments")

    return segments


def split_long_segments(segments: list[dict], max_duration: float = 10.0) -> list[dict]:
    """Chia segment dài thành đoạn ngắn hơn tại ranh giới câu.

    Dấu câu được dùng làm điểm tách; thời lượng được phân bổ theo số ký tự để
    giữ tương đối nhịp nói gốc.
    """
    import re
    result = []
    new_id = 0

    for seg in segments:
        if seg["duration"] <= max_duration:
            new_id += 1
            result.append({**seg, "id": new_id})
            continue

        # Ưu tiên tách ở dấu câu thay vì cắt giữa một từ/câu hoàn chỉnh.
        sentences = re.split(r'(?<=[.!?;])\s+', seg["text"].strip())
        if len(sentences) <= 1:
            # Không tìm thấy ranh giới câu thì giữ nguyên đoạn hiện tại.
            new_id += 1
            result.append({**seg, "id": new_id})
            continue

        # Gom các câu liên tiếp sao cho mỗi nhóm gần với giới hạn thời lượng.
        total_chars = sum(len(s) for s in sentences)
        total_duration = seg["duration"]
        start = seg["start"]

        chunk_sentences = []
        chunk_chars = 0

        for sentence in sentences:
            estimated_chunk_duration = (chunk_chars + len(sentence)) / total_chars * total_duration

            # Nếu thêm câu mới làm vượt giới hạn và nhóm hiện tại đã có nội dung,
            # chốt nhóm cũ trước khi bắt đầu nhóm tiếp theo.
            if chunk_sentences and estimated_chunk_duration > max_duration:
                chunk_duration = chunk_chars / total_chars * total_duration
                end = round(start + chunk_duration, 3)
                new_id += 1
                result.append({
                    "id": new_id,
                    "text": " ".join(chunk_sentences),
                    "start": round(start, 3),
                    "end": end,
                    "duration": round(chunk_duration, 3),
                })
                start = end
                chunk_sentences = []
                chunk_chars = 0

            chunk_sentences.append(sentence)
            chunk_chars += len(sentence)

        # Ghi nhóm câu còn lại và dùng đúng thời điểm kết thúc của segment gốc.
        if chunk_sentences:
            end = seg["end"]
            new_id += 1
            result.append({
                "id": new_id,
                "text": " ".join(chunk_sentences),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })

    return result


def save_transcript(segments: list[dict], output_path: str) -> str:
    """Lưu transcript JSON UTF-8, giữ nguyên chữ Unicode và format dễ đọc."""
    import os
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    logger.info(f"Transcript saved: {output_path}")
    return output_path
