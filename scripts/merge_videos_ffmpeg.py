import subprocess
import sys
import os
import shutil
from static_ffmpeg import run as ffmpeg_run

def merge_videos(video_files, output_file):
    try:
        ffmpeg_path, _ = ffmpeg_run.get_or_fetch_platform_executables_else_raise()
    except Exception as e:
        sys.stderr.write(f"Lỗi tải FFmpeg: {e}\n")
        sys.exit(1)

    # 1. Tạo thư mục tạm với tên an toàn (không dấu, không khoảng trắng)
    base_dir = os.path.dirname(os.path.abspath(output_file))
    temp_dir = os.path.join(base_dir, "temp_merge")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)

    # 2. Sao chép các file video vào thư mục tạm và đổi tên đơn giản
    safe_paths = []
    for i, video in enumerate(video_files):
        safe_name = f"input_{i}.mp4"
        safe_path = os.path.join(temp_dir, safe_name)
        try:
            shutil.copy(video, safe_path)
            # Chuyển sang đường dẫn tuyệt đối và dùng dấu / để FFmpeg hiểu
            safe_paths.append(safe_path.replace("\\", "/"))
        except Exception as e:
            sys.stderr.write(f"Lỗi copy file {video}: {e}\n")
            sys.exit(1)

    # 3. Xây dựng lệnh FFmpeg với các đường dẫn an toàn
    inputs = []
    filter_parts = []
    for i, safe_path in enumerate(safe_paths):
        inputs.extend(["-i", f'"{safe_path}"'])  # Bọc trong ngoặc kép để an toàn tuyệt đối
        filter_parts.append(f"[{i}:v][{i}:a]")
    
    filter_complex = "".join(filter_parts) + f"concat=n={len(safe_paths)}:v=1:a=1[outv][outa]"
    
    cmd = [
        ffmpeg_path, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-c:a", "aac",
        f'"{output_file}"'
    ]
    
    try:
        print(f"Merging {len(safe_paths)} videos...")
        # Chạy lệnh, không cần shell=True vì đã xử lý đường dẫn cẩn thận
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
        print("Merge successful!")
    except subprocess.CalledProcessError as e:
        sys.stderr.write("=== FFmpeg Failed ===\n")
        sys.stderr.write(e.stderr)
        sys.exit(1)
    finally:
        # Dọn dẹp thư mục tạm sau khi xong việc
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
    output = sys.argv[1]
    inputs = sys.argv[2:]
    merge_videos(inputs, output)