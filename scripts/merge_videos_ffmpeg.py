import subprocess
import sys
import os
from static_ffmpeg import run
ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()

def merge_videos(video_files, output_file):
    list_file_path = "concat_list.txt"
    with open(list_file_path, "w", encoding="utf-8") as f:
        for video in video_files:
            abs_path = os.path.abspath(video)
            f.write(f"file '{abs_path}'\n")
    
    # Thay vì dùng -c copy, chúng ta dùng re-encode để đồng nhất dữ liệu
    # Điều này ngăn chặn hoàn toàn lỗi video bị treo hoặc nhảy cóc thời gian
    cmd = [
        ffmpeg_path, 
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file_path,
        "-c:v", "libx264",  # Mã hóa lại video
        "-c:a", "aac",      # Mã hóa lại âm thanh
        "-strict", "experimental",
        output_file
    ]
    
    try:
        print(f"Merging {len(video_files)} videos...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("Merge successful!")
    except subprocess.CalledProcessError as e:
        print("Merge error:")
        print(e.stderr)
        sys.exit(1)
    finally:
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python merge_videos_ffmpeg.py <output_file> <input1> <input2> ...")
        sys.exit(1)
    
    output = sys.argv[1]
    inputs = sys.argv[2:]
    merge_videos(inputs, output)