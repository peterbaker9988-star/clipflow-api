import os
import sys
import uuid
import subprocess
import whisper
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

OUTPUT_DIR = "/tmp/clips"
os.makedirs(OUTPUT_DIR, exist_ok=True)

model = whisper.load_model("tiny.en")

def format_srt_time(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

@app.route("/", methods=["GET"])
def home():
    return "ClipFlow Studio API is Running!"

@app.route("/clip", methods=["POST"])
def create_clip():
    data = request.json
    video_url = data.get("url", "").strip()
    start_sec = int(data.get("start", 60))
    duration = min(60, max(15, int(data.get("duration", 35))))
    hook_title = data.get("hook", "WAIT FOR THE END").upper().replace("'", "").replace(":", "")

    if not video_url:
        return jsonify({"error": "Please enter a valid video URL"}), 400

    job_id = str(uuid.uuid4())[:8]
    raw_path = os.path.join(OUTPUT_DIR, f"raw_{job_id}.mp4")
    srt_path = os.path.join(OUTPUT_DIR, f"sub_{job_id}.srt")
    final_path = os.path.join(OUTPUT_DIR, f"ClipFlow_{job_id}.mp4")

    try:
        cmd = [sys.executable, "-m", "yt_dlp", "-g", "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best", video_url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        urls = [u.strip() for u in res.stdout.strip().split("\n") if u.strip()]

        if not urls:
            return jsonify({"error": "Could not fetch video stream. Check URL."}), 400

        if len(urls) >= 2:
            dl_cmd = ["ffmpeg", "-y", "-ss", str(start_sec), "-t", str(duration), "-i", urls[0], "-ss", str(start_sec), "-t", str(duration), "-i", urls[1], "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", raw_path]
        else:
            dl_cmd = ["ffmpeg", "-y", "-ss", str(start_sec), "-t", str(duration), "-i", urls[0], "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", raw_path]
        subprocess.run(dl_cmd, check=True, timeout=90)

        result = model.transcribe(raw_path, word_timestamps=True, fp16=False)
        with open(srt_path, "w", encoding="utf-8") as f:
            idx = 1
            for seg in result.get("segments", []):
                words = seg.get("words", [])
                for i in range(0, len(words), 4):
                    chunk = words[i:i+4]
                    text = " ".join(w["word"].strip() for w in chunk).upper()
                    if text:
                        f.write(f"{idx}\n{format_srt_time(chunk[0]['start'])} --> {format_srt_time(chunk[-1]['end'])}\n{text}\n\n")
                        idx += 1

        sub_style = "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=55"
        if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
            vf = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=15:5[bg];"
                "[0:v]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                f"drawtext=text='{hook_title}':fontcolor=black:fontsize=44:box=1:boxcolor=white@0.95:boxborderw=22:x=(w-text_w)/2:y=250,"
                f"subtitles={srt_path}:force_style='{sub_style}'"
            )
        else:
            vf = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=15:5[bg];"
                "[0:v]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                f"drawtext=text='{hook_title}':fontcolor=black:fontsize=44:box=1:boxcolor=white@0.95:boxborderw=22:x=(w-text_w)/2:y=250"
            )

        subprocess.run(["ffmpeg", "-y", "-i", raw_path, "-filter_complex", vf, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", final_path], check=True, timeout=90)
        return send_file(final_path, as_attachment=True, download_name=f"ClipFlow_Short_{job_id}.mp4")

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(raw_path): os.remove(raw_path)
        if os.path.exists(srt_path): os.remove(srt_path)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
