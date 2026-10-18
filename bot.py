import os
import re
import json
import subprocess
import difflib
from datetime import datetime, timezone, timedelta
import requests
from groq import Groq

YEMOT_USERNAME = os.getenv("PHONE_SYSTEM_DID")
YEMOT_PASSWORD = os.getenv("YEMOT_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

PROCESSED_FILES_LOG = "processed_files.json"

# היסטוריית ההודעות שכבר עלו לשלוחה 1 (לזיהוי הודעות כפולות)
HISTORY_FILE = "approved_history.json"
HISTORY_MAX_ITEMS = 40          # כמה הודעות אחרונות לזכור
HISTORY_MAX_AGE_HOURS = 24      # ידיעות שפורסמו ב-24 השעות האחרונות נחשבות "כבר פורסמו"
DUPLICATE_SIMILARITY = 0.85     # דמיון טקסטואלי גבוה = הודעה כפולה

# מצב הבוט בין ריצות (למשל: צינתוק שממתין לשעה 08:00)
STATE_FILE = "bot_state.json"

# שעות צינתוק לפי שעון ישראל: מ-08:00 ועד חצות
ISRAEL_TZ_NAME = "Asia/Jerusalem"
TZINTUK_FIRST_HOUR = 8      # שעת הצינתוק הראשונה בבוקר
TZINTUK_LAST_HOUR = 24      # עד חצות (24 = חצות בלילה, לא כולל)
# הודעה דחופה (למשל "האדמו"ר יוצא לשחרית") נשלחת מיד גם בשעות השקט.
# לביטול: שנה ל-False, ואז גם הודעה דחופה תמתין ל-08:00.
URGENT_BYPASSES_QUIET_HOURS = True

# רשת ביטחון: אם אחת המילים האלה מופיעה, ההודעה נפסלת מיד, בלי קשר ל-AI
BAD_KEYWORDS = [
    "חתן", "התארס", "התארוס", "איתארס", "נהיה חתן", "ניהיה חתן", "נעשה חתן",
    "אירוסין", "וורט", "פשנדווק", "פשנדווארט", "פשנדורט",
    "משה לייב פרידמן", "מוישי פרידמן", "משה לייב", "מוישי",
    "חדשות גור גרוע", "ביקורת על הקו", "נגד המערכת", "נגד הקו",
]

MIN_TEXT_LENGTH = 15

AI_SYSTEM_PROMPT = """אתה עורך ומסנן הודעות קוליות עבור קו חדשות של חסידות גור.
קיבלת תמלול אוטומטי (Whisper) של הודעה קולית. התמלול עלול להכיל טעויות שמיעה.

עליך לבצע כמה משימות:

1. החלטה: לאן ההודעה הולכת.
   ברירת המחדל היא אישור (approved=true): כל הודעה שיש לה קשר כלשהו לחסידות גור,
   גם קשר עקיף או חלקי (אדמו"רים, בתי מדרש, אנשי שלומנו, אירועי ציבור, שיעורים,
   תפילות ומניינים, עדכוני קהילה, ענייני חסידים וכו'), מאושרת לשידור.

   רק במקרים הבאים ההודעה לא מאושרת (approved=false) ועוברת לבדיקה ידנית של מנהל הקו:
   - תוכן נגד המערכת או הקו, נגד מפעיל הקו, או ביקורת עליהם
   - כל הודעה שקשורה לחתנים, אירוסין, ווארט, פשנדווארט ודומיהם, גם אם הקשר עקיף
     או היסטורי (למשל הודעה על מי שהיה חתן, התארס או עומד להתחתן), וגם אם
     ההודעה קשורה לחסידות גור
   - תוכן נגד חסידות גור, נגד האדמו"ר או נגד אנשי שלומנו, או תוכן מזלזל ופוגעני
   - הודעה שאין לה שום קשר לחסידות גור, פרסומת, או הודעה ריקה / חסרת משמעות
   - תוכן שאסור או לא חוקי לפרסם, למשל: לשון הרע או הוצאת דיבה על אדם מסוים,
     הסתה לאלימות או לשנאה, איומים, פגיעה בפרטיות (פרסום כתובת, פרטים מזהים
     או מידע רפואי אישי על אדם בלי הסכמתו), פרסום פרטים מזהים של קטינים או של
     נפגעי עבירה, האשמה פלילית של אדם בשמו, עידוד לעבירה, הונאה או הטעיה,
     מידע שפרסומו אסור על פי חוק או צו, והפרת זכויות יוצרים
   הנושאים שלעיל פוסלים גם הודעה שנוגעת בהם באופן חלקי או עקיף, בלי קשר לשאלה
   אם יש בה גם קשר לגור. אם יש ספק אמיתי האם ההודעה פוגענית, נגד המערכת,
   נוגעת בנושא החתנים או אסורה בפרסום: approved=false.
   אבל אל תפסול רק בגלל שהקשר לגור חלש או עקיף, קשר כזה מספיק לאישור.

2. דחיפות (urgent): סמן urgent=true רק להודעה מאושרת שהיא חריגה או דרמטית במיוחד,
   או שיש בה צורך מיידי, כך שהמנויים צריכים לדעת עליה עכשיו.
   דוגמאות: האדמו"ר שליט"א יוצא כעת לתפילת שחרית או לתפילה אחרת, אירוע שמתחיל
   ממש עכשיו או בקרוב מאוד, שינוי דחוף בזמן או במקום של תפילה או אירוע ציבורי,
   הלוויה שמתקיימת מיד, חירום או סכנה, בשורה חריגה שנוגעת לכלל הציבור.
   הודעה רגילה (שיעור, מודעה, עדכון שוטף, אירוע עתידי) היא urgent=false.
   אם יש ספק, urgent=false.

3. ניסוח: הפוך את התמלול לטקסט מדויק וברור שמתאים להקראה בקו.
   - תקן מילים שהתמלול טעה בהן לפי ההקשר (שמות, מונחי חסידות ותפילות,
     מילים שנשמעות דומה). חשוב על מה המדבר התכוון, ובחר את המילה שמתאימה להקשר.
   - אפשר לתקן דקדוק, להשלים מילת קישור חסרה ולנקות גמגומים, חזרות ומילות מילוי,
     כדי שהמשפט יהיה תקין וזורם.
   - אסור להוסיף מידע שלא נאמר, להשמיט עובדות, לשנות שמות, מספרים, שעות
     ותאריכים, או לשנות את המשמעות.
   - אם מילה לא ברורה ואי אפשר להבין מההקשר מה הייתה, אל תנחש:
     השאר אותה כפי שהיא ורשום אותה ב-unclear_words.

4. כפילות: תקבל רשימה של הודעות שכבר עלו לשלוחה 1 ב-24 השעות האחרונות,
   עם הזמן שעבר מאז כל אחת. בדוק לפי המשמעות והתוכן, ולא לפי הניסוח:
   גם אם הניסוח, הסדר והמילים שונים לגמרי, ידיעה שכבר פורסמה היא כפולה.
   - duplicate_kind="full": כל ההודעה כבר פורסמה ואין בה מידע חדש מהותי.
     במקרה כזה approved=false.
   - duplicate_kind="partial": ההודעה כוללת ידיעה שכבר פורסמה וגם תוספת או מידע חדש
     (פרט חדש, שעה או מקום ששונו, עדכון, ביטול או המשך). במקרה כזה approved=true,
     וב-followup_text כתוב רק את החדש, בניסוח שמתחיל במילים "בהמשך לעדכון הקודם על",
     אחריהן תיאור קצר של מה שפורסם קודם, ואז התוספת החדשה. אל תחזור על פרטים
     שכבר פורסמו מעבר לתיאור הקצר. ההודעה המקורית המלאה תועבר לבדיקה ידנית,
     ורק followup_text יעלה לשלוחה 1.
   - duplicate_kind="none": אין חפיפה עם מה שפורסם, או שאין הודעות קודמות.
   - אם ההודעה פסולה מסיבה אחרת (חתנים, נגד המערכת וכו'), approved=false
     ו-duplicate_kind="none".

5. הסבר למנהל (explanation): בכל מקרה ש-approved=false, וגם כש-duplicate_kind="partial",
   כתוב הסבר ברור ומובן שיוקרא למנהל בטלפון לפני ההודעה, כדי שיחליט אם להעלות
   אותה בכל זאת.
   - פרט מה בדיוק גרם לפסילה: איזה כלל, איזה תוכן או משפט בהודעה, ולמה.
   - אם ההודעה כפולה, ציין איזו הודעה קודמת היא משכפלת ומתי פורסמה.
   - אם ההודעה חלקית: הסבר מה כבר פורסם, מה החדש, ושרק התוספת עלתה לשלוחה 1.
   - אם המקרה גבולי, ציין זאת, כדי שהמנהל ידע שאפשר לשקול להעלות.
   - כתוב באריכות ובפירוט כשצריך, בעברית פשוטה וזורמת ובלי סימנים מיוחדים,
     כי הטקסט מוקרא בטלפון.

6. הבנת לשון הקודש כפי שחסידי גור מדברים אותה: הדוברים הם חסידי גור שמדברים
   עברית ולשון קודש בהגייה אשכנזית-חסידית, בשילוב ביטויי יידיש ותארים קבועים.
   התמלול האוטומטי נוטה לטעות בהם, ולכן:
   - הגייה אשכנזית עלולה להתמלל בצורה מעוותת (למשל "שבת" שנשמעת "שאבעס",
     "תורה" שנשמעת "תוירה"). זהה את המילה העברית המקורית לפי ההקשר ותקן אותה.
   - מונחים וביטויים רגילים בקהילה: שבת קודש, מוצאי שבת, ערב שבת, שלש סעודות,
     מלווה מלכה, טיש, שטיבל, בית המדרש, קבלת קהל, יחידות, שיעור, מניין,
     שחרית, מנחה, מעריב, סליחות, ימים נוראים, שמחת תורה, הקפות, עליה לרגל,
     תיקון חצות, דאווענען, לערנען, גבאי, מקווה, כולל.
   - תארים וראשי תיבות: שליט"א, זצ"ל, זי"ע, הרה"ק, הגה"צ, ר', הרב, אדמו"ר.
   - שמות רבותינו, כפי שנהוג לקרוא להם: החידושי הרי"ם, השפת אמת, האמרי אמת,
     הבית ישראל, הלב שמחה, הפני מנחם, והאדמו"ר שליט"א. אלה שמות, אל תשנה אותם.
   - מילים ביידיש שהדובר משלב: השאר אותן, אך כתוב אותן באיות נכון באותיות
     עבריות, ואל תתרגם אותן ואל תחליף את סגנון הדיבור בעברית מודרנית.
   - אל תהפוך את הטקסט לעברית ספרותית או עכשווית: שמור על לשון הדוברים.

החזר אך ורק JSON במבנה הבא:
{
  "approved": true או false,
  "urgent": true או false,
  "urgent_reason": "אם דחופה: למה, אחרת ריק",
  "duplicate_kind": "none" או "partial" או "full",
  "duplicate_reason": "אם יש כפילות: איזו הודעה קודמת, אחרת ריק",
  "followup_text": "רק אם duplicate_kind הוא partial: ההודעה לשלוחה 1, אחרת ריק",
  "reason": "סיבה קצרה בעברית",
  "explanation": "הסבר מפורט למנהל. חובה כש-approved=false או duplicate_kind=partial",
  "corrected_text": "הטקסט אחרי תיקון וליטוש",
  "corrections": ["מילה מקורית -> מילה מתוקנת"],
  "unclear_words": ["מילים שלא היה ברור מה הן"]
}"""


def load_history():
    """טוען את היסטוריית ההודעות שעלו לשלוחה 1, ומנקה ישנות."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=HISTORY_MAX_AGE_HOURS)
    fresh = []
    for item in data:
        try:
            when = datetime.fromisoformat(item["time"])
            if when >= cutoff and item.get("text"):
                fresh.append(item)
        except Exception:
            continue
    return fresh[-HISTORY_MAX_ITEMS:]


def save_history(history):
    history = history[-HISTORY_MAX_ITEMS:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def add_to_history(history, text):
    history.append({"time": datetime.now(timezone.utc).isoformat(), "text": text})
    del history[:-HISTORY_MAX_ITEMS]
    save_history(history)


def find_similar_in_history(text, history):
    """זיהוי כפילות לפי דמיון טקסטואלי גבוה. מחזיר את ההודעה הדומה או None."""
    for item in history:
        ratio = difflib.SequenceMatcher(None, text.strip(), item["text"].strip()).ratio()
        if ratio >= DUPLICATE_SIMILARITY:
            return item
    return None


def get_yemot_token():
    url = "https://call2all.co.il/ym/api/Login"
    params = {"username": YEMOT_USERNAME, "password": YEMOT_PASSWORD}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("responseStatus") == "OK":
            return data.get("token")
        else:
            print(f"שגיאה בהתחברות לימות המשיח: {data}")
    except Exception as e:
        print(f"שגיאת תקשורת בהתחברות: {e}")
    return None


def load_processed_files():
    if os.path.exists(PROCESSED_FILES_LOG):
        try:
            with open(PROCESSED_FILES_LOG, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def commit_and_push(paths, message):
    """מבצע commit ו-push של הקבצים לריפוזיטורי (כדי שהמצב יישמר בין ריצות)."""
    try:
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        for path in paths:
            if os.path.exists(path):
                subprocess.run(["git", "add", path], check=False)
        subprocess.run(["git", "commit", "-m", message], check=False)

        github_token = os.getenv("GITHUB_TOKEN")
        repo = os.getenv("GITHUB_REPOSITORY")
        if github_token and repo:
            remote_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            result = subprocess.run(["git", "push", remote_url, "HEAD:main"], capture_output=True, text=True)
            if result.returncode == 0:
                print("הקבצים גובו ב-GitHub בהצלחה!")
            else:
                print(f"שגיאה בביצוע push: {result.stderr}")
        else:
            subprocess.run(["git", "push"], check=False)
    except Exception as e:
        print(f"שגיאה בגיבוי ב-git: {e}")


def save_and_commit_processed_file(file_name):
    processed = load_processed_files()
    if file_name not in processed:
        processed.append(file_name)
        with open(PROCESSED_FILES_LOG, "w", encoding="utf-8") as f:
            json.dump(processed, f, ensure_ascii=False, indent=4)
        print(f"הקובץ {file_name} נוסף ללוג המעובדים ונשמר.")
        commit_and_push(
            [PROCESSED_FILES_LOG, HISTORY_FILE, STATE_FILE],
            f"Automated update processed files: {file_name}",
        )


def get_ivr2_files(token, folder_path):
    url = "https://call2all.co.il/ym/api/GetIVR2Dir"
    clean_path = str(folder_path).strip("/")
    params = {"token": token, "path": clean_path}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"שגיאה בתקשורת מול ימות המשיח בשלוחה {folder_path}: {e}")
        return None


def download_file_from_yemot(token, file_name, dest_path="temp_audio.wav"):
    url = "https://call2all.co.il/ym/api/DownloadFile"
    paths_to_try = [
        f"ivr2:2/{file_name}",
        f"ivr2:2/{file_name}.wav" if "." not in file_name else None,
        f"2/{file_name}",
        f"2/{file_name}.wav" if "." not in file_name else None,
    ]
    for path in paths_to_try:
        if not path:
            continue
        params = {"token": token, "path": path}
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200 and len(response.content) > 100:
                with open(dest_path, "wb") as f:
                    f.write(response.content)
                return dest_path
        except Exception:
            pass
    return None


def get_next_available_filename(token, target_folder, extension="tts"):
    data = get_ivr2_files(token, target_folder)
    max_num = 0
    if data and data.get("responseStatus") == "OK":
        files = data.get("files", []) or data.get("data", [])
        for f in files:
            name = f.get("name") if isinstance(f, dict) else str(f)
            base_name = name.split(".")[0]
            if base_name.isdigit():
                num = int(base_name)
                if num > max_num:
                    max_num = num
    return f"{max_num + 1:03d}.{extension}"


def upload_tts_to_yemot(token, text_content, target_folder):
    url = "https://call2all.co.il/ym/api/UploadFile"
    clean_folder = str(target_folder).strip("/")
    target_file_name = get_next_available_filename(token, clean_folder, extension="tts")
    temp_tts_file = f"temp_{target_file_name}"

    with open(temp_tts_file, "w", encoding="utf-8") as f:
        f.write(text_content)

    paths_to_try = [f"ivr2:{clean_folder}/{target_file_name}", f"{clean_folder}/{target_file_name}"]
    success = False
    for upload_path in paths_to_try:
        params = {"token": token, "path": upload_path}
        try:
            with open(temp_tts_file, "rb") as f:
                response = requests.post(url, params=params, files={"file": (target_file_name, f)})
                if response.json().get("responseStatus") == "OK":
                    success = True
                    break
        except Exception:
            pass
    if os.path.exists(temp_tts_file):
        os.remove(temp_tts_file)
    return success


# רמז לוויספר: אוצר מילים של חסידי גור, כדי לשפר זיהוי מילים בהגייה חסידית
WHISPER_PROMPT = (
    "הודעה בעברית בהגייה חסידית של חסידי גור. מונחים: שבת קודש, אדמו\"ר שליט\"א, "
    "בית המדרש, שטיבל, טיש, מלווה מלכה, שלש סעודות, קבלת קהל, יחידות, שחרית, מנחה, "
    "מעריב, דאווענען, לערנען, החידושי הרי\"ם, השפת אמת, האמרי אמת, הבית ישראל, "
    "הלב שמחה, הפני מנחם, זצ\"ל, זי\"ע."
)


def transcribe_audio_with_groq(audio_path):
    """מחזיר טקסט (יכול להיות מחרוזת ריקה אם אין דיבור), או None אם התמלול נכשל."""
    if not GROQ_API_KEY:
        print("חסר GROQ_API_KEY")
        return None
    client = Groq(api_key=GROQ_API_KEY)
    try:
        with open(audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(audio_path, file.read()),
                model="whisper-large-v3",
                language="he",
                prompt=WHISPER_PROMPT,
                temperature=0,
            )
        text = transcription.text or ""
        # הגנה: בהקלטה שקטה וויספר לפעמים מחזיר את הרמז עצמו
        if text.strip() and text.strip() in WHISPER_PROMPT:
            return ""
        return text
    except Exception as e:
        print(f"שגיאה בתמלול: {e}")
        return None


def find_bad_keyword(text):
    for bad in BAD_KEYWORDS:
        if bad in text:
            return bad
    return None


def build_user_message(text, history):
    if history:
        now = datetime.now(timezone.utc)
        lines = []
        for i, item in enumerate(history, 1):
            try:
                hours = (now - datetime.fromisoformat(item["time"])).total_seconds() / 3600
                age = "לפני פחות משעה" if hours < 1 else f"לפני כ-{round(hours)} שעות"
            except Exception:
                age = "לפני זמן לא ידוע"
            lines.append(f"{i}. ({age}) {item['text'][:400]}")
        previous = "\n".join(lines)
    else:
        previous = "(אין הודעות קודמות)"
    return f"הודעות שכבר עלו לשלוחה 1 ב-24 השעות האחרונות:\n{previous}\n\nהתמלול החדש:\n{text}"


# סדר העדפה למודלים (הבוט בודק מול Groq מה באמת זמין בחשבון שלך ובוחר אוטומטית)
PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]
NON_CHAT_MARKERS = ("whisper", "guard", "tts", "orpheus", "playai", "embed", "compound", "safeguard")
MAX_MODEL_ATTEMPTS = 6

_chosen_model = None


def list_available_models(client):
    """שואל את Groq אילו מודלים זמינים בחשבון, ומסנן מודלי שמע/אבטחה."""
    try:
        ids = [m.id for m in client.models.list().data]
    except Exception as e:
        print(f"לא הצלחתי לקבל רשימת מודלים מ-Groq: {e}")
        return []
    return [i for i in ids if not any(mark in i.lower() for mark in NON_CHAT_MARKERS)]


def candidate_models(client):
    available = list_available_models(client)
    print(f"מודלים זמינים בחשבון: {available}")
    candidates = []
    env_model = os.getenv("GROQ_MODEL")
    if env_model:
        candidates.append(env_model)
    if available:
        candidates += [m for m in PREFERRED_MODELS if m in available]
        candidates += available
    else:
        candidates += PREFERRED_MODELS
    seen = []
    for m in candidates:
        if m not in seen:
            seen.append(m)
    return seen[:MAX_MODEL_ATTEMPTS]


def parse_json_response(content):
    """מחלץ JSON מתשובת המודל, גם אם עטף אותה בטקסט, בגדרות קוד או בתגיות חשיבה."""
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    first, last = content.find("{"), content.rfind("}")
    if first == -1 or last == -1:
        raise ValueError("לא נמצא JSON בתשובה")
    return json.loads(content[first:last + 1])


def call_llm(client, model, messages):
    kwargs = {"model": model, "temperature": 0, "messages": messages}
    try:
        return client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        if "response_format" in msg or "json" in msg:
            # חלק מהמודלים לא תומכים במצב JSON, מנסים בלעדיו
            return client.chat.completions.create(**kwargs)
        raise


def try_model(client, model, messages):
    try:
        completion = call_llm(client, model, messages)
        result = parse_json_response(completion.choices[0].message.content)
        if not isinstance(result, dict) or not isinstance(result.get("approved"), bool):
            print(f"המודל {model} החזיר מבנה לא תקין: {result}")
            return None
        return result
    except Exception as e:
        print(f"המודל {model} נכשל: {e}")
        return None


def ai_review_text(text, history):
    """שולח את התמלול למודל שפה. מחזיר dict, או None אם כל הניסיונות נכשלו."""
    global _chosen_model
    client = Groq(api_key=GROQ_API_KEY)
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(text, history)},
    ]

    # קודם המודל שכבר עבד בהודעה קודמת
    if _chosen_model:
        result = try_model(client, _chosen_model, messages)
        if result:
            return result
        _chosen_model = None

    for model in candidate_models(client):
        result = try_model(client, model, messages)
        if result:
            _chosen_model = model
            print(f"מודל AI בשימוש: {model}")
            return result

    print("אף מודל לא החזיר תשובה תקינה")
    return None


def is_correction_safe(original, corrected):
    """הגנה מפני 'הזיות': התיקון לא יכול לשנות את אורך הטקסט בצורה חריגה."""
    if not corrected or not corrected.strip():
        return False
    ratio = len(corrected.strip()) / max(len(original.strip()), 1)
    return 0.5 <= ratio <= 1.5


def reject_result(text, explanation):
    """הודעה שעוברת לשלוחה 0/1 (ניהול), עם הסבר שיוקרא לפניה."""
    return {"publish_text": None, "review_text": text, "explanation": explanation, "urgent": False}


def followup_is_valid(followup, final_text, history):
    """בדיקת תקינות להודעת ההמשך ("בהמשך לעדכון הקודם...") שה-AI ניסח."""
    if not isinstance(followup, str):
        return False
    f = followup.strip()
    if len(f) < 15:
        return False
    if len(f) > max(len(final_text) * 1.5, 200):
        return False
    if find_bad_keyword(f):
        return False
    if find_similar_in_history(f, history):
        return False
    return True


def analyze_and_process_text(text, history=None):
    """
    מחזיר dict:
      publish_text : טקסט שיעלה לשלוחה 1 (או None)
      review_text  : טקסט שיעלה לשלוחה 0/1 לבדיקה ידנית (או None)
      explanation  : הסבר למנהל שיועלה לפני ההודעה ב-0/1 (או None)
      urgent       : האם ההודעה שבשלוחה 1 דחופה (לצינתוק מיידי)
    כל מקרה של ספק או כשל מוביל לשלוחה 0/1.
    """
    history = history or []

    if not text or len(text.strip()) < MIN_TEXT_LENGTH:
        print("הודעה קצרה מדי או לא ברורה - מועברת לשלוחה 0/1")
        return reject_result(text, "ההודעה קצרה מדי או לא ברורה, ולכן לא ניתן היה לבדוק אותה. כדאי להאזין לה ולהחליט אם להעלות.")

    # שלב 1: רשת ביטחון של מילות פסילה (על התמלול הגולמי)
    bad = find_bad_keyword(text)
    if bad:
        print(f"זוהתה מילת פסילה ('{bad}') - מעביר מיד לשלוחה 0/1")
        return reject_result(text, f"ההודעה נפסלה אוטומטית, כי הופיעה בה המילה או הביטוי {bad}, שנמצאים ברשימת הפסילה של המערכת. אם ההודעה מתאימה בכל זאת, אפשר להעלות אותה ידנית.")

    # שלב 1.5: בדיקת כפילות מהירה לפי דמיון טקסטואלי
    similar = find_similar_in_history(text, history)
    if similar:
        print(f"הודעה כמעט זהה להודעה שכבר עלתה לשלוחה 1 ({similar['text'][:60]}...) - מועבר לשלוחה 0/1")
        return reject_result(text, f"ההודעה כמעט זהה להודעה שכבר פורסמה בשלוחה אחת ב-24 השעות האחרונות. ההודעה שפורסמה: {similar['text'][:200]}")

    # שלב 2: סינון, תיקון, כפילות ודחיפות על ידי AI
    review = ai_review_text(text, history)
    if review is None:
        print("ה-AI לא החזיר תשובה תקינה - מועבר לשלוחה 0/1 לבדיקה ידנית")
        return reject_result(text, "בדיקת הבינה המלאכותית לא הצליחה מסיבה טכנית, ולכן ההודעה הועברה לבדיקה ידנית. אפשר להאזין לה ולהחליט אם להעלות.")

    print(f"החלטת AI: approved={review['approved']} | דחוף={review.get('urgent')} | כפילות={review.get('duplicate_kind')} | סיבה: {review.get('reason', '')}")

    final_text = text
    corrected = review.get("corrected_text")
    if isinstance(corrected, str) and is_correction_safe(text, corrected):
        final_text = corrected.strip()
        if review.get("corrections"):
            print(f"תיקונים שבוצעו: {review['corrections']}")
    else:
        print("תיקון ה-AI נדחה (ריק או חריג באורכו), נשמר הטקסט המקורי")

    if review.get("unclear_words"):
        print(f"מילים לא ברורות שלא תוקנו: {review['unclear_words']}")

    approved = review["approved"]
    kind = review.get("duplicate_kind")
    if kind not in ("none", "partial", "full"):
        kind = "full" if review.get("is_duplicate") is True else "none"
    urgent = review.get("urgent") is True
    explanation = (review.get("explanation") or "").strip()
    reason = (review.get("reason") or "").strip()

    # כפילות מלאה
    if approved and kind == "full":
        print(f"ה-AI זיהה הודעה כפולה: {review.get('duplicate_reason', '')} - מועבר לשלוחה 0/1")
        approved = False
        explanation = explanation or f"ההודעה כבר פורסמה ב-24 השעות האחרונות ואין בה מידע חדש. {review.get('duplicate_reason', '')}"

    # כפילות חלקית: ההמשך עולה ל-1, המקור עובר ל-0/1
    if approved and kind == "partial":
        followup = review.get("followup_text")
        if followup_is_valid(followup, final_text, history):
            print("כפילות חלקית: ההמשך יעלה לשלוחה 1, ההודעה המקורית תעבור לשלוחה 0/1")
            explanation = explanation or f"ההודעה כוללת ידיעה שכבר פורסמה וגם מידע חדש. לשלוחה אחת עלתה רק התוספת, בניסוח בהמשך לעדכון הקודם. {review.get('duplicate_reason', '')}"
            return {"publish_text": followup.strip(), "review_text": final_text, "explanation": explanation, "urgent": urgent}
        print("ניסוח ההמשך של ה-AI לא תקין - ההודעה כולה מועברת לשלוחה 0/1")
        approved = False
        explanation = explanation or "ההודעה כוללת ידיעה שכבר פורסמה וגם מידע חדש, אך לא הצלחתי לנסח הודעת המשך בצורה בטוחה. כדאי להאזין ולנסח ידנית את התוספת."

    # בדיקות חוזרות על הטקסט המתוקן (מילות פסילה וכפילות)
    if approved:
        bad_after = find_bad_keyword(final_text)
        if bad_after:
            print(f"מילת פסילה ('{bad_after}') בטקסט המתוקן - מועבר לשלוחה 0/1")
            approved = False
            explanation = f"אחרי התיקון הופיעה בהודעה המילה {bad_after}, שנמצאת ברשימת הפסילה, ולכן היא הועברה לבדיקה ידנית."
        elif find_similar_in_history(final_text, history):
            print("הטקסט המתוקן כמעט זהה להודעה קודמת בשלוחה 1 - מועבר לשלוחה 0/1")
            approved = False
            explanation = "אחרי התיקון, ההודעה כמעט זהה להודעה שכבר פורסמה בשלוחה אחת ב-24 השעות האחרונות."

    if approved:
        return {"publish_text": final_text, "review_text": None, "explanation": None, "urgent": urgent}

    explanation = explanation or reason or "הבינה המלאכותית פסלה את ההודעה, ולא נמסרה סיבה מפורטת."
    return reject_result(final_text, explanation)


# רשימת הצינתוקים (מספר 2). אפשר לעקוף דרך משתנה סביבה TZINTUK_LIST
DEFAULT_TZINTUK_LIST = "2"
# זמן צלצול בשניות (כמו בהגדרה המומלצת במדריך הצינתוקים של ימות)
TZINTUK_TIMEOUT = "16"


def trigger_tzintuk(token):
    """שולח צינתוק (לא קמפיין קולי) לרשימת הצינתוקים דרך RunTzintuk. מחזיר True אם הצליח."""
    list_id = (os.getenv("TZINTUK_LIST") or "").strip() or DEFAULT_TZINTUK_LIST
    phones = list_id if list_id.startswith(("tzl:", "tpl:")) else f"tzl:{list_id}"
    params = {"token": token, "phones": phones, "TzintukTimeOut": TZINTUK_TIMEOUT}
    caller_id = (os.getenv("TZINTUK_CALLER_ID") or "").strip()
    if caller_id:
        params["callerId"] = caller_id

    print(f"שולח צינתוק לרשימה: {phones}")
    try:
        response = requests.get("https://call2all.co.il/ym/api/RunTzintuk", params=params)
        print(f"תשובת ימות לצינתוק: {response.text[:300]}")
        data = response.json()
        if data.get("responseStatus") == "OK":
            print("הצינתוק הופעל בהצלחה.")
            return True
        print("הצינתוק נכשל, ראה את התשובה של ימות למעלה.")
    except Exception as e:
        print(f"שגיאה בהפעלת הצינתוק: {e}")
    return False


def israel_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(ISRAEL_TZ_NAME))
    except Exception as e:
        print(f"אזהרה: לא ניתן לטעון אזור זמן ישראל ({e}), משתמש ב-UTC+3 בקירוב")
        return datetime.now(timezone(timedelta(hours=3)))


def tzintuk_allowed_now(now=None):
    """האם מותר לשלוח צינתוק רגיל עכשיו (מ-08:00 עד חצות, שעון ישראל)."""
    now = now or israel_now()
    return TZINTUK_FIRST_HOUR <= now.hour < TZINTUK_LAST_HOUR


def load_state():
    state = {"pending_tzintuk": False}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state["pending_tzintuk"] = bool(data.get("pending_tzintuk", False))
        except Exception:
            pass
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def decide_tzintuk(published_count, urgent_count, pending, allowed):
    """
    מחליט אם לשלוח צינתוק עכשיו. מחזיר (send_now, new_pending).
    - הודעה דחופה: צינתוק מיידי בכל שעה (אם URGENT_BYPASSES_QUIET_HOURS).
    - הודעה רגילה: צינתוק עד חצות; אחרי חצות הוא ממתין ל-08:00.
    """
    if urgent_count > 0 and URGENT_BYPASSES_QUIET_HOURS:
        return True, False
    if allowed and (published_count > 0 or pending):
        return True, False
    if published_count > 0:
        return False, True
    return False, pending


def handle_tzintuk(token, published_count, urgent_count):
    state = load_state()
    old_pending = state["pending_tzintuk"]
    now = israel_now()
    allowed = tzintuk_allowed_now(now)

    send, new_pending = decide_tzintuk(published_count, urgent_count, old_pending, allowed)
    print(f"שעה בישראל: {now.strftime('%H:%M')} | צינתוק מותר: {allowed} | הודעות חדשות בשלוחה 1: {published_count} | דחופות: {urgent_count} | צינתוק ממתין: {old_pending}")

    if send:
        if urgent_count > 0 and not allowed:
            print("הודעה דחופה - שולח צינתוק מיידי גם בשעות השקט.")
        elif old_pending and published_count == 0:
            print("שולח את הצינתוק שהמתין לבוקר.")
        else:
            print("הודעה אושרה לשלוחה 1, מפעיל צינתוק...")
        if not trigger_tzintuk(token):
            new_pending = True  # ננסה שוב בריצה הבאה
    elif new_pending and not old_pending:
        print("מחוץ לשעות הצינתוק - הצינתוק יישלח בשעה 08:00.")

    if new_pending != old_pending:
        state["pending_tzintuk"] = new_pending
        save_state(state)
        commit_and_push([STATE_FILE], "Automated update tzintuk state")


def process_file(token, file_name, history):
    """
    מטפל בהודעה אחת.
    מחזיר (done, published, urgent):
      done=True       -> הטיפול הסתיים ואפשר לסמן את הקובץ כמעובד
      done=False      -> משהו נכשל (הורדה/תמלול/העלאה), הקובץ ינוסה שוב בריצה הבאה
      published=True  -> הועלתה הודעה לשלוחה 1
      urgent=True     -> ההודעה שעלתה ל-1 דחופה
    """
    local_audio = download_file_from_yemot(token, file_name)
    if not local_audio:
        print(f"הורדת {file_name} נכשלה - לא מסומן כמעובד, ינוסה שוב.")
        return False, False, False

    try:
        raw_text = transcribe_audio_with_groq(local_audio)
    finally:
        if os.path.exists(local_audio):
            os.remove(local_audio)

    if raw_text is None:
        print(f"תמלול {file_name} נכשל - לא מסומן כמעובד, ינוסה שוב.")
        return False, False, False

    if not raw_text.strip():
        print(f"{file_name}: לא זוהה דיבור בהקלטה, מדלג.")
        return True, False, False

    print(f"תמלול גולמי: {raw_text}")

    analysis = analyze_and_process_text(raw_text, history)
    publish_text = analysis["publish_text"]
    review_text = analysis["review_text"]
    explanation = analysis["explanation"]

    # קודם שלוחה 0/1: ההודעה (מספר N), ואז ההסבר (מספר N+1). המספר הגבוה יותר
    # מושמע ראשון, כך שהמנהל שומע קודם את ההסבר ואחריו את ההודעה.
    if review_text:
        print("יעד העלאה: שלוחה 0/1 (בדיקה ידנית)")
        if not upload_tts_to_yemot(token, review_text, "0/1"):
            print(f"העלאת {file_name} ל-0/1 נכשלה - לא מסומן כמעובד, ינוסה שוב.")
            return False, False, False
        if explanation:
            spoken = f"הסבר למנהל על ההודעה הבאה. {explanation}"
            if upload_tts_to_yemot(token, spoken, "0/1"):
                print("קובץ ההסבר הועלה לפני ההודעה ב-0/1.")
            else:
                print("אזהרה: העלאת קובץ ההסבר נכשלה (ההודעה עצמה כן הועלתה).")

    if publish_text:
        print("יעד העלאה: שלוחה 1")
        if not upload_tts_to_yemot(token, publish_text, "1"):
            print(f"העלאת {file_name} לשלוחה 1 נכשלה - לא מסומן כמעובד, ינוסה שוב.")
            return False, False, False
        add_to_history(history, publish_text)
        return True, True, bool(analysis["urgent"])

    return True, False, False


def main():
    token = get_yemot_token()
    if not token:
        return

    processed_files = load_processed_files()
    history = load_history()
    print(f"היסטוריית שלוחה 1: {len(history)} הודעות אחרונות")
    print(f"קבצים שכבר עובדו בעבר ({len(processed_files)} קבצים)")

    published_count = 0
    urgent_count = 0

    data = get_ivr2_files(token, "2")
    if data and data.get("responseStatus") == "OK":
        files = data.get("files", []) or data.get("data", [])

        for file in files:
            file_name = file.get("name") if isinstance(file, dict) else str(file)
            if not file_name or file_name.endswith(".ini") or file_name.endswith(".ymgr"):
                continue
            if not (file_name.endswith(".wav") or file_name.endswith(".mp3") or file_name.isdigit() or file_name.startswith("wav.")):
                continue

            if file_name in processed_files:
                print(f"הקובץ {file_name} כבר עובד בעבר, מדלג עליו.")
                continue

            print(f"\n--- מטפל בהודעה קולית חדשה: {file_name} ---")

            done, published, urgent = process_file(token, file_name, history)
            if done:
                if published:
                    published_count += 1
                    if urgent:
                        urgent_count += 1
                save_and_commit_processed_file(file_name)

    # הצינתוק נבדק תמיד, גם כשאין הודעות חדשות (כדי לשלוח צינתוק שחיכה ל-08:00)
    handle_tzintuk(token, published_count, urgent_count)


if __name__ == "__main__":
    main()
