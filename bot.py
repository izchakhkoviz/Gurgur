import os, json, time, re, requests
from groq import Groq

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT = "2"
OUT_EXT = "1"           # שלוחה 1: עדכוני חדשות שוטפים (גור)
UNRELATED_EXT = "0/1"   # שלוחה 0/1: הודעות כלליות / ניהול (ללא צינטוקים)

STATE_PATH = "ivr2:/9/state.json"
STT_MODEL = "whisper-large-v3-turbo"
STATE_VERSION = 33

BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן"]

RULES = ("אתה עורך חדשות ראשי בכיר בקו טלפוני חסידי מוביל של חסידות גור. "
         "תפקידך לקחת את התמלול והנתונים שהעביר המאזין ולנסח אותם כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         "תקן שגיאות תמלול ומילים לא ברורות לפי ההקשר הלוגי. "
         "הקפד על לשון נקייה ומכובדת, ושמור על כבוד כ\"ק מרן אדמו\"ר שליט\"א והחצר החסידית. "
         "החזר אך ורק את טקסט הידיעה המנוסח, ללא פתיחים וללא הערות נוספות.")

DETECTION_PROMPT = ("נתח את המבזק/התמלול הבא מתוך קו נייעס חסידי. "
                    "קבע האם הידיעה מוגדרת כ'חריגה' או 'דחופה' בעלת השפעה מיידית. "
                    "דוגמאות לחריג/דחוף: שינוי פתאומי בלו\"ז הקודש, יציאה בלתי צפויה לתפילת שחרית/מנחה, ביטול קבלת קהל, או אירוע חרום/משמעותי שקורה עכשיו. "
                    "החזר תשובה במבנה JSON בלבד בפורמט הבא:\n"
                    "{\"is_urgent\": true/false, \"reason\": \"הסבר קצר\"}")

client = Groq(api_key=os.environ["GROQ_API_KEY"])
PREFERRED = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]

def pick_model():
    try:
        ids = {m.id for m in client.models.list().data}
        for m in PREFERRED:
            if m in ids:
                return m
    except Exception:
        pass
    return PREFERRED[0]

LLM_MODEL = pick_model()
print("LLM model:", LLM_MODEL)

def ask(system, user):
    try:
        r = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=350)
        time.sleep(1)
        return r.choices[0].message.content or ""
    except Exception as e:
        print("LLM error:", e)
        return ""

def trigger_tzintuk(ext_number):
    """הפעלת צינטוק לרשימה דרך הרצת השלוחה המתאימה בימות המשיח."""
    try:
        url = f"{API}/RunExtensions"
        params = {"token": TOKEN, "ext": ext_number}
        r = requests.get(url, params=params)
        print(f"TZINTUK TRIGGERED (Ext {ext_number}):", r.status_code, r.text)
    except Exception as e:
        print(f"Error triggering tzintuk on ext {ext_number}:", e)

# ---------- ימות ----------
def y_list(ext):
    r = requests.get(f"{API}/GetIVR2Dir", params={"token": TOKEN, "path": f"ivr2:/{ext}"}).json()
    files = [f["name"] for f in r.get("files", []) if f["name"].lower().endswith((".wav", ".tts"))]
    return files

def get_next_available_index(ext):
    """סורק את התיקייה בימות המשיח, מוצא את המספר הגבוה ביותר שקיים ומחזיר את הבא בתור."""
    files = y_list(ext)
    max_num = 0
    for f in files:
        match = re.search(r'(\d+)', f)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    return max_num + 1

def y_download(path):
    return requests.get(f"{API}/DownloadFile", params={"token": TOKEN, "path": path}).content

def y_text_upload(path, text):
    r = requests.post(f"{API}/UploadTextFile", data={"token": TOKEN, "what": path, "contents": text})
    print(f"UPLOAD RESULT ({path}):", r.status_code, r.text)

def load_state():
    try:
        s = json.loads(y_download(STATE_PATH))
        if s.get("v") == STATE_VERSION:
            return s
    except Exception:
        pass

    print("מאתחל מצב חדש לגרסה 33...")
    return {
        "v": STATE_VERSION,
        "seen": []
    }

def save_state(s):
    y_text_upload(STATE_PATH, json.dumps(s, ensure_ascii=False))

# ---------- ניתוח ותמלול ----------
def transcribe(raw):
    r = client.audio.transcriptions.create(file=("a.wav", raw), model=STT_MODEL, language="he")
    return getattr(r, "text", r)

def norm(t):
    return " ".join(re.sub(r"[^\u05d0-\u05ea ]", " ", t).split())

def analyze_audio(raw):
    text = transcribe(raw)
    print("תמלול שהתקבל:", text)
    t = norm(text)
    if any(norm(n) in t for n in BLOCKED_NAMES):
        return {"blocked_name": True}
    if len(t) < 2:
        return {"empty": True}

    is_gur = any(k in text for k in ["גור", "אדמור", "חסידי", "חסידות", "בית מדרש", "שטיבל", "חצר", "שחרית", "מנחה", "מעריב"])
    return {"empty": False, "raw_text": text, "related_to_gur": is_gur}

def check_if_urgent_llm(text):
    res = ask(DETECTION_PROMPT, text)
    try:
        match = re.search(r'\{.*\}', res, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("is_urgent", False)
    except Exception as e:
        print("שגיאה בפילוש תשובת דחיפות:", e)
    return False

def write_story_single(item):
    raw_text = item.get("raw_text", "")
    p = (f"תמלול המאזין:\n{raw_text}\n\n"
         "נסח את הידיעה כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         "תקן שגיאות תמלול לפי ההקשר. החזר אך ורק את טקסט הידיעה המנוסח.")
    res = ask(RULES, p).strip()
    if not res or len(res) < 2:
        return raw_text
    return res

# ---------- ריצה ראשית ----------
def main():
    s = load_state()
    files = y_list(IN_EXT)

    unprocessed = [f for f in files if f not in s["seen"]]
    print(f"סה''כ קבצים ממתינים לעיבוד בריצה זו: {len(unprocessed)}")

    if not unprocessed:
        print("אין קבצים חדשים לעיבוד.")
        return

    processed_items = []
    has_regular_updates = False
    has_urgent_updates = False

    # שלב 1: תמלול ומיון ראשוני של כל הקבצים
    for name in unprocessed:
        s["seen"].append(name)
        try:
            print(f"מעבד את קובץ: {name}")
            raw_data = y_download(f"ivr2:/{IN_EXT}/{name}")
            m = analyze_audio(raw_data)
        except Exception as e:
            print("שגיאה בניתוח הקובץ", name, e)
            continue

        if m.get("blocked_name") or m.get("empty"):
            continue

        processed_items.append(m)

    is_multi_reports = len(processed_items) >= 2

    # שלב 2: בדיקת המספר הפנוי האמיתי מתוך התיקיות לפני כל העלאה
    for m in processed_items:
        text_out = write_story_single(m)
        is_gur = m.get("related_to_gur", False)

        if is_gur:
            is_urgent_content = check_if_urgent_llm(text_out)
            is_urgent = is_urgent_content or is_multi_reports

            next_idx = get_next_available_index(OUT_EXT)
            target_path = f"ivr2:/{OUT_EXT}/{next_idx:03d}.tts"
            print(f"PUBLISHING ({next_idx:03d}.tts) -> שלוחה 1 (דחוף={is_urgent})")

            if is_urgent:
                has_urgent_updates = True
            else:
                has_regular_updates = True
        else:
            next_idx = get_next_available_index(UNRELATED_EXT)
            target_path = f"ivr2:/{UNRELATED_EXT}/{next_idx:03d}.tts"
            print(f"PUBLISHING ({next_idx:03d}.tts) -> שלוחה 0/1 (ניהול - ללא צינטוק)")

        y_text_upload(target_path, text_out)

    save_state(s)

    # שלב 3: הפעלת צינטוקים אך ורק עבור שלוחה 1
    if has_urgent_updates:
        print("זוהו עדכוני גור חריגים/דחופים! מפעיל צינטוק בשלוחה 400...")
        trigger_tzintuk("400")
    elif has_regular_updates:
        print("זוהו עדכוני גור רגילים. מפעיל צינטוק בשלוחה 200...")
        trigger_tzintuk("200")

if __name__ == "__main__":
    main()
