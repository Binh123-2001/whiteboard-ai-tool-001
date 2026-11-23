import urllib.parse
from PIL import Image, ImageDraw
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
            st.rerun()
        else:
            st.error("Mật khẩu không đúng, vui lòng thử lại!")
    st.stop()

# --- 3. CẤU HÌNH HỆ THỐNG ---
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

# Đường dẫn Cloud
import sys
PROJECT_DIR = Path(__file__).parent.absolute()
OUTPUT_DIR = PROJECT_DIR / "my_project"
PYTHON_EXE = sys.executable 
RENDER_SCRIPT = PROJECT_DIR / "scripts" / "render_stream_whiteboard.py"
MERGE_SCRIPT = PROJECT_DIR / "scripts" / "merge_videos_ffmpeg.py"
HAND_IMG = PROJECT_DIR / "assets" / "drawing-hand.png"

ffmpeg_path = "ffmpeg"
ffprobe_path = "ffprobe"

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
    cmd = [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip()) * 1000

# Hàm chuyển đổi mili-giây sang định dạng thời gian của SRT
def ms_to_srt_time(ms):
    hours = int(ms // 3600000)
    ms %= 3600000
    minutes = int(ms // 60000)
    ms %= 60000
    seconds = int(ms // 1000)
    milliseconds = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

# --- 4. GIAO DIỆN CHÍNH ---
st.title("🎬 Whiteboard AI Studio")
st.markdown("Tự động biến file phụ đề (SRT) thành video Whiteboard có giọng đọc.")

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2103/2103604.png", width=100)
    st.header("Cấu hình Video")
    
    # TÙY CHỌN TỶ LỆ KHUNG HÌNH
    video_format = st.radio("Tỷ lệ khung hình:", ["Dọc (9:16 - TikTok/Reels)", "Ngang (16:9 - YouTube)"])
    if "Dọc" in video_format:
        canvas_w, canvas_h = 720, 1080
        # Sửa lại vùng vẽ (region) to hơn, nằm ở giữa màn hình
        region_coords = {"x": 110, "y": 250, "width": 500, "height": 500} 
        # Sửa lại đường đi của bàn tay cho khớp với khung mới
        hand_start, hand_end = [120, 260], [600, 740]
        # Sửa lại cỡ chữ phụ đề nhỏ đi và đẩy xuống sát đáy
        sub_fontsize, sub_margin = 11, 30 

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
    show_subtitles = st.checkbox("Hiển thị phụ đề", value=True)

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
        synced_srt_path = OUTPUT_DIR / "synced.srt" # File phụ đề đồng bộ mới
        OUTPUT_DIR.mkdir(exist_ok=True)
        with open(srt_file_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
            
        st.info(f"Tìm thấy {len(scenes)} cảnh trong file SRT.")
        progress_bar = st.progress(0)
        status_text = st.empty()
        video_clips = []
        
        # Biến dùng để đồng bộ phụ đề
        current_time_ms = 0
        synced_srt_content = ""

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
    Hãy tưởng tượng ra MỘT khung hình cụ thể, trực quan để minh họa cho nội dung này.
    Nếu nội dung có nhắc đến con người, hãy mô tả họ dưới dạng "stickman" (người que) hoặc "simple cartoon character" (nhân vật hoạt hình đơn giản) đang thực hiện một hành động cụ thể. TUYỆT ĐỐI KHÔNG mô tả chi tiết khuôn mặt.
    Mô tả khung hình đó bằng 1 câu tiếng Anh ngắn gọn.
    Trả về đúng định dạng JSON: {{"image_prompt": "câu mô tả tiếng Anh chi tiết", "subtitle": "{scene_text}"}}
    """
                    response = ai_client.chat.completions.create(
                        model=GROQ_MODEL, 
                        messages=[{"role": "system", "content": prompt_system}, {"role": "user", "content": prompt_user}],
                        temperature=0.7, response_format={"type": "json_object"}
                    )
                    ai_data = json.loads(response.choices[0].message.content)
                    break
                except Exception:
                    time.sleep(5)
            
            if not ai_data: continue
            img_prompt, subtitle_text = ai_data["image_prompt"], ai_data["subtitle"]
            
            # Cloudflare
            # --- CƠ CHẾ TẠO ẢNH 3 LỚP (BẤT TỬ) ---
            img_path = OUTPUT_DIR / f"scene-{scene_num:02d}.png"
            full_prompt = f"Coloring book page for kids, thick clean black outlines, pure white background, very clear and recognizable drawing, no abstract art. Subject: {img_prompt}"
            img_success = False
            
            # LỚP 1: Thử Cloudflare 5 lần
            for attempt in range(5):
                try:
                    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
                    payload = {"prompt": f"Simple 2D vector line art, thick clean black outlines, pure white background, minimalist doodle style. If there are people, draw them as simple stickman or faceless cartoon characters. Subject: {img_prompt}", "steps": 4}
                    cf_res = requests.post(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, json=payload, timeout=60)
                    
                    if cf_res.status_code == 200:
                        img_b64 = cf_res.json().get("result", {}).get("image")
                        if img_b64: # Kiểm tra xem có dữ liệu ảnh thực sự không
                            with open(img_path, "wb") as f:
                                f.write(base64.b64decode(img_b64))
                            img_success = True
                            break
                except Exception as e:
                    print(f"Lỗi Cloudflare (Lần {attempt+1}): {e}")
                
                time.sleep(5 + attempt * 2) # Đợi lâu hơn nếu thử lại nhiều lần
            
            # LỚP 2: Nếu Cloudflare thất bại, dùng Pollinations.AI dự phòng
            if not img_success:
                st.warning(f"⚠️ Cloudflare quá tải ở cảnh {scene_num}, đang chuyển sang máy chủ dự phòng...")
                try:
                    time.sleep(5)
                    encoded_prompt = urllib.parse.quote(full_prompt)
                    fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={canvas_w}&height={canvas_h}&nologo=true&model=flux"
                    res = requests.get(fallback_url, timeout=60)
                    if res.status_code == 200:
                        with open(img_path, "wb") as f:
                            f.write(res.content)
                        img_success = True
                except Exception as e:
                    print(f"Lỗi dự phòng: {e}")

            # LỚP 3: Nếu mạng sập hoàn toàn, tạo ảnh trắng để không làm hỏng video
            if not img_success:
                st.error(f"❌ Mạng có vấn đề ở cảnh {scene_num}. Tạo ảnh trắng thay thế để tiếp tục...")
                img = Image.new('RGB', (canvas_w, canvas_h), color='white')
                d = ImageDraw.Draw(img)
                d.text((50, 50), f"Lỗi tạo ảnh cho: {img_prompt[:50]}...", fill="black")
                img.save(img_path)
            # --- KẾT THÚC CƠ CHẾ 3 LỚP ---

            # 1. TẠO GIỌNG ĐỌC (EDGE TTS) - ĐÂY LÀ PHẦN ĐANG BỊ THIẾU
            audio_path = OUTPUT_DIR / f"scene-{scene_num:02d}.mp3"
            audio_success = asyncio.run(generate_tts_with_retry(subtitle_text, str(audio_path), selected_voice))
            
            if not audio_success:
                est_duration = max(3, len(subtitle_text.split()) * 0.4)
                subprocess.run([ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(est_duration), "-q:a", "9", str(audio_path)], capture_output=True)

            # 2. ĐO ĐỘ DÀI ÂM THANH (Lúc này audio_path đã tồn tại)
            audio_duration_ms = get_audio_duration(audio_path) 
            video_duration_ms = int(audio_duration_ms + 800)

            # 3. ĐỒNG BỘ PHỤ ĐỀ
            start_time_str = ms_to_srt_time(current_time_ms)
            end_time_str = ms_to_srt_time(current_time_ms + audio_duration_ms)
            synced_srt_content += f"{scene_num}\n{start_time_str} --> {end_time_str}\n{subtitle_text}\n\n"
            current_time_ms += video_duration_ms
            
            # ... (Phần JSON cấu hình vẽ bên dưới giữ nguyên)
            
            # JSON cấu hình vẽ (Đã cập nhật tọa độ theo khung hình)
            annotation_json = {
                "sceneId": f"scene-{scene_num:02d}", "canvas": {"width": canvas_w, "height": canvas_h},
                "storyBasis": subtitle_text, "sceneDurationMs": video_duration_ms,
                "elements": [{"id": "el-1", "label": "Object", "sequence": 1, "narrativeRole": "Vật thể", "subtitle": subtitle_text, "type": "structure", "region": region_coords, "reveal": {"direction": "left_to_right", "startMs": 500, "durationMs": max(2000, video_duration_ms - 1500), "maskPaddingPx": 22, "protectedRegions": []}, "handPath": {"start": hand_start, "end": hand_end, "easing": "easeInOut"}}]
            }
            json_path = OUTPUT_DIR / f"scene-{scene_num:02d}.annotation.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(annotation_json, f, ensure_ascii=False, indent=2)
                
            # Render
            video_output = OUTPUT_DIR / f"scene-{scene_num:02d}.mp4"
            try:
                subprocess.run([PYTHON_EXE, str(RENDER_SCRIPT), str(img_path), str(json_path), str(video_output), str(HAND_IMG)], env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
            except subprocess.CalledProcessError as e:
                st.error(f"Lỗi render video cảnh {scene_num}:")
                st.code(e.stderr)
                st.stop()
            
            # Ghép Audio
            video_with_audio = OUTPUT_DIR / f"scene-{scene_num:02d}_audio.mp4"
            cmd_audio = [ffmpeg_path, "-y", "-i", str(video_output), "-i", str(audio_path), "-c:v", "copy", "-c:a", "aac", "-shortest", str(video_with_audio)]
            subprocess.run(cmd_audio, capture_output=True, check=True)
            video_clips.append(str(video_with_audio))
            
            progress_bar.progress((i + 1) / len(scenes))
            time.sleep(15)     

        # Ghi file phụ đề đồng bộ
        with open(synced_srt_path, "w", encoding="utf-8") as f:
            f.write(synced_srt_content)

        # Ghép các cảnh & Upload
        if video_clips:
            status_text.info("🔄 Đang ghép các cảnh...")
            final_output = OUTPUT_DIR / "FINAL_VIDEO.mp4"
            
            try:
                subprocess.run([PYTHON_EXE, str(MERGE_SCRIPT), str(final_output)] + video_clips, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
            except subprocess.CalledProcessError as e:
                st.error("Lỗi khi ghép video:")
                st.code(e.stderr)
                st.stop()
            
            file_to_upload = final_output
            
            # Chèn phụ đề (Sử dụng file synced.srt thay vì file gốc)
            if show_subtitles:
                status_text.info("🔄 Đang chèn phụ đề (Đã đồng bộ)...")
                final_with_subs = OUTPUT_DIR / "FINAL_VIDEO_SUBBED.mp4"
                srt_path_escaped = str(synced_srt_path).replace("\\", "/").replace(":", "\\:")
                
                sub_cmd = [
                    ffmpeg_path, "-y", "-i", str(final_output),
                    "-vf", f"subtitles='{srt_path_escaped}':force_style='FontName=Arial,FontSize={sub_fontsize},PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV={sub_margin}'",
                    "-c:v", "libx264", "-c:a", "aac", str(final_with_subs)
                ]
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