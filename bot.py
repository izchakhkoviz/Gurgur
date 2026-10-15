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
STATE_VERSION = 27      # גרסה 27: גיבוי תמלול מקורי במקרה שהניסוח ריק

BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן"]
HOLD_SENSITIVE = True
MAX_AGE_H = 24

RULES = ("אתה עורך חדשות ראשי בכיר בקו טלפוני חסידי מוביל. "
         "תפקידך לקחת את התמלול והנתונים שהעביר המאזין ולנסח אותם כמבזק חדשות טלפוני קולח, ברור ומקצועי. "
         "הקפד על לשון נקייה ומכובדת, ושמור על כבוד כ\"ק מרן אדמו\"ר שליט\"א והחצר החסידית. "
         "החזר אך ורק את טקסט הידיעה המנוסח.")

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
            max_tokens=300)
        time.sleep(2)
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
    requests.post(f"{API}/UploadTextFile", data={"token": TOKEN, "what": path, "contents": text})

def load_state():
    all_files = []
    try:
        all_files = y_list(IN_EXT)
    except Exception:
        pass

    try:
        s = json.loads(y_download(STATE_PATH))
        if "pending" in s and s.get("v") == STATE_VERSION:
            if s.get("next", 0) < 242:
                s["next"] = 242
            if s.get("next_unrelated", 0) < 1:
                s["next_unrelated"] = 1
            return s
    except Exception:
        pass

    last_3 = all_files[-3:] if len(all_files) >= 3 else all_files
    seen_files = [f for f in all_files if f not in last_3]

    print(f"עיבוד מחדש בגרסה 27 של הקבצים: {last_3}")

    return {
        "v": STATE_VERSION,
        "seen": seen_files,
        "pending": [],
        "held": [],
        "next": 242,
        "next_unrelated": 1,
        "last_decide": 0
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
    if len(t) < 4:
        return {"empty": True}

    is_gur = any(k in text for k in ["גור", "אדמור", "חסידי", "חסידות", "בית מדרש", "שטיבל"])
    return {"empty": False, "raw_text": text, "related_to_gur": is_gur}

def is_quota(e):
    return "429" in str(e) or "rate_limit" in str(e).lower()

def write_story_single(item):
    raw_text = item.get("raw_text", "")
    p = (f"תמלול המאזין:\n{raw_text}\n\n"
         "נסח את הידיעה כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         "תקן שגיאות תמלול לפי ההקשר. החזר אך ורק את הניסוח הסופי.")
    res = ask(RULES, p).strip()
    # גיבוי: אם הניסוח מסיבה כלשהי ריק, נשתמש ישירות בתמלול המקורי של המאזין!
    if not res or len(res) < 3:
        return raw_text
    return res

# ---------- ריצה ----------
def main():
    s = load_state()
    now = time.time()
    quota_hit = False

    for name in y_list(IN_EXT):
        if name in s["seen"]:
            continue
        try:
            m = analyze_audio(y_download(f"ivr2:/{IN_EXT}/{name}"))
        except Exception as e:
            print("fail", name, e)
            if is_quota(e):
                quota_hit = True
                break
            s.setdefault("fails", {})
            s["fails"][name] = s["fails"].get(name, 0) + 1
            if s["fails"][name] >= 3:
                s["seen"].append(name)
            continue

        s["seen"].append(name)
        if m.get("blocked_name"):
            print("msg", name, "שם-חסום-נחסם")
            continue
        if m.get("empty"):
            print("msg", name, "ריק")
            continue

        m.update(id=name, ts=now)
        s["pending"].append(m)

    s["pending"] = [p for p in s["pending"] if now - p["ts"] < MAX_AGE_H * 3600]

    if s["pending"] and not quota_hit:
        processed_ids = []
        for item in list(s["pending"]):
            if quota_hit:
                break
            try:
                text_out = write_story_single(item)
            except Exception as e:
                print("write fail for", item["id"], e)
                if is_quota(e):
                    quota_hit = True
                continue

            is_gur = item.get("related_to_gur", False)

            if is_gur:
                n = s["next"]
                s["next"] += 1
                target_path = f"ivr2:/{OUT_EXT}/{n:03d}.tts"
                print(f"published ({item['id']} -> שלוחה 1 - גור):", f"{n:03d}.tts")
            else:
                n = s.get("next_unrelated", 1)
                s["next_unrelated"] = n + 1
                target_path = f"ivr2:/{UNRELATED_EXT}/{n:03d}.tts"
                print(f"published ({item['id']} -> שלוחה 0/1 - ניהול):", f"{n:03d}.tts")

            y_text_upload(target_path, text_out)
            processed_ids.append(item["id"])

        s["pending"] = [p for p in s["pending"] if p["id"] not in processed_ids]

    if quota_hit:
        print("QUOTA: המכסה החינמית נגמרה, ננסה שוב בהרצה הבאה")
    save_state(s)

if __name__ == "__main__":
    main()
