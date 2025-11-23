import os
import time
import json
import textwrap
import base64
import requests

#For Image storage
from google.cloud import storage
import uuid
#

from flask import Flask, request, jsonify
from PIL import Image, ImageDraw, ImageFont

# =========================
# CONFIG
# =========================

#add the storage bucket name
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "moviequiz-images")

OPENAI_API_KEY = os.environ.get("euF6_LSTuLogImccSsJWejMN7bClY7dOduXJtzUGNLxk2AkVHDmnZT3BlbkFJ_yUXawTsxG9q_jdC5mefZzaGLuCLhR1k5nIWhemT8cnOsq7_arnZ6pGOatTMZalf3i8y9USK4A")
LEONARDO_API_KEY = os.environ.get("0042aded-1bbc-4a65-bcf8-4d80703c5df8")

WIDTH, HEIGHT = 1080, 1080
LEONARDO_MODEL_ID = "e316348f-7773-490e-adcd-46757c738eb7"  # cinematic model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

TEMPLATE_PATH = os.path.join(ASSETS_DIR, "template.png")
ROBOTO_SLAB_REG = os.path.join(ASSETS_DIR, "RobotoSlab-Regular.ttf")
ROBOTO_SLAB_BOLD = os.path.join(ASSETS_DIR, "RobotoSlab-Bold.ttf")

app = Flask(__name__)


# =========================
# FONT + DRAW HELPERS
# =========================

def load_font(size: int, bold: bool = False):
    """
    Load Roboto Slab at the given size.
    If something goes wrong, fall back to a system TTF or Pillow's default.
    """
    preferred_path = ROBOTO_SLAB_BOLD if bold else ROBOTO_SLAB_REG

    if os.path.exists(preferred_path):
        try:
            return ImageFont.truetype(preferred_path, size)
        except OSError:
            print(f"⚠️ Could not load {preferred_path}, will try system fonts.")

    for root, _, files in os.walk("/usr/share/fonts"):
        for fname in files:
            if fname.lower().endswith(".ttf"):
                try:
                    path = os.path.join(root, fname)
                    print("✅ Using fallback system font:", path)
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue

    print("⚠️ Using default font (no TTF could be loaded).")
    return ImageFont.load_default()


def add_overlay(base_img, overlay_img, opacity=0.3):
    overlay = overlay_img.resize(base_img.size).convert("RGBA")
    alpha = int(255 * opacity)
    overlay.putalpha(alpha)
    base = base_img.convert("RGBA")
    base.alpha_composite(overlay)
    return base


def get_text_size(draw, text, font):
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return w, h


def draw_text_block(draw, text, font, x, y, max_width, line_spacing=10, fill=(255, 255, 255)):
    lines = []
    for line in text.split("\n"):
        if line.strip() == "":
            lines.append("")
        else:
            lines.extend(textwrap.wrap(line, width=38))

    current_y = y
    for line in lines:
        if line == "":
            current_y += line_spacing
            continue
        w, h = get_text_size(draw, line, font)
        draw.text((x + (max_width - w) / 2, current_y), line, font=font, fill=fill)
        current_y += h + line_spacing


def make_quiz_image(template_path, overlay_path, question, options, output_path):
    base = Image.open(template_path).convert("RGBA").resize((WIDTH, HEIGHT))
    overlay = Image.open(overlay_path)

    base = add_overlay(base, overlay, opacity=0.3)
    draw = ImageDraw.Draw(base)

    title_font    = load_font(62, bold=True)
    question_font = load_font(51, bold=True)
    options_font  = load_font(47, bold=False)

    # Title
    title_text = "Movie Quiz"
    title_w, _ = get_text_size(draw, title_text, title_font)
    draw.text(((WIDTH - title_w) / 2, 100), title_text, font=title_font, fill=(255,255,255))

    # Question
    question_area_x = 80
    question_area_y = 320
    question_area_width = WIDTH - 2 * question_area_x

    draw_text_block(
        draw,
        question,
        font=question_font,
        x=question_area_x,
        y=question_area_y,
        max_width=question_area_width,
        line_spacing=10,
        fill=(255,255,255),
    )

    # Options
    options_text = "\n".join(options)
    options_area_x = 80
    options_area_y = 500
    options_area_width = WIDTH - 2 * options_area_x

    draw_text_block(
        draw,
        options_text,
        font=options_font,
        x=options_area_x,
        y=options_area_y,
        max_width=options_area_width,
        line_spacing=6,
        fill=(255,255,255),
    )

    base.convert("RGB").save(output_path, format="PNG")
    return output_path

#Helper to upload to google storage#
def upload_to_gcs(local_path, bucket_name=GCS_BUCKET_NAME):
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    file_id = f"{uuid.uuid4()}.png"
    blob = bucket.blob(f"quiz_posts/{file_id}")
    blob.upload_from_filename(local_path)
    blob.make_public()

    return blob.public_url


# =========================
# OPENAI: QUIZ + LEONARDO PROMPT + CAPTION
# =========================

def get_quiz_from_gpt(topic: str, language: str = "greek"):
    """
    Returns:
    {
      "question": "...",
      "options": [...],
      "leonardo_prompt": "...",
      "caption_line": "one sentence + 6 hashtags"
    }
    """
    url = "https://api.openai.com/v1/chat/completions"

    system_prompt = (
        "You are an Instagram movie quiz generator. "
        "Return ONLY valid JSON, no explanation. "
        "JSON schema:\n"
        "{\n"
        '  "question": "string",\n'
        '  "options": ["A) ...", "B) ...", "C) ...", "D) ..."],\n'
        '  "leonardo_prompt": "string for cinematic background, no text, no logos",\n'
        '  "caption_line": "one short sentence for the post, ending with exactly 6 hashtags"\n'
        "}\n"
        "The quiz should be about movies."
    )

    if language.lower().startswith("gr"):
        user_prompt = f"""
Create ONE quiz in Greek about movies.
Topic: "{topic}".

Rules:
- "question" in Greek.
- "options" in Greek (labels A), B), Γ), Δ)).
- Keep options relatively short.
- "leonardo_prompt" in ENGLISH, describing a cinematic movie-related background (no text, no logos).
- "caption_line" in Greek:
    - 1 σύντομη πρόταση, playful αλλά όχι cringe.
    - Στο τέλος της ίδιας γραμμής, βάλε ακριβώς 6 hashtags (separated by spaces).
Return ONLY valid JSON according to the schema.
"""
    else:
        user_prompt = f"""
Create ONE quiz in English about movies.
Topic: "{topic}".

Rules:
- "question" in English.
- "options" in English ("A) ...", "B) ...", "C) ...", "D) ...").
- "leonardo_prompt" in ENGLISH, describing a cinematic movie-related background (no text, no logos).
- "caption_line" in English:
    - 1 short, engaging sentence.
    - At the end of the SAME line, add exactly 6 hashtags (space separated).
Return ONLY valid JSON according to the schema.
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
    }

    resp = requests.post(url, headers=headers, data=json.dumps(data))
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()

    try:
        quiz = json.loads(content)
    except json.JSONDecodeError:
        if "```" in content:
            parts = content.split("```")
            if len(parts) >= 2:
                inner = parts[1]
                if inner.strip().lower().startswith("json"):
                    inner = "\n".join(inner.split("\n")[1:])
                quiz = json.loads(inner)
            else:
                raise
        else:
            raise

    return quiz


# =========================
# LEONARDO: OVERLAY
# =========================

def generate_overlay_from_leonardo(prompt: str, output_path: str) -> str:
    base_url = "https://cloud.leonardo.ai/api/rest/v1"
    headers = {
        "Authorization": f"Bearer {LEONARDO_API_KEY}",
        "Content-Type": "application/json",
    }

    gen_payload = {
        "prompt": prompt,
        "modelId": LEONARDO_MODEL_ID,
        "width": 1024,
        "height": 1024,
        "num_images": 1,
        "presetStyle": "CINEMATIC",
    }

    r = requests.post(f"{base_url}/generations", headers=headers, json=gen_payload)
    r.raise_for_status()
    generation_id = r.json()["sdGenerationJob"]["generationId"]

    while True:
        time.sleep(5)
        r2 = requests.get(f"{base_url}/generations/{generation_id}", headers=headers)
        r2.raise_for_status()
        data = r2.json()
        gen = data.get("generations_by_pk")
        if gen and gen.get("generated_images"):
            img_url = gen["generated_images"][0]["url"]
            break

    img_resp = requests.get(img_url)
    img_resp.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(img_resp.content)

    return output_path


# =========================
# HTTP ENDPOINT
# =========================

@app.route("/generate-quiz", methods=["POST"])
@app.route("/generate-quiz", methods=["POST"])
def generate_quiz_endpoint():
    """
    POST JSON:
    {
      "topic": "Χριστουγεννιάτικες ταινίες",
      "language": "greek"
    }
    """
    # Διαβάζουμε τα keys από τα env vars κάθε φορά, για σιγουριά
    global OPENAI_API_KEY, LEONARDO_API_KEY
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    LEONARDO_API_KEY = os.environ.get("LEONARDO_API_KEY")

    # Αν λείπουν, μην κόβεις τη ροή – απλά κάνε log.
    if not OPENAI_API_KEY or not LEONARDO_API_KEY:
        print(
            "⚠️ API keys check:",
            {
                "has_openai": bool(OPENAI_API_KEY),
                "has_leonardo": bool(LEONARDO_API_KEY),
            },
        )
        # συνεχίζουμε – OpenAI/Leonardo θα επιστρέψουν πιο συγκεκριμένο error

    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "Χριστουγεννιάτικες ταινίες")
    language = data.get("language", "greek")

    try:
        quiz = get_quiz_from_gpt(topic, language=language)
        question = quiz["question"]
        options = quiz["options"]
        caption_line = quiz.get("caption_line", "")
        leonardo_prompt = quiz["leonardo_prompt"]

        overlay_path = "/tmp/overlay_auto.jpg"
        generate_overlay_from_leonardo(leonardo_prompt, overlay_path)

        final_path = "/tmp/quiz_post.png"
        make_quiz_image(TEMPLATE_PATH, overlay_path, question, options, final_path)

        image_url = upload_to_gcs(final_path)

        return jsonify({
            "topic": topic,
            "language": language,
            "question": question,
            "options": options,
            "caption_line": caption_line,
            "leonardo_prompt": leonardo_prompt,
            "image_url": image_url
        })

    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    # For local testing
    app.run(host="0.0.0.0", port=8080, debug=True)
