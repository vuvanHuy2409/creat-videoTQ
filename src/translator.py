import urllib.request
import urllib.parse
import json
from src.utils import setup_logging

logger = setup_logging("translator")


def translate_text_free(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using free Google Translate API."""
    if not text.strip():
        return text

    # Map lang codes for Google Translate
    # e.g., 'en-US' -> 'en', 'zh-CN' -> 'zh-CN', 'vi-VN' -> 'vi'
    sl = source_lang.split("-")[0]
    tl = target_lang.split("-")[0]

    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            translated = "".join([part[0] for part in data[0] if part[0]])
            return translated
    except Exception as e:
        logger.error(f"Free translation failed: {e}")
        # Return original text as fallback if translation fails
        return text


def translate_segments_vi(segments: list[dict], source_lang: str) -> list[dict]:
    """Translate the 'text' field of each segment to Vietnamese and add 'text_vi'."""
    logger.info(f"Translating {len(segments)} segments to Vietnamese...")
    
    # We can batch the translations to be faster and respect limits
    # Join with a unique delimiter that Google Translate preserves
    delimiter = "\n---\n"
    texts = [seg["text"] for seg in segments]
    
    # Group into batches of ~15 segments to avoid URL length limit (approx 2000 chars)
    batch_size = 15
    translated_texts = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        combined = delimiter.join(batch)
        translated_combined = translate_text_free(combined, source_lang, "vi-VN")
        
        # Split back
        parts = translated_combined.split(delimiter)
        # Clean up parts (sometimes Google Translate modifies spaces around delimiter)
        parts = [p.strip() for p in parts]
        
        # If split count matches, append them, otherwise fallback to item-by-item for this batch
        if len(parts) == len(batch):
            translated_texts.extend(parts)
        else:
            logger.warning("Batch split mismatch, falling back to segment-by-segment translation for this batch")
            for item in batch:
                translated_texts.append(translate_text_free(item, source_lang, "vi-VN"))

    # Assign translations
    for seg, trans in zip(segments, translated_texts):
        seg["text_vi"] = trans

    return segments


def translate_segments_jp(segments: list[dict], source_lang: str) -> list[dict]:
    """Translate the 'text' field of each segment to Japanese and add 'text_jp'."""
    logger.info(f"Translating {len(segments)} segments to Japanese...")
    
    delimiter = "\n---\n"
    texts = [seg["text"] for seg in segments]
    
    batch_size = 15
    translated_texts = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        combined = delimiter.join(batch)
        translated_combined = translate_text_free(combined, source_lang, "ja-JP")
        
        parts = translated_combined.split(delimiter)
        parts = [p.strip() for p in parts]
        
        if len(parts) == len(batch):
            translated_texts.extend(parts)
        else:
            logger.warning("Batch split mismatch, falling back to segment-by-segment translation for this batch")
            for item in batch:
                translated_texts.append(translate_text_free(item, source_lang, "ja-JP"))

    for seg, trans in zip(segments, translated_texts):
        seg["text_jp"] = trans

    return segments


def translate_json_openai(json_text: str, target_lang: str) -> str:
    """Translate the 'text' field in the JSON transcript using OpenAI API."""
    from config import OPENAI_API_KEY, OPENAI_MODEL_ID
    
    # Fallback / backward compatibility: check OPENAI_API_KEY first, fallback to GOOGLE_API_KEY if user hasn't set OPENAI_API_KEY
    api_key = OPENAI_API_KEY
    if not api_key:
        raise ValueError("Lỗi: Không tìm thấy OPENAI_API_KEY trong file .env hoặc config.py. Vui lòng thêm OPENAI_API_KEY vào .env.")

    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Lỗi: Thư viện 'openai' chưa được cài đặt. Vui lòng chạy: pip install openai")

    system_prompt = (
        f"You are a professional video subtitling translator. "
        f"Translate the 'text' field in the provided JSON array into {target_lang}. "
        f"Maintain natural, conversational tone appropriate for video dubbing. "
        f"Return ONLY valid JSON array with the exact same structure, preserving 'id', 'start', 'end', 'duration' fields. "
        f"Do not include markdown code block formatting (like ```json), commentary, or extra keys."
    )

    prompt = f"JSON to translate:\n{json_text}"

    client = OpenAI(api_key=api_key)
    model = OPENAI_MODEL_ID or "gpt-4o"

    import time
    import re

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"} if "gpt-4" in model or "gpt-3.5" in model or "o1" in model or "o3" in model else None
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return text.strip()
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                wait_time = 5.0
                logger.warning(f"Quá tải API OpenAI. Đang chờ {wait_time} giây để thử lại (Lần {attempt+1}/3)...")
                time.sleep(wait_time)
            else:
                logger.error(f"OpenAI translation failed: {e}")
                raise RuntimeError(f"Lỗi khi gọi API OpenAI:\n{e}")


# Alias for backward compatibility
translate_json_gemini = translate_json_openai

