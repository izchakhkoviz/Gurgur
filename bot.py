import os
import json
import requests
from groq import Groq

# טעינת נתוני ההתחברות מתוך GitHub Secrets
YEMOT_USERNAME = os.getenv("PHONE_SYSTEM_DID")
YEMOT_PASSWORD = os.getenv("YEMOT_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

PROCESSED_FILES_LOG = "processed_files.json"

def get_yemot_token():
    url = "https://call2all.co.il/ym/api/Login"
    params = {
        "username": YEMOT_USERNAME,
        "password": YEMOT_PASSWORD
    }
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
                return json.load(f)
        except Exception:
            return []
    return []

def save_processed_file(file_name):
    processed = load_processed_files()
    if file_name not in processed:
        processed.append(file_name)
        with open(PROCESSED_FILES_LOG, "w", encoding="utf-8") as f:
            json.dump(processed, f, ensure_ascii=False, indent=4)

def get_ivr2_files(token, folder_path="2"):
    """קריאת תכולת השלוחה באמצעות GetIVR2Dir שעובד יציב מול השרת"""
    url = "https://call2all.co.il/ym/api/GetIVR2Dir"
    clean_path = str(folder_path).strip("/")
    
    params = {
        "token": token,
        "path": clean_path
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"שגיאה בתקשורת מול ימות המשיח: {e}")
        return None

def download_file_from_yemot(token, file_name, dest_path="temp_audio.wav"):
    url = "https://call2all.co.il/ym/api/DownloadFile"
    params = {
        "token": token,
        "path": f"2/{file_name}"
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            with open(dest_path, "wb") as f:
                f.write(response.content)
            return dest_path
    except Exception as e:
        print(f"שגיאה בהורדת הקובץ {file_name}: {e}")
    return None

def upload_file_to_yemot(token, file_path, target_folder, target_file_name):
    url = "https://call2all.co.il/ym/api/UploadFile"
    clean_folder = str(target_folder).strip("/")
    
    params = {
        "token": token,
        "path": f"{clean_folder}/{target_file_name}"
    }
    try:
        with open(file_path, "rb") as f:
            files = {'file': (target_file_name, f)}
            response = requests.post(url, params=params, files=files)
            res_data = response.json()
            if res_data.get("responseStatus") == "OK":
                print(f"הקובץ הועלה בהצלחה לשלוחה {target_folder}/{target_file_name}")
                return True
            else:
                print(f"שגיאה בהעלאה לשלוחה {target_folder}: {res_data}")
    except Exception as e:
        print(f"שגיאה בהעלאת קובץ לשרת: {e}")
    return False

def transcribe_audio_with_groq(audio_path):
    if not GROQ_API_KEY:
        print("שגיאה: GROQ_API_KEY אינו מוגדר!")
        return None
        
    client = Groq(api_key=GROQ_API_KEY)
    try:
        with open(audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(audio_path, file.read()),
                model="whisper-large-v3",
                language="he"
            )
        return transcription.text
    except Exception as e:
        print(f"שגיאה בתמלול ב-Groq: {e}")
        return None

def analyze_and_process_text(text):
    if not GROQ_API_KEY or not text:
        return {"approved": False, "processed_text": text}

    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
אתה מיועד לסנן ולעבד הודעות קוליות עבור קו "חדשות גור".

תפקידך:
1. תקן שגיאות כתיב והשלם מילים חסרות או לא ברורות לפי הקשר המשפט, כך שהטקסט יהיה רציף וברור.
2. בדוק אם הטקסט עומד בכל הכללים הבאים:
   - קשור לחסידות גור.
   - אינו מכיל שום תוכן או ביקורת נגד קו הטלפון "חדשות גור".
   - אינו מכיל שום תוכן או ביקורת נגד משה לייב פרידמן (מוישי פרידמן).

החזר תשובה במבנה JSON בלבד בצורה הבאה:
{{
  "approved": true/false,
  "processed_text": "הטקסט המתוקן והמושלם"
}}

הטקסט לבדיקה:
{text}
"""
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        result = json.loads(completion.choices[0].message.content)
        return result
    except Exception as e:
        print(f"שגיאה בניתוח ב-Groq: {e}")
        return {"approved": False, "processed_text": text}

def main():
    print("מתחיל התחברות לימות המשיח...")
    token = get_yemot_token()
    
    if not token:
        print("שגיאה קריטית: לא ניתן להנפיק טוקן התחברות!")
        return

    print("התחברות הצליחה! סורק את שלוחה 2 באמצעות GetIVR2Dir...")
    processed_files = load_processed_files()
    data = get_ivr2_files(token, "2")
    
    if not data or data.get("responseStatus") != "OK":
        print(f"שגיאה בגישה לשלוחה 2: {data}")
        return

    files = data.get("files", []) or data.get("data", [])
    print(f"נמצאו {len(files)} פריטים בשלוחה.")
    
    for file in files:
        file_name = file.get("name") if isinstance(file, dict) else str(file)
        
        # סינון: נעבוד אך ורק על קובצי השמע של ההקלטות בשלוחה (wav) ונתעלם מקובצי מערכת
        if not file_name or not (file_name.startswith("wav.") or file_name.endswith(".wav") or file_name.endswith(".mp3")):
            continue
            
        if file_name in processed_files:
            continue
            
        print(f"\nמטפל בהודעה קולית חדשה: {file_name}")
        
        local_audio = download_file_from_yemot(token, file_name)
        if local_audio:
            raw_text = transcribe_audio_with_groq(local_audio)
            if raw_text:
                print(f"תמלול גולמי: {raw_text}")
                
                analysis = analyze_and_process_text(raw_text)
                is_approved = analysis.get("approved", False)
                final_text = analysis.get("processed_text", raw_text)
                
                print(f"טקסט מעובד: {final_text}")
                print(f"אושר לשלוחה 1: {is_approved}")
                
                target_folder = "1" if is_approved else "0/1"
                upload_file_to_yemot(token, local_audio, target_folder, file_name)
            
            if os.path.exists(local_audio):
                os.remove(local_audio)
                
            save_processed_file(file_name)

if __name__ == "__main__":
    main()
