import os, json, time, re, requests
from groq import Groq

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT = "2"
OUT_EXT = "1"           # שלוחה 1: עדכוני חדשות שוטפים של חסידות גור
UNRELATED_EXT = "0/1"   # שלוחה 0/1: שלוחת ניהול אישית לכל שאר ההודעות

STATE_PATH = "ivr2:/9/state.json"
STT_MODEL = "whisper-large-v3-turbo"
STATE_VERSION = 33      # גרסה 33: עיבוד חופשי של כל הקבצים הממתינים ללא שום הגבלה

BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן"]

RULES = ("אתה עורך חדשות ראשי בכיר בקו טלפוני חסידי מוביל. "
         "תפקידך לקחת את התמלול והנתונים שהעביר המאזין ולנסח אותם כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         "תקן שגיאות תמלול ומילים לא ברורות לפי ההקשר הלוגי. "
         "הקפד על לשון נקייה ומכובדת, ושמור על כבוד כ\"ק מרן אדמו\"ר שליט\"א והחצר החסידית. "
         "החזר אך ורק את טקסט הידיעה המנוסח, ללא פתיחים וללא הערות נוספות.")

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

# ---------- ימות ----------
def y_list(ext):
    r = requests.get(f"{API}/GetIVR2Dir", params={"token": TOKEN, "path": f"ivr2:/{ext}"}).json()
    files = [f["name"] for f in r.get("files", []) if f["name"].lower().endswith(".wav")]
    return sorted(files, key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0)

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
        "seen": [],
        "next": 242,
        "next_unrelated": 1
    }

def save_state(s):
    y_text_upload(STATE_PATH, json.dumps(s, ensure_ascii=False))

# ---------- שלבים ----------
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
    
    is_gur = any(k in text for k in ["גור", "אדמור", "חסידי", "חסידות", "בית מדרש", "שטיבל", "חצר"])
    return {"empty": False, "raw_text": text, "related_to_gur": is_gur}

def write_story_single(item):
    raw_text = item.get("raw_text", "")
    p = (f"תמלול המאזין:\n{raw_text}\n\n"
         "נסח את הידיעה כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         "תקן שגיאות תמלול לפי ההקשר. החזר אך ורק את טקסט הידיעה המנוסח.")
    res = ask(RULES, p).strip()
    if not res or len(res) < 2:
        return raw_text
    return res

# ---------- ריצה ----------
def main():
    s = load_state()
    files = y_list(IN_EXT)
    
    # לוקח את כל הקבצים שטרם עובדו ללא שום הגבלה
    unprocessed = [f for f in files if f not in s["seen"]]
    print(f"סה''כ קבצים ממתינים לעיבוד בריצה זו: {len(unprocessed)}")

    if not unprocessed:
        print("אין קבצים חדשים לעיבוד.")
        return

    for name in unprocessed:
        s["seen"].append(name)
        try:
            print(f"מעבד את קובץ: {name}")
            m = analyze_audio(y_download(f"ivr2:/{IN_EXT}/{name}"))
        except Exception as e:
            print("שגיאה בניתוח הקובץ", name, e)
            continue
        
        if m.get("blocked_name"):
            print("הודעה נחסמה (שם חסום):", name)
            continue
        if m.get("empty"):
            print("הודעה ריקה:", name)
            continue
            
        text_out = write_story_single(m)
        is_gur = m.get("related_to_gur", False)

        if is_gur:
            n = s["next"]
            s["next"] += 1
            target_path = f"ivr2:/{OUT_EXT}/{n:03d}.tts"
            print(f"PUBLISHING ({name} -> שלוחה 1 - גור): {n:03d}.tts")
        else:
            n = s.get("get_unrelated", 1) if "get_unrelated" in s else s.get("next_unrelated", 1)
            s["next_unrelated"] = n + 1
            target_path = f"ivr2:/{UNRELATED_EXT}/{n:03d}.tts"
            print(f"PUBLISHING ({name} -> שלוחה 0/1 - ניהול): {n:03d}.tts")

        y_text_upload(target_path, text_out)

    save_state(s)

if __name__ == "__main__":
    main()
