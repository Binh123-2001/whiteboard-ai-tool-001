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

# --- 1. CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title="Whiteboard AI Studio", page_icon="🎬", layout="centered")

# CSS để ẩn menu mặc định, làm web trông như một sản phẩm thực thụ
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# --- 2. CƠ CHẾ ĐĂNG NHẬP BẢO MẬT ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Whiteboard AI Studio")
    st.write("Vui lòng nhập mật khẩu để truy cập hệ thống.")
    pwd = st.text_input("Mật khẩu:", type="password")
    if st.button("Đăng nhập"):
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun() # Tải lại trang ngay khi đăng nhập đúng
        else:
            st.error("Mật khẩu không đúng, vui lòng thử lại!")
    st.stop() # Dừng toàn bộ chương trình nếu chưa đăng nhập đúng

# --- 3. CẤU HÌNH HỆ THỐNG (Lấy từ Secrets) ---
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
GROQ_MODEL = "openai/gpt-oss-120b" 
ai_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

CF_ACCOUNT_ID = st.secrets["CF_ACCOUNT_ID"]
CF_API_TOKEN = st.secrets["CF_API_TOKEN"]
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"

R2_ACCESS_KEY = st.secrets["R2_ACCESS_KEY"]
R2_SECRET_KEY = st.secrets["R2_SECRET_KEY"]
R2_ENDPOINT = st.secrets["R2_ENDPOINT"]
R2_BUCKET = "whiteboard-videos"
R2_PUBLIC_URL = st.secrets["R2_PUBLIC_URL"]

# Đường dẫn
PROJECT_DIR = Path(__file__).parent.absolute()
OUTPUT_DIR = PROJECT_DIR / "my_project"
import sys
PYTHON_EXE = sys.executable 
RENDER_SCRIPT = PROJECT_DIR / "scripts" / "render_stream_whiteboard.py"
MERGE_SCRIPT = PROJECT_DIR / "scripts" / "merge_videos_ffmpeg.py"
HAND_IMG = PROJECT_DIR / "assets" / "drawing-hand.png"

s3_client = boto3.client(
    "s3", endpoint_url=R2_ENDPOINT, aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY, region_name="auto",
    config=Config(signature_version="s3v4")
)

def upload_to_r2(local_file_path, r2_key):
    try:
        s3_client.upload_file(str(local_file_path), R2_BUCKET, r2_key, ExtraArgs={'ContentType': 'video/mp4'})
        return f"{R2_PUBLIC_URL}/{r2_key}"
    except Exception as e:
        st.error(f"Lỗi upload R2: {e}")
        return None

async def generate_tts_with_retry(text, output_file, voice):
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_file)
            return True
        except Exception:
            time.sleep(2)
    return False

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip()) * 1000

# --- 4. GIAO DIỆN CHÍNH & TÙY CHỌN ---
st.title("🎬 Whiteboard AI Studio")
st.markdown("Tự động biến file phụ đề (SRT) thành video Whiteboard có giọng đọc.")

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2103/2103604.png", width=100) # Logo mẫu (bạn có thể thay link ảnh khác)
    st.header("Cấu hình Video")
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
    
    show_subtitles = st.checkbox("Hiển thị phụ đề", value=False)
    st.info("💡 Mẹo: Video từ 3-5 phút sẽ cho chất lượng tốt nhất.")

# --- 5. XỬ LÝ CHÍNH ---
uploaded_file = st.file_uploader("Tải lên file phụ đề (.srt)", type=["srt"])

if uploaded_file is not None:
    if st.button("🚀 Bắt đầu tạo Video", use_container_width=True):
        srt_content = uploaded_file.getvalue().decode("utf-8-sig")
        
        def parse_srt(text):
            blocks = re.split(r'\n\s*\n', text.strip())
            return [" ".join(b.strip().split('\n')[2:]) for b in blocks if len(b.strip().split('\n')) >= 3]
        
        scenes = parse_srt(srt_content)
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
            
            # Groq AI
            ai_data = None
            for attempt in range(3):
                try:
                    prompt_system = "Bạn là một chuyên gia vẽ minh họa line-art. Bạn CHỈ trả về JSON hợp lệ."
                    prompt_user = f"""
    Đọc nội dung sau: "{scene_text}"
    Hãy tưởng tượng ra MỘT khung hình cụ thể, trực quan, dễ vẽ (đồ vật, con vật, hoặc hành động rõ ràng) để minh họa cho nội dung này.
    Tuyệt đối không vẽ các khái niệm trừu tượng (như sự học, tình yêu, sự thành công).
    Mô tả khung hình đó bằng 1 câu tiếng Anh thật chi tiết.
    Trả về đúng định dạng JSON: {{"image_prompt": "câu mô tả tiếng Anh chi tiết", "subtitle": "{scene_text}"}}
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
                    ai_data = json.loads(response.choices[0].message.content)
                    break
                except Exception:
                    time.sleep(5)
            
            if not ai_data: continue
            
            img_prompt, subtitle_text = ai_data["image_prompt"], ai_data["subtitle"]
            
            # Cloudflare
            img_path = OUTPUT_DIR / f"scene-{scene_num:02d}.png"
            for attempt in range(3):
                try:
                    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
                    # Thêm các từ khóa ép buộc phong cách rõ ràng, tránh vẽ nguệch ngoạc
                    full_prompt = f"Coloring book page for kids, thick clean black outlines, pure white background, very clear and recognizable drawing, no abstract art. Subject: {img_prompt}"
                    payload = {"prompt": full_prompt, "steps": 4}
                    cf_res = requests.post(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, json=payload, timeout=60)
                    if cf_res.status_code == 200:
                        with open(img_path, "wb") as f:
                            f.write(base64.b64decode(cf_res.json().get("result", {}).get("image")))
                        break
                except Exception:
                    time.sleep(3)
            
            # Edge TTS
            audio_path = OUTPUT_DIR / f"scene-{scene_num:02d}.mp3"
            audio_success = asyncio.run(generate_tts_with_retry(subtitle_text, str(audio_path), selected_voice))
            
            if not audio_success:
                est_duration = max(3, len(subtitle_text.split()) * 0.4)
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(est_duration), "-q:a", "9", str(audio_path)], capture_output=True)
                
            audio_duration_ms = get_audio_duration(audio_path)
            video_duration_ms = int(audio_duration_ms + 800)
            
            # JSON cấu hình vẽ
            annotation_json = {
                "sceneId": f"scene-{scene_num:02d}", "canvas": {"width": 1080, "height": 720},
                "storyBasis": subtitle_text, "sceneDurationMs": video_duration_ms,
                "elements": [{"id": "el-1", "label": "Object", "sequence": 1, "narrativeRole": "Vật thể", "subtitle": subtitle_text, "type": "structure", "region": {"x": 340, "y": 210, "width": 400, "height": 300}, "reveal": {"direction": "left_to_right", "startMs": 500, "durationMs": max(2000, video_duration_ms - 1500), "maskPaddingPx": 22, "protectedRegions": []}, "handPath": {"start": [350, 220], "end": [700, 450], "easing": "easeInOut"}}]
            }
            json_path = OUTPUT_DIR / f"scene-{scene_num:02d}.annotation.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(annotation_json, f, ensure_ascii=False, indent=2)
                
            # Render
            video_output = OUTPUT_DIR / f"scene-{scene_num:02d}.mp4"
            subprocess.run([PYTHON_EXE, str(RENDER_SCRIPT), str(img_path), str(json_path), str(video_output), str(HAND_IMG)], env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True)
            
            # Ghép Audio
            video_with_audio = OUTPUT_DIR / f"scene-{scene_num:02d}_audio.mp4"
            subprocess.run(["ffmpeg", "-y", "-i", str(video_output), "-i", str(audio_path), "-c:v", "copy", "-c:a", "aac", "-shortest", str(video_with_audio)], capture_output=True)
            video_clips.append(str(video_with_audio))
            
            progress_bar.progress((i + 1) / len(scenes))

        # Ghép các cảnh & Upload
        if video_clips:
            status_text.info("🔄 Đang ghép các cảnh...")
            final_output = OUTPUT_DIR / "FINAL_VIDEO.mp4"
            subprocess.run([PYTHON_EXE, str(MERGE_SCRIPT), str(final_output)] + video_clips, capture_output=True, check=True)
            
            file_to_upload = final_output
            
            # Phụ đề
            if show_subtitles:
                status_text.info("🔄 Đang chèn phụ đề...")
                final_with_subs = OUTPUT_DIR / "FINAL_VIDEO_SUBBED.mp4"
                srt_path_escaped = str(srt_file_path).replace("\\", "/").replace(":", "\\:")
                sub_cmd = ["ffmpeg", "-y", "-i", str(final_output), "-vf", f"subtitles='{srt_path_escaped}':force_style='FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=25'", "-c:v", "libx264", "-c:a", "aac", str(final_with_subs)]
                subprocess.run(sub_cmd, capture_output=True, check=True)
                file_to_upload = final_with_subs

            status_text.info("☁️ Đang tải video lên đám mây...")
            r2_key = f"video_{int(time.time())}.mp4"
            public_video_url = upload_to_r2(file_to_upload, r2_key)
            
            if public_video_url:
                st.success("🎉 HOÀN THÀNH! Video đã được lưu trên mây.")
                st.warning("⚠️ Lưu ý: Link tải video này sẽ tự động hết hạn và bị xóa sau 24 giờ. Bạn hãy tải video về máy ngay nhé!")
                st.markdown(f"### [👉 Bấm vào đây để tải Video]({public_video_url})")
                st.video(public_video_url)
            else:
                st.warning("Không thể upload lên mây. Đang hiển thị video từ máy chủ.")
                st.video(str(file_to_upload))