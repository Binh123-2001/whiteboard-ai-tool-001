import subprocess
import sys
import os
import tempfile

def merge_videos(video_files, output_file):
    # Trên môi trường Cloud, ffmpeg đã có sẵn trong hệ thống
    ffmpeg_bin = "ffmpeg"

    # Tạo file danh sách trong thư mục tạm
    list_file_path = os.path.join(tempfile.gettempdir(), "concat_list.txt")
    
    with open(list_file_path, "w", encoding="utf-8") as f:
        for video in video_files:
            # Lấy đường dẫn tuyệt đối
            abs_path = os.path.abspath(video).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")
    
    # Mã hóa lại để đảm bảo các video có cùng thông số (tránh lỗi giật lag)
    cmd = [
        ffmpeg_bin, 
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file_path,
        "-c:v", "libx264", 
        "-c:a", "aac",
        output_file
    ]
    
    try:
        print(f"Merging {len(video_files)} videos...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
        print("Merge successful!")
    except subprocess.CalledProcessError as e:
        sys.stderr.write("=== FFmpeg Failed ===\n")
        sys.stderr.write(e.stderr)
        sys.exit(1)
    finally:
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
    output = sys.argv[1]
    inputs = sys.argv[2:]
    merge_videos(inputs, output)