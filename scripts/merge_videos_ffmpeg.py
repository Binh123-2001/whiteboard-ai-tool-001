import subprocess
import sys
import os
import tempfile

def merge_videos(video_files, output_file):
    # 1. Sử dụng thư mục tạm của hệ thống (để tránh lỗi phân quyền trên Cloud)
    list_file_path = os.path.join(tempfile.gettempdir(), "concat_list.txt")
    
    with open(list_file_path, "w", encoding="utf-8") as f:
        for video in video_files:
            abs_path = os.path.abspath(video)
            f.write(f"file '{abs_path}'\n")
    
    # 2. Gọi trực tiếp lệnh ffmpeg hệ thống (đã được cài qua file packages.txt)
    cmd = [
        "ffmpeg", 
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file_path,
        "-c", "copy",
        output_file
    ]
    
    try:
        print(f"Merging {len(video_files)} videos...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("Merge successful!")
    except subprocess.CalledProcessError as e:
        print("Merge error:")
        print(e.stderr) # In ra lỗi chi tiết nếu có
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