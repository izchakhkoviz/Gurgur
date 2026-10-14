import os, json, time, re, requests
from groq import Groq

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT = "2"
OUT_EXT = "1"           # שלוחה 1: עדכוני חדשות חסידות גור
UNRELATED_EXT = "0/1"   # שלוחה 0/1: הודעות כלליות בלבד

STATE_PATH = "ivr2:/9/state.json"
STT_MODEL = "whisper-large-v3-turbo"
STATE_VERSION = 23      # גרסה 23: שיפור פרומפט הניסוח למבזקים מקצועיים

BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן"]
HOLD_SENSITIVE = True
MAX_AGE_H = 24

RULES = ("אתה עורך חדשות ראשי בכיר בקו טלפוני חסידי מוביל. "
         "תפקידך לקחת את הנתונים או הפרטים שהעביר המאזין ולנסח אותם מחדש כמבזק חדשות טלפוני קולח, חד, מרתק ומקצועי לחלוטין. "
         "הקפד על לשון נקייה ומכובדת, ושמור על כבוד כ\"ק מרן אדמו\"ר שליט\"א, הרבנים והחצר החסידית. "
         "אל תכלול בפתיח מילים מיותרות כמו 'לפי אלפון ששוגר' או 'פורסם ב...'. הצג ישירות את הנתונים או הידיעה בצורה מכובדת.")

client = Groq(api_key=os.environ["GROQ_API_KEY"])

PREFERRED = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]

def pick_model():
    try:
        ids = {m.id for m in client.models.list().data}
        for m in PREFERRED:
            if m in ids:
                return m
    except Exception as e:
        print("models list fail", e)
    return PREFERRED[0]

LLM_MODEL = pick_model()
print("LLM model:", LLM_MODEL)

def ask(system, user, as_json=False):
    kw = {"max_tokens": 250} if as_json else {"max_tokens": 180}
    r = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}], **kw)
    time.sleep(4)
    return r.choices[0].message.content or ""

def jparse(t):
    try:
        t = re.sub(r"```(json)?", "", t).strip()
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1:
            return json.loads(t[start:end + 1])
    except Exception:
        pass
    return {}

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

    # מעבדים מחדש את 3 הקבצים האחרונים
    last_3 = all_files[-3:] if len(all_files) >= 3 else all_files
    seen_files = [f for f in all_files if f not in last_3]

    print(f"עיבוד מחדש בגרסה 23 של הקבצים: {last_3}")

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

def triage(text):
    p = ('נתח הודעת מאזין לקו חדשות. החזר JSON בלבד:\n'
         '{\n'
         '  "empty": bool (שקט, רעש או ג\'יבריש מוחלט ללא שום תוכן),\n'
         '  "against_system": bool (פגיעה, זלזול, ביקורת או קללה נגד הנהלת/מערכת הקו),\n'
         '  "sensitive": bool (דיווח על פטירה, פציעה קשה או פיגוע בלבד),\n'
         '  "related_to_gur": bool (האם יש קשר לחסידות גור, מוסדותיה, אנשיה או אירועיה),\n'
         '  "topic": "", "summary": "", "facts": []\n'
         '}\n\nהודעה:\n' + text)
    res = jparse(ask("אתה עורך תוכן חסידי.", p, as_json=True))
    res.setdefault("topic", "")
    res.setdefault("summary", "")
    res.setdefault("facts", [])
    return res

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
    m = triage(text)
    return m

def is_quota(e):
    return "429" in str(e) or "rate_limit" in str(e).lower()

def write_story_single(item):
    summary = item.get("summary", "")
    facts = item.get("facts", [])
    src = json.dumps({"summary": summary, "facts": facts}, ensure_ascii=False)
    p = (f"{RULES}\n\n"
         f"נתונים ועובדות מהמאזין:\n{src}\n\n"
         "נסח את הדברים כמבזק חדשות אקטואלי, קולח וברור להקראה טלפונית. "
         'החזר JSON בלבד: {"publishable":true, "text":"טקסט הידיעה המנוסח"}')
    return jparse(ask("אתה עורך ראשי במערכת ניוז חסידית.", p, as_json=True))

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
        hold = bool(m.get("sensitive")) and HOLD_SENSITIVE
        print("msg", name,
              "ריק" if m.get("empty") else "",
              "נגד-המערכת-נחסם" if m.get("against_system") else "",
              "רגיש-ממתין-לאישור" if hold else "")
        if m.get("empty") or m.get("against_system"):
            continue
        m.update(id=name, ts=now)
        (s["held"] if hold else s["pending"]).append(m)

    s["pending"] = [p for p in s["pending"] if now - p["ts"] < MAX_AGE_H * 3600]

    if s["pending"] and not quota_hit:
        processed_ids = []
        for item in list(s["pending"]):
            if quota_hit:
                break
            try:
                res = write_story_single(item)
            except Exception as e:
                print("write fail for", item["id"], e)
                if is_quota(e):
                    quota_hit = True
                continue

            text_out = res.get("text", "")
            if not res.get("publishable") or not text_out:
                print("הודעה נפסלה:", item["id"])
                item["tries"] = item.get("tries", 0) + 1
                continue

            is_gur = item.get("related_to_gur", True)

            if is_gur:
                n = s["next"]
                s["next"] += 1
                target_path = f"ivr2:/{OUT_EXT}/{n:03d}.tts"
                print(f"published ({item['id']} -> שלוחה 1):", f"{n:03d}.tts")
            else:
                n = s.get("next_unrelated", 1)
                s["next_unrelated"] = n + 1
                target_path = f"ivr2:/{UNRELATED_EXT}/{n:03d}.tts"
                print(f"published ({item['id']} -> שלוחה 0/1):", f"{n:03d}.tts")

            y_text_upload(target_path, text_out)
            processed_ids.append(item["id"])

        s["pending"] = [p for p in s["pending"] if p["id"] not in processed_ids]

    if quota_hit:
        print("QUOTA: המכסה החינמית נגמרה, ננסה שוב בהרצה הבאה")
    if s["held"]:
        print("ממתינות לאישור ידני:", len(s["held"]))
    save_state(s)

if __name__ == "__main__":
    main()
