import streamlit as st
from pathlib import Path
import requests
import subprocess
import json
import time
import base64
import os
import re
import asyncio
import edge_tts
from openai import OpenAI
import boto3
from botocore.config import Config

# --- CẤU HÌNH CLOUDFLARE R2 (Lấy từ Secrets) ---
R2_ACCESS_KEY = st.secrets["R2_ACCESS_KEY"]
R2_SECRET_KEY = st.secrets["R2_SECRET_KEY"]
R2_ENDPOINT = st.secrets["R2_ENDPOINT"]
R2_BUCKET = "whiteboard-videos" # Tên bucket thì không cần giấu
R2_PUBLIC_URL = st.secrets["R2_PUBLIC_URL"]

s3_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="apac",
    config=Config(signature_version="s3v4")
)

def upload_to_r2(local_file_path, r2_key):
    """Upload file lên R2 và trả về URL public"""
    try:
        s3_client.upload_file(
            str(local_file_path), R2_BUCKET, r2_key,
            ExtraArgs={'ContentType': 'video/mp4'}
        )
        return f"{R2_PUBLIC_URL}/{r2_key}"
    except Exception as e:
        st.error(f"Lỗi upload R2: {e}")
        return None

# Lấy đường dẫn FFmpeg và FFprobe
ffmpeg_path = "ffmpeg"
ffprobe_path = "ffprobe"

import streamlit as st
import os

# --- CẤU HÌNH HỆ THỐNG (SỬ DỤNG SECRETS) ---
# Lấy API Key từ file cấu hình bảo mật của Streamlit
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
GROQ_MODEL = "openai/gpt-oss-120b" 
# Khởi tạo kết nối với Groq AI
ai_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

CF_ACCOUNT_ID = st.secrets["CF_ACCOUNT_ID"]
CF_API_TOKEN = st.secrets["CF_API_TOKEN"]
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"

R2_ACCESS_KEY = st.secrets["R2_ACCESS_KEY"]
R2_SECRET_KEY = st.secrets["R2_SECRET_KEY"]
R2_ENDPOINT = st.secrets["R2_ENDPOINT"]
R2_BUCKET = "whiteboard-videos"
R2_PUBLIC_URL = st.secrets["R2_PUBLIC_URL"]

# Đường dẫn tương đối cho máy chủ Cloud
from pathlib import Path

# Đường dẫn tương đối cho máy chủ Cloud
PROJECT_DIR = Path(__file__).parent.absolute()
OUTPUT_DIR = PROJECT_DIR / "my_project"
PYTHON_EXE = "python" 
RENDER_SCRIPT = PROJECT_DIR / "scripts" / "render_stream_whiteboard.py"
MERGE_SCRIPT = PROJECT_DIR / "scripts" / "merge_videos_ffmpeg.py"
HAND_IMG = PROJECT_DIR / "assets" / "drawing-hand.png"

# --- GIAO DIỆN WEB ---
st.set_page_config(page_title="Whiteboard AI Generator", layout="wide")
st.title("🎬 Hệ thống tạo Video Whiteboard (Groq + Cloudflare)")

uploaded_file = st.file_uploader("Tải lên file phụ đề (.srt)", type=["srt"])

col1, col2 = st.columns(2)
with col1:
    voice_option = st.selectbox(
        "Chọn giọng đọc:",
        ["Nữ (HoaiMy)", "Nam (NamMinh)", "Nữ miền Nam (LanAnh)", "Nam miền Bắc (BaoQuoc)"]
    )
    voice_map = {
        "Nữ (HoaiMy)": "vi-VN-HoaiMyNeural",
        "Nam (NamMinh)": "vi-VN-NamMinhNeural",
        "Nữ miền Nam (LanAnh)": "vi-VN-LanAnhNeural",
        "Nam miền Bắc (BaoQuoc)": "vi-VN-BaoQuocNeural"
    }
    selected_voice = voice_map[voice_option]
with col2:
    show_subtitles = st.checkbox("Hiển thị phụ đề trên video (Font nhỏ, ở dưới cùng)", value=False)

# --- CÁC HÀM XỬ LÝ ---
async def generate_tts_with_retry(text, output_file, voice):
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_file)
            return True
        except Exception as e:
            print(f"TTS Error (Attempt {attempt+1}): {e}")
            time.sleep(2)
    return False

def get_audio_duration(file_path):
    cmd = [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip()) * 1000

# --- LUỒNG XỬ LÝ CHÍNH ---
if uploaded_file is not None:
    st.success(f"Đã nhận file: {uploaded_file.name}")
    
    if st.button("🚀 Bắt đầu tạo Video"):
        srt_content = uploaded_file.getvalue().decode("utf-8-sig")
        
        # Parse SRT thành list các câu
        def parse_srt(text):
            blocks = re.split(r'\n\s*\n', text.strip())
            scenes = []
            for block in blocks:
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    scenes.append(" ".join(lines[2:]))
            return scenes

        scenes = parse_srt(srt_content)
        
        # Lưu file SRT gốc
        srt_file_path = OUTPUT_DIR / "original.srt"
        OUTPUT_DIR.mkdir(exist_ok=True)
        with open(srt_file_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
            
        st.info(f"Tìm thấy {len(scenes)} cảnh trong file SRT.")
        progress_bar = st.progress(0)
        status_text = st.empty()
        video_clips = []
        
        for i, scene_text in enumerate(scenes):
            scene_num = i + 1
            status_text.info(f"⏳ Đang xử lý cảnh {scene_num}/{len(scenes)}...")
            
            # --- 1. GỌI GROQ AI LẤY PROMPT (Có Retry) ---
            ai_data = None
            for attempt in range(3):
                try:
                    prompt_system = "Bạn là chuyên gia làm video whiteboard. Bạn CHỈ trả về chuỗi JSON hợp lệ, không có bất kỳ văn bản giải thích nào bên ngoài."
                    prompt_user = f"""
Đọc nội dung sau và tạo 1 câu lệnh tiếng Anh mô tả hình vẽ line-art đen trắng.
Trả về đúng cấu trúc JSON này:
{{"image_prompt": "câu lệnh tiếng anh", "subtitle": "{scene_text}"}}
"""
                    response = ai_client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=[
                            {"role": "system", "content": prompt_system},
                            {"role": "user", "content": prompt_user}
                        ],
                        temperature=0.7,
                        response_format={"type": "json_object"}
                    )
                    json_str = response.choices[0].message.content
                    ai_data = json.loads(json_str)
                    break
                except Exception as e:
                    st.warning(f"Cảnh {scene_num} - Lỗi Groq AI (Lần {attempt+1}/3): {e}. Đợi 5s...")
                    time.sleep(5) 
            
            if not ai_data:
                st.error(f"Không thể lấy dữ liệu từ AI cho cảnh {scene_num}. Bỏ qua cảnh này.")
                continue
                
            img_prompt = ai_data["image_prompt"]
            subtitle_text = ai_data["subtitle"]
            
            # --- 2. GỌI CLOUDFLARE TẠO ẢNH (Có Retry) ---
            img_path = OUTPUT_DIR / f"scene-{scene_num:02d}.png"
            img_success = False
            for attempt in range(3):
                try:
                    full_prompt = f"Simple black and white line art, whiteboard animation style, thick clean black outlines, pure white background. {img_prompt}"
                    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
                    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
                    payload = {"prompt": full_prompt, "steps": 4}
                    
                    cf_response = requests.post(url, headers=headers, json=payload, timeout=60)
                    if cf_response.status_code == 200:
                        image_b64 = cf_response.json().get("result", {}).get("image")
                        with open(img_path, "wb") as f:
                            f.write(base64.b64decode(image_b64))
                        img_success = True
                        break
                except Exception as e:
                    time.sleep(3)
            
            if not img_success:
                st.error(f"Không thể tạo ảnh cho cảnh {scene_num}. Bỏ qua cảnh này.")
                continue

            # --- 3. TẠO GIỌNG ĐỌC (Có Retry & Fallback) ---
            audio_path = OUTPUT_DIR / f"scene-{scene_num:02d}.mp3"
            time.sleep(2)
            audio_success = asyncio.run(generate_tts_with_retry(subtitle_text, str(audio_path), selected_voice))
            
            if not audio_success:
                st.warning(f"Không thể tạo âm thanh cảnh {scene_num}. Tạo âm thanh câm thay thế...")
                est_duration = max(3, len(subtitle_text.split()) * 0.4)
                cmd_silent = [ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(est_duration), "-q:a", "9", str(audio_path)]
                subprocess.run(cmd_silent, capture_output=True)
                
            audio_duration_ms = get_audio_duration(audio_path)
            video_duration_ms = int(audio_duration_ms + 800)
            
            # --- 4. TẠO FILE JSON CẤU HÌNH VẼ ---
            annotation_json = {
                "sceneId": f"scene-{scene_num:02d}",
                "canvas": {"width": 1080, "height": 720},
                "storyBasis": subtitle_text,
                "sceneDurationMs": video_duration_ms,
                "elements": [
                    {
                        "id": "el-1", "label": "Object", "sequence": 1, "narrativeRole": "Vật thể",
                        "subtitle": subtitle_text, "type": "structure",
                        "region": {"x": 340, "y": 210, "width": 400, "height": 300},
                        "reveal": {"direction": "left_to_right", "startMs": 500, "durationMs": max(2000, video_duration_ms - 1500), "maskPaddingPx": 22, "protectedRegions": []},
                        "handPath": {"start": [350, 220], "end": [700, 450], "easing": "easeInOut"}
                    }
                ]
            }
            json_path = OUTPUT_DIR / f"scene-{scene_num:02d}.annotation.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(annotation_json, f, ensure_ascii=False, indent=2)
                
            # --- 5. RENDER VIDEO NGẮN ---
            video_output = OUTPUT_DIR / f"scene-{scene_num:02d}.mp4"
            cmd = [str(PYTHON_EXE), str(RENDER_SCRIPT), str(img_path), str(json_path), str(video_output), str(HAND_IMG)]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, env=env)
            
            # --- 6. GHÉP ÂM THANH VÀO VIDEO ---
            video_with_audio = OUTPUT_DIR / f"scene-{scene_num:02d}_audio.mp4"
            cmd_audio = [ffmpeg_path, "-y", "-i", str(video_output), "-i", str(audio_path), "-c:v", "copy", "-c:a", "aac", "-shortest", str(video_with_audio)]
            subprocess.run(cmd_audio, capture_output=True, check=True)
            video_clips.append(str(video_with_audio))
                
            progress_bar.progress((i + 1) / len(scenes))

                # --- 7. GHÉP TẤT CẢ CÁC CẢNH ---
        if video_clips:
            status_text.info("🔄 Đang ghép các cảnh...")
            final_output = OUTPUT_DIR / "FINAL_VIDEO.mp4"
            merge_cmd = [str(PYTHON_EXE), str(MERGE_SCRIPT), str(final_output)] + video_clips
            subprocess.run(merge_cmd, capture_output=True, check=True)
            
            # Biến lưu file cuối cùng để upload
            file_to_upload = final_output
            
            # --- CHÈN PHỤ ĐỀ (NẾU BẬT) ---
            if show_subtitles:
                status_text.info("🔄 Đang chèn phụ đề...")
                final_with_subs = OUTPUT_DIR / "FINAL_VIDEO_SUBBED.mp4"
                srt_path_escaped = str(srt_file_path).replace("\\", "/").replace(":", "\\:")
                
                sub_cmd = [
                    ffmpeg_path, "-y", "-i", str(final_output),
                    "-vf", f"subtitles='{srt_path_escaped}':force_style='FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=25'",
                    "-c:v", "libx264", "-c:a", "aac", str(final_with_subs)
                ]
                try:
                    subprocess.run(sub_cmd, capture_output=True, check=True)
                    file_to_upload = final_with_subs
                    st.success("Đã chèn phụ đề thành công.")
                except Exception as e:
                    st.error("Lỗi khi chèn phụ đề. Sẽ tải video không phụ đề.")

            # --- UPLOAD LÊN CLOUDFLARE R2 ---
            status_text.info("☁️ Đang tải video lên đám mây (R2)...")
            r2_key = f"video_{int(time.time())}.mp4"
            public_video_url = upload_to_r2(file_to_upload, r2_key)
            
            if public_video_url:
                st.success("🎉 HOÀN THÀNH! Video đã được lưu trên mây.")
                st.markdown(f"### [👉 Bấm vào đây để tải Video]({public_video_url})")
                st.video(public_video_url)
            else:
                st.warning("Không thể upload lên mây. Đang hiển thị video từ ổ cứng máy tính.")
                st.video(str(file_to_upload))
        else:
            st.error("Không có video nào được tạo thành công.")