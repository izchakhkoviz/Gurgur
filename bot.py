import os
import requests
import json
from groq import Groq

# משתני סביבה מתוך GitHub Secrets
YEMOT_TOKEN = os.environ.get("YEMOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
PHONE_SYSTEM_DID = os.environ.get("PHONE_SYSTEM_DID")

client = Groq(api_key=GROQ_API_KEY)

def clean_path(path):
    """מנקה סלאשים מובילים ומיותרים כדי למנוע שגיאות WS name"""
    if not path:
        return ""
    path = str(path).strip()
    while path.startswith('/'):
        path = path[1:]
    return path

def get_next_folder_index(folder_path):
    """מציאת המספר הסידורי הפנוי הבא בשלוחת היעד"""
    try:
        folder = clean_path(folder_path)
        url = f"https://www.call2all.co.il/ym/api/GetIVR2?token={YEMOT_TOKEN}&folder={folder}"
        response = requests.get(url)
        data = response.json()
        if data.get("responseStatus") == "OK":
            files = data.get("files", [])
            max_num = 0
            for f in files:
                name = f.get("name", "").split(".")[0]
                if name.isdigit():
                    max_num = max(max_num, int(name))
            return max_num + 1
    except Exception as e:
        print(f"שגיאה בבדיקת אינדקס בשלוחה {folder_path}: {e}")
    return 1

def download_audio_file(file_name, local_filename="temp_audio.wav"):
    """הורדת קובץ השמע משלוחה 2 בימות המשיח"""
    download_url = f"https://www.call2all.co.il/ym/api/DownloadFile?token={YEMOT_TOKEN}&path=2/{file_name}"
    try:
        res = requests.get(download_url, stream=True)
        if res.status_code == 200:
            with open(local_filename, 'wb') as f:
                for chunk in res.iter_content(chunk_size=8192):
                    f.write(chunk)
            return local_filename
        else:
            print(f"שגיאה בהורדת קובץ, סטטוס: {res.status_code}, תוכן: {res.text}")
    except Exception as e:
        print(f"שגיאה בהורדת הקובץ {file_name}: {e}")
    return None

def transcribe_audio_with_groq(file_path):
    """תמלול קובץ השמע באמצעות Whisper של Groq"""
    try:
        with open(file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(file_path, file.read()),
                model="whisper-large-v3",
                language="he",
                response_format="json",
            )
            return transcription.text
    except Exception as e:
        print(f"שגיאה בתמלול השמע מול Groq: {e}")
        return ""

def analyze_and_classify_with_llm(text_content):
    """סיווג וניתוח התוכן מול LLM"""
    if not text_content or len(text_content.strip()) < 3:
        return "REGULAR"

    prompt = f"""
    נתח את הניסוח והתוכן של הודעת החדשות החסידית הבאה:
    
    1. בדוק האם הטקסט מכיל ביקורת, פגיעה, דיבור נגד ההנהלה/החסידות או תכנים פסולים. אם כן - החזר בדיוק: "BLOCKED".
    2. אם הטקסט תקין, בדוק האם מדובר בהודעה דחופה וחריגה (שינוי פתאומי בלו"ז, אירוע חירום דרמטי וכד'). אם כן - החזר בדיוק: "URGENT".
    3. אם הטקסט תקין וזהו עדכון שוטף ורגיל - החזר בדיוק: "REGULAR".
    
    החזר אך ורק מילה אחת מתוך השלוש ("BLOCKED", "URGENT", "REGULAR"):
    
    תוכן ההודעה:
    {text_content}
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        result = completion.choices[0].message.content.strip().upper()
        if "BLOCKED" in result:
            return "BLOCKED"
        elif "URGENT" in result:
            return "URGENT"
        else:
            return "REGULAR"
    except Exception as e:
        print(f"שגיאה בניתוח LLM: {e}")
        return "REGULAR"

def move_file_in_yemot(source_file_path, target_folder):
    """העברת קובץ בשרתי ימות המשיח"""
    next_index = get_next_folder_index(target_folder)
    file_extension = source_file_path.split('.')[-1]
    target_file_name = f"{next_index:03d}.{file_extension}"
    
    clean_target = clean_path(target_folder)
    clean_source = clean_path(source_file_path)
    
    url = f"https://www.call2all.co.il/ym/api/MoveFile?token={YEMOT_TOKEN}&source={clean_source}&target={clean_target}/{target_file_name}"
    try:
        response = requests.get(url)
        res_data = response.json()
        if res_data.get("responseStatus") == "OK":
            print(f"הקובץ הועבר בהצלחה ל-{clean_target}/{target_file_name}")
            return True
        else:
            print(f"שגיאה בהעברת הקובץ בימות המשיח: {res_data}")
    except Exception as e:
        print(f"שגיאה בביצוע MoveFile: {e}")
    return False

def trigger_tzintuk(extension):
    """הפעלת שלוחת הצינטוקים"""
    try:
        url = f"https://www.call2all.co.il/ym/api/RunExtensions?token={YEMOT_TOKEN}&did={PHONE_SYSTEM_DID}&extension={extension}"
        response = requests.get(url)
        print(f"הפעלת צינטוק בשלוחה {extension}: {response.text}")
    except Exception as e:
        print(f"שגיאה בהפעלת צינטוק לשלוחה {extension}: {e}")

def process_voice_messages():
    print("מתחיל סריקה, תמלול ועיבוד הודעות בשלוחה 2...")
    
    # הדפסת בדיקה לווידוא שהטוקן אינו ריק
    if not YEMOT_TOKEN:
        print("שגיאה קריטית: YEMOT_TOKEN אינו מוגדר ב-GitHub Secrets!")
        return

    # פנייה נקייה לשלוחה 2
    files_url = f"https://www.call2all.co.il/ym/api/GetIVR2?token={YEMOT_TOKEN}&folder=2"
    res = requests.get(files_url)
    data = res.json()
    
    if data.get("responseStatus") != "OK":
        print(f"שגיאה בגישה לשלוחה 2: {data}")
        return

    files = data.get("files", [])
    audio_files = [f for f in files if f.get("type") == "file" and f.get("name", "").lower().endswith(('.wav', '.mp3', '.m4a', '.gsm'))]

    if not audio_files:
        print("אין קבצי שמע חדשים לעיבוד בשלוחה 2.")
        return

    for file_info in audio_files:
        file_name = file_info.get("name")
        source_path = f"2/{file_name}"
        print(f"\n--- מעבד את הקובץ: {file_name} ---")
        
        # 1. הורדה
        local_file = download_audio_file(file_name)
        if not local_file:
            print("נכשלה הורדת קובץ השמע.")
            continue
            
        # 2. תמלול
        print("מבצע תמלול שמע באמצעות Groq Whisper...")
        transcribed_text = transcribe_audio_with_groq(local_file)
        print(f"תוצאת התמלול: {transcribed_text}")

        if os.path.exists(local_file):
            os.remove(local_file)

        # 3. ניתוח וסיווג
        status = analyze_and_classify_with_llm(transcribed_text)
        print(f"תוצאת סיווג התוכן: {status}")

        # 4. העברה וצינטוק
        if status == "BLOCKED":
            print("תוכן חסום! מועבר לשלוחה 0/1 (ללא צינטוק)...")
            move_file_in_yemot(source_path, "0/1")
            
        elif status == "URGENT":
            print("הודעה דחופה! מועברת לשלוחה 1 ומפעילה צינטוק 400...")
            if move_file_in_yemot(source_path, "1"):
                trigger_tzintuk("400")
            
        else:
            print("הודעה רגילה. מועברת לשלוחה 1 ומפעילה צינטוק 200...")
            if move_file_in_yemot(source_path, "1"):
                trigger_tzintuk("200")

    print("\nהסריקה, התמלול והעיבוד הושלמו בהצלחה!")

if __name__ == "__main__":
    process_voice_messages()
