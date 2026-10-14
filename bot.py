import os, json, time, re, requests
from groq import Groq

SYSTEM = "0772023014"
TOKEN = f"{SYSTEM}:{os.environ['YEMOT_PASSWORD']}"
API = "https://www.call2all.co.il/ym/api"
IN_EXT = "2"
OUT_EXT = "1"           # שלוחה להודעות הקשורות לחסידות גור
UNRELATED_EXT = "0/1"   # שלוחה 1 בתוך 0 להודעות שאינן קשורות לחסידות גור

STATE_PATH = "ivr2:/9/state.json"   # המצב נשמר בימות
STT_MODEL = "whisper-large-v3-turbo"
STATE_VERSION = 11

# הודעה שמזכירה אחד מהשמות האלה לא תעלה
BLOCKED_NAMES = ["מוישי", "משה לייב פרידמן", "משה ליב פרידמן", "משה לייב פריידמן", "mשה ליב פריידמן"]
HOLD_SENSITIVE = True   # True = דיווחי פטירה/פציעה/פיגוע בלבד ממתינים לאישור
MAX_AGE_H = 24

RULES = ("אתה עורך מקצועי במערכת ניוז טלפונית חסידית. "
         "תפקידך להאזין לתמליל ההודעה ולנסח אותה מחדש בצורה קולחת, אנושית, זורמת ומקצועית לחלוטין, "
         "כאילו עורך תוכן אנושי ניסח אותה עבור השידור. "
         "הקפד על לשון נקייה ומכובדת, ושמור על כבוד כ\"ק מרן אדמו\"ר שליט\"א, הרבנים והחצר החסידית. "
         "אין להשתמש בפתיחים מלאכותיים כגון 'כתב המערכת' או 'כאן כתב'. "
         "התאם את ניסוח הידיעה בדיוק לאופי ההודעה המקורית: "
         "אם המאזין שואל שאלה או מברר פרט - נסח את הדיווח כשאלה/פניית מאזינים (לדוגמה: 'מאזינים מבקשים לדעת...'). "
         "אם זו בקשת עזרה/מידע, הודעת מערכת או סקופ חדשותי - נסח בהתאם לתוכן בצורה אנושית וטבעית.")

client = Groq(api_key=os.environ["GROQ_API_KEY"])

PREFERRED = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]

IDS = set()

def pick_model():
    global IDS
    try:
        ids = {m.id for m in client.models.list().data}
        IDS = ids
    except Exception as e:
        print("models list fail", e)
        return PREFERRED[0]
    for m in PREFERRED:
        if m in ids:
            return m
    return PREFERRED[0]

LLM_MODEL = pick_model()
print("LLM model:", LLM_MODEL)

def ask(system, user, as_json=False):
    kw = {"max_tokens": 400} if as_json else {"max_tokens": 250}
    r = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}], **kw)
    # השהיה קטנה למניעת חריגת OTPM (Rate limit)
    time.sleep(3)
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
    return {"v": STATE_VERSION, "seen": [], "pending": [], "held": [], "next": 242, "next_unrelated": 1, "last_decide": 0}

def save_state(s):
    y_text_upload(STATE_PATH, json.dumps(s, ensure_ascii=False))

# ---------- שלבים ----------
def transcribe(raw):
    r = client.audio.transcriptions.create(file=("a.wav", raw), model=STT_MODEL, language="he")
    return getattr(r, "text", r)

def triage(text):
    p = ('נתח הודעה של מאזין. החזר JSON בלבד:\n'
         '{\n'
         '  "empty": bool (שקט, רעש או לא מובן),\n'
         '  "against_system": bool (פגיעה, זלזול, ביקורת או קללה נגד הנהלת/מערכת הקו),\n'
         '  "sensitive": bool (דיווח על פטירה, פציעה קשה או פיגוע בלבד),\n'
         '  "related_to_gur": bool (האם יש קשר, אפילו רחוק, לחסידות גור, מוסדותיה, אנשיה, אירועיה או נושאים הנוגעים לה),\n'
         '  "topic": "", "summary": "", "facts": []\n'
         '}\n\nהודעה:\n' + text)
    return jparse(ask("אתה עורך תוכן חסידי.", p, as_json=True))

def norm(t):
    return " ".join(re.sub(r"[^\u05d0-\u05ea ]", " ", t).split())

def analyze_audio(raw):
    text = transcribe(raw)
    t = norm(text)
    if any(norm(n) in t for n in BLOCKED_NAMES):
        return {"blocked_name": True}
    if len(t) < 6:
        return {"empty": True}
    m = triage(text)
    m["empty"] = False
    return m

def is_quota(e):
    return "429" in str(e) or "rate_limit" in str(e).lower()

def decide(pending):
    items = [{"id": i["id"], "topic": i["topic"], "summary": i["summary"],
              "facts": i["facts"], "age_min": int((time.time() - i["ts"]) / 60)} for i in pending]
    p = ('קבץ את ההודעות לפי אירוע. החזר JSON: {"groups":[{"ids":[],"ready":bool}]}. '
         'ready=true לכל קבוצה, גם אם יש בה הודעה אחת בלבד.\n' + json.dumps(items, ensure_ascii=False))
    return jparse(ask("אתה עורך חדשות זהיר.", p, as_json=True)).get("groups", [{"ids": [i["id"] for i in pending], "ready": True}])

def write_story(group):
    src = json.dumps([{"summary": g["summary"], "facts": g["facts"]} for g in group], ensure_ascii=False)
    p = (f"{RULES}\n\n"
         f"מקורות מהמאזינים:\n{src}\n\n"
         "נסח את הידיעה בצורה אנושית ומקצועית לחלוטין. "
         'החזר JSON בלבד: {"publishable":true, "text":"טקסט הידיעה המנוסח בצורה טבעית וקולחת"}')
    return jparse(ask("אתה עורך ראשי במערכת ניוז חסידית.", p, as_json=True))

# ---------- ריצה ----------
def main():
    s = load_state()
    now = time.time()
    quota_hit = False
    new = 0

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
        new += 1
        if m.get("blocked_name"):
            print("msg", name, "שם-חסום-נחסם")
            continue
        hold = bool(m.get("sensitive")) and HOLD_SENSITIVE
        print("msg", name,
              "ריק" if m.get("empty") else "",
              "נגד-המערכת-נחסם" if m.get("against_system") else "",
              "רגיש-ממתין-לאישור" if hold else "",
              "קשור לחסידות גור:" if m.get("related_to_gur") else "לא קשור לגור")
        m.pop("transcript", None)
        if m.get("empty") or m.get("against_system"):
            continue
        m.update(id=name, ts=now)
        (s["held"] if hold else s["pending"]).append(m)

    s["pending"] = [p for p in s["pending"] if now - p["ts"] < MAX_AGE_H * 3600]

    if s["pending"] and not quota_hit and (new or now - s.get("last_decide", 0) > 1200):
        s["last_decide"] = now
        try:
            groups = decide(s["pending"])
        except Exception as e:
            print("decide fail", e); groups = []
            quota_hit = is_quota(e)
        by_id = {p["id"]: p for p in s["pending"]}
        for g in groups:
            if quota_hit or not g.get("ready"):
                continue
            group = [by_id[i] for i in g["ids"] if i in by_id]
            if not group:
                continue
            try:
                res = write_story(group)
            except Exception as e:
                print("write fail", e)
                if is_quota(e):
                    quota_hit = True
                continue
            if not res.get("publishable"):
                for p in group:
                    p["tries"] = p.get("tries", 0) + 1
                continue

            is_gur = any(p.get("related_to_gur") for p in group)

            if is_gur:
                n = s["next"]
                s["next"] += 1
                target_path = f"ivr2:/{OUT_EXT}/{n:03d}.tts"
                print("published (גור - שלוחה 1)", f"{n:03d}.tts")
            else:
                n = s.get("next_unrelated", 1)
                s["next_unrelated"] = n + 1
                target_path = f"ivr2:/{UNRELATED_EXT}/{n:03d}.tts"
                print("published (לא קשור - שלוחה 0/1)", f"{n:03d}.tts")

            y_text_upload(target_path, res["text"])
            s["pending"] = [p for p in s["pending"] if p["id"] not in g["ids"]]

        s["pending"] = [p for p in s["pending"] if p.get("tries", 0) < 3]

    if quota_hit:
        print("QUOTA: המכסה החינמית נגמרה, ננסה שוב בהרצה הבאה")
    if s["held"]:
        print("ממתינות לאישור ידני:", len(s["held"]))
    save_state(s)

if __name__ == "__main__":
    main()
