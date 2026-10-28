import base64
import io
import json
import os
import random
import re
import shutil
import smtplib
import sqlite3
import time
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import qrcode
import qrcode.image.svg
from werkzeug.utils import secure_filename
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "my_super_secret_key_for_basketball_event")

# --- โฟลเดอร์รูปภาพแบนเนอร์สไลเดอร์โปรโมชั่น (ดึงไฟล์ภาพอัตโนมัติ) ---
BANNER_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "banners"
)
os.makedirs(BANNER_FOLDER, exist_ok=True)

# --- โฟลเดอร์อัปโหลดรูปโปรไฟล์ผู้ใช้งาน ---
AVATAR_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "uploads", "avatars"
)
os.makedirs(AVATAR_FOLDER, exist_ok=True)

# --- โฟลเดอร์อัปโหลดสลิปหลักฐานการโอนเงินมัดจำ ---
SLIP_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "uploads", "slips"
)
os.makedirs(SLIP_FOLDER, exist_ok=True)

# --- การตั้งค่าระบบชำระเงินมัดจำพร้อมเพย์ 5% (หรือ 1 บาท สำหรับโหมดทดสอบ) ---
PROMPTPAY_NUMBER = "0918303564"

# --- ตำแหน่งไฟล์สำรองฐานข้อมูล SQLite สำหรับกู้คืนอัตโนมัติ ---
BACKUP_SQL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "database_backup.sql"
)
BACKUP_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "database_backup.db"
)


def crc16_ccitt(data: str) -> str:
  """คำนวณ Checksum CRC16-CCITT สำหรับมาตรฐาน PromptPay EMVCo QR Code"""
  crc = 0xFFFF
  for char in data.encode("ascii"):
    crc ^= (char << 8)
    for _ in range(8):
      if crc & 0x8000:
        crc = ((crc << 1) ^ 0x1021) & 0xFFFF
      else:
        crc = (crc << 1) & 0xFFFF
  return f"{crc:04X}"


def generate_promptpay_payload(phone: str, amount: float = None) -> str:
  """สร้างสตริงข้อมูลมาตรฐาน PromptPay EMVCo Payload พร้อมยอดเงินทศนิยม 2 ตำแหน่ง"""
  clean_phone = phone.replace("-", "").strip()
  if clean_phone.startswith("0"):
    clean_phone = "0066" + clean_phone[1:]
  target_tag = f"01{len(clean_phone):02d}{clean_phone}"
  sub_data = f"0016A000000677010111{target_tag}"
  merchant_tag = f"29{len(sub_data):02d}{sub_data}"
  payload = "000201" + ("010212" if amount else "010211") + merchant_tag + "5303764"
  if amount is not None and amount > 0:
    amt_str = f"{amount:.2f}"
    payload += f"54{len(amt_str):02d}{amt_str}"
  payload += "5802TH6304"
  payload += crc16_ccitt(payload)
  return payload


def validate_password_strength(password: str):
  """
  ตรวจสอบความปลอดภัยของรหัสผ่านผู้ใช้งาน:
  - ความยาวอย่างน้อย 8 ตัวอักษร
  - มีตัวอักษรพิมพ์เล็ก (a-z) อย่างน้อย 1 ตัว
  - มีตัวอักษรพิมพ์ใหญ่ (A-Z) อย่างน้อย 1 ตัว
  - มีตัวเลข (0-9) อย่างน้อย 1 ตัว
  - มีอักขระพิเศษ (@#$%^&*!_- ฯลฯ) อย่างน้อย 1 ตัว
  """
  if not password:
    return False, "กรุณากรอกรหัสผ่าน"
  if len(password) < 8:
    return False, "รหัสผ่านต้องมีความยาวอย่างน้อย 8 ตัวอักษร"
  if not re.search(r"[a-z]", password):
    return False, "รหัสผ่านต้องมีตัวอักษรพิมพ์เล็ก (a-z) อย่างน้อย 1 ตัว"
  if not re.search(r"[A-Z]", password):
    return False, "รหัสผ่านต้องมีตัวอักษรพิมพ์ใหญ่ (A-Z) อย่างน้อย 1 ตัว"
  if not re.search(r"[0-9]", password):
    return False, "รหัสผ่านต้องมีตัวเลข (0-9) อย่างน้อย 1 ตัว"
  if not re.search(r"[^a-zA-Z0-9]", password):
    return False, "รหัสผ่านต้องมีอักขระพิเศษ (เช่น !@#$%^&*()_+-=) อย่างน้อย 1 ตัว"
  return True, ""


def generate_promptpay_png_b64(phone: str, amount: float = None) -> str:
  """สร้างภาพ QR Code PNG Base64 Data URI ที่ฝังยอดเงินสำหรับสแกนผ่านแอปธนาคารทุกแห่ง"""
  payload = generate_promptpay_payload(phone, amount)
  qr = qrcode.QRCode(
      version=None,
      error_correction=qrcode.constants.ERROR_CORRECT_M,
      box_size=8,
      border=3,
  )
  qr.add_data(payload)
  qr.make(fit=True)
  img = qr.make_image(fill_color="black", back_color="white")
  buf = io.BytesIO()
  img.save(buf, format="PNG")
  return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def generate_promptpay_svg(phone: str, amount: float = None) -> str:
  """สร้างรูปภาพ SVG QR Code พร้อมเพย์แบบเวกเตอร์ คมชัด สวยงาม ไม่แตก"""
  payload = generate_promptpay_payload(phone, amount)
  factory = qrcode.image.svg.SvgPathImage
  img = qrcode.make(payload, image_factory=factory)
  svg_str = img.to_string().decode("utf-8")
  svg_str = re.sub(r'width="\d+mm" height="\d+mm"', 'width="100%" height="100%"', svg_str)
  if "<path" in svg_str:
    svg_str = svg_str.replace("<path", '<rect width="100%" height="100%" fill="#ffffff"/><path fill="#000000"')
  return svg_str


def get_banner_images():
    """
    ฟังก์ชันดึงรายชื่อไฟล์รูปภาพโปรโมทแบนเนอร์จาก static/banners/ โดยเฉพาะ (ไม่ปนกับ static/images)
    รองรับสกุลไฟล์ .jpg, .jpeg, .png, .webp, .svg, .gif
    """
    allowed_exts = {'.jpg', '.jpeg', '.png', '.webp', '.svg', '.gif'}
    images = []

    # ค้นหาเฉพาะใน static/banners/ เท่านั้น
    if os.path.exists(BANNER_FOLDER):
        for f in os.listdir(BANNER_FOLDER):
            ext = os.path.splitext(f)[1].lower()
            if ext in allowed_exts:
                images.append({'path': 'banners/' + f, 'name': f})

    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s['name'])]

    images.sort(key=natural_sort_key)
    return images

# --- รายชื่อ 77 จังหวัดในประเทศไทย สำหรับผู้จอง / สมัครสมาชิก ---
THAI_PROVINCES = [
    "ขอนแก่น",
    "กรุงเทพมหานคร",
    "กระบี่",
    "กาญจนบุรี",
    "กาฬสินธุ์",
    "กำแพงเพชร",
    "จันทบุรี",
    "ฉะเชิงเทรา",
    "ชลบุรี",
    "ชัยนาท",
    "ชัยภูมิ",
    "ชุมพร",
    "เชียงราย",
    "เชียงใหม่",
    "ตรัง",
    "ตราด",
    "ตาก",
    "นครนายก",
    "นครปฐม",
    "นครพนม",
    "นครราชสีมา",
    "นครศรีธรรมราช",
    "นครสวรรค์",
    "นนทบุรี",
    "นราธิวาส",
    "น่าน",
    "บึงกาฬ",
    "บุรีรัมย์",
    "ปทุมธานี",
    "ประจวบคีรีขันธ์",
    "ปราจีนบุรี",
    "ปัตตานี",
    "พระนครศรีอยุธยา",
    "พังงา",
    "พัทลุง",
    "พิจิตร",
    "พิษณุโลก",
    "เพชรบุรี",
    "เพชรบูรณ์",
    "แพร่",
    "พะเยา",
    "ภูเก็ต",
    "มหาสารคาม",
    "มุกดาหาร",
    "แม่ฮ่องสอน",
    "ยโสธร",
    "ยะลา",
    "ร้อยเอ็ด",
    "ระนอง",
    "ระยอง",
    "ราชบุรี",
    "ลพบุรี",
    "ลำปาง",
    "ลำพูน",
    "เลย",
    "ศรีสะเกษ",
    "สกลนคร",
    "สงขลา",
    "สตูล",
    "สมุทรปราการ",
    "สมุทรสงคราม",
    "สมุทรสาคร",
    "สระแก้ว",
    "สระบุรี",
    "สิงห์บุรี",
    "สุโขทัย",
    "สุพรรณบุรี",
    "สุราษฎร์ธานี",
    "สุรินทร์",
    "หนองคาย",
    "หนองบัวลำภู",
    "อ่างทอง",
    "อุดรธานี",
    "อุทัยธานี",
    "อุตรดิตถ์",
    "อุบลราชธานี",
    "อำนาจเจริญ",
]

# --- รายชื่อสนามแข่งขันบาสเกตบอลภายในจังหวัดขอนแก่น และยิมมหาวิทยาลัยขอนแก่น ---
KHON_KAEN_VENUES = [
    {
        "id": "kku_gym1",
        "name": "ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น (KKU Sports Complex - โรงยิมเนเซียม 1 สนามหลักพื้นปาร์เก้)",
        "zone": "มหาวิทยาลัยขอนแก่น",
        "type": "ในร่ม ติดแอร์ / พื้นไม้ปาร์เก้มาตรฐาน FIBA",
        "capacity": "อัฒจันทร์ 1,500 ที่นั่ง",
    },
    {
        "id": "kku_gym2",
        "name": "ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น (KKU Sports Complex - โรงยิมเนเซียม 2 สนามฝึกซ้อม)",
        "zone": "มหาวิทยาลัยขอนแก่น",
        "type": "ในร่ม / พื้นยางสังเคราะห์มาตรฐาน",
        "capacity": "อัฒจันทร์ 500 ที่นั่ง",
    },
    {
        "id": "kku_600",
        "name": "โรงยิมเนเซียม 600 ที่นั่ง มหาวิทยาลัยขอนแก่น (KKU Indoor Gymnasium 600 Seats)",
        "zone": "มหาวิทยาลัยขอนแก่น",
        "type": "ในร่ม / พื้นไม้คอร์ทคู่",
        "capacity": "อัฒจันทร์ 600 ที่นั่ง",
    },
    {
        "id": "kku_romsak",
        "name": "สนามบาสเกตบอลกลางแจ้ง สวนร่มสัก มหาวิทยาลัยขอนแก่น",
        "zone": "มหาวิทยาลัยขอนแก่น",
        "type": "กลางแจ้ง (Outdoor) 4 แป้นมาตรฐาน บรรยากาศร่มรื่น",
        "capacity": "ที่นั่งริมสนามและสวนพักผ่อน",
    },
    {
        "id": "kku_jubilee",
        "name": "ศูนย์ประชุมอเนกประสงค์กาญจนาภิเษก / อาคาร 50 ปี มหาวิทยาลัยขอนแก่น",
        "zone": "มหาวิทยาลัยขอนแก่น",
        "type": "ในร่มปรับอากาศ สำหรับทัวร์นาเมนต์และอีเวนต์พิเศษ",
        "capacity": "อัฒจันทร์ 3,000+ ที่นั่ง",
    },
    {
        "id": "kk_pao_stadium",
        "name": "สนามกีฬาองค์การบริหารส่วนจังหวัดขอนแก่น (โรงยิมเนเซียม 1 อบจ. ขอนแก่น)",
        "zone": "อำเภอเมืองขอนแก่น",
        "type": "ในร่ม พื้นยางสังเคราะห์เกรดแข่งขัน",
        "capacity": "อัฒจันทร์ 2,000 ที่นั่ง",
    },
    {
        "id": "kk_central_hall",
        "name": "ขอนแก่น ฮอลล์ (Khon Kaen Hall) ชั้น 5 เซ็นทรัลพลาซา ขอนแก่น",
        "zone": "ใจกลางเมืองขอนแก่น",
        "type": "Convention Hall ติดแอร์เต็มระบบ สำหรับ Exhibition & All-Star",
        "capacity": "รองรับผู้ชม 2,500+ คน",
    },
    {
        "id": "kk_muni_gym",
        "name": "โรงยิมเนเซียม สนามกีฬาเทศบาลนครขอนแก่น (ยิม 2)",
        "zone": "เทศบาลนครขอนแก่น",
        "type": "ในร่ม พื้นปาร์เก้มาตรฐานกีฬาเทศบาล",
        "capacity": "อัฒจันทร์ 1,000 ที่นั่ง",
    },
    {
        "id": "kkw_gym",
        "name": "โรงยิมเนเซียม 100 ปี โรงเรียนขอนแก่นวิทยายน",
        "zone": "อำเภอเมืองขอนแก่น",
        "type": "ในร่ม เหมาะสำหรับลีกเยาวชนและบาสเกตบอล 3x3",
        "capacity": "อัฒจันทร์ 800 ที่นั่ง",
    },
    {
        "id": "bueng_kaen_nakhon",
        "name": "ลานกีฬาบาสเกตบอล สวนสาธารณะบึงแก่นนคร",
        "zone": "บึงแก่นนคร ขอนแก่น",
        "type": "Outdoor Streetball ริมบึง วิวโปร่งรับลม",
        "capacity": "อัฒจันทร์ริมสวนสาธารณะ",
    },
]

# --- ตัวเลือกช่วงเวลาจัดงานแข่งขัน ---
EVENT_TIME_SLOTS = [
    {"id": "morning", "name": "08:00 - 12:00 น. (ช่วงเช้า 4 ชม.)"},
    {"id": "afternoon", "name": "13:00 - 17:00 น. (ช่วงบ่าย 4 ชม.)"},
    {"id": "evening", "name": "17:00 - 21:00 น. (ช่วงค่ำ 4 ชม.)"},
    {"id": "fullday", "name": "08:00 - 17:00 น. (เต็มวัน 8 ชม.)"},
    {"id": "fullday_extended", "name": "09:00 - 21:00 น. (เต็มวันพิเศษ 12 ชม.)"},
    {"id": "two_days", "name": "09:00 - 18:00 น. ทั้ง 2 วัน (สุดสัปดาห์เต็มวัน)"},
]

# --- ฐานข้อมูล SQLite (.db) ---
DB_PATH = "database.db"


def get_db():
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  return conn


def backup_db():
  """สำรองข้อมูลฐานข้อมูล SQLite ลงไฟล์ database_backup.sql และ database_backup.db"""
  try:
    if not os.path.exists(DB_PATH):
      return False, "ไม่พบไฟล์ฐานข้อมูลต้นทาง"
    conn = get_db()
    with open(BACKUP_SQL_PATH, "w", encoding="utf-8") as f:
      for line in conn.iterdump():
        f.write(f"{line}\n")
    conn.close()
    shutil.copyfile(DB_PATH, BACKUP_DB_PATH)
    return True, "สำรองข้อมูลสำเร็จ"
  except Exception as e:
    return False, f"เกิดข้อผิดพลาดในการสำรองข้อมูล: {e}"


def restore_db():
  """กู้คืนข้อมูลจากไฟล์สำรอง database_backup.sql หรือ database_backup.db ทันทีหากฐานข้อมูลเดิมถูกลบ"""
  try:
    if os.path.exists(BACKUP_DB_PATH) and os.path.getsize(BACKUP_DB_PATH) > 0:
      shutil.copyfile(BACKUP_DB_PATH, DB_PATH)
      return True, "กู้คืนข้อมูลจากไฟล์สำรอง .db เรียบร้อยแล้ว"

    if os.path.exists(BACKUP_SQL_PATH) and os.path.getsize(BACKUP_SQL_PATH) > 0:
      conn = get_db()
      with open(BACKUP_SQL_PATH, "r", encoding="utf-8") as f:
        sql_script = f.read()
      conn.executescript(sql_script)
      conn.commit()
      conn.close()
      return True, "กู้คืนข้อมูลจากไฟล์สำรอง .sql เรียบร้อยแล้ว"

    init_db_schema_only()
    return True, "สร้างฐานข้อมูลใหม่พร้อมบัญชีผู้ดูแลระบบเริ่มต้นสำเร็จ"
  except Exception as e:
    return False, f"เกิดข้อผิดพลาดในการกู้คืน: {e}"


def init_db_schema_only():
  """สร้างโครงสร้างตารางและบัญชีแอดมินเริ่มต้น"""
  conn = get_db()
  cursor = conn.cursor()

  # ตารางผู้ใช้งาน (Users)
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT NOT NULL,
        fullname TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        province TEXT DEFAULT 'ขอนแก่น',
        avatar TEXT DEFAULT '',
        role TEXT DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

  # ตารางการจองอีเว้นท์ (Bookings)
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        package_id TEXT NOT NULL,
        package_name TEXT NOT NULL,
        base_price INTEGER NOT NULL,
        options TEXT DEFAULT '',
        extra_price INTEGER DEFAULT 0,
        total_price INTEGER NOT NULL,
        event_date TEXT NOT NULL,
        event_time TEXT DEFAULT '08:00 - 17:00 น. (เต็มวัน 8 ชม.)',
        venue TEXT DEFAULT 'ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น (KKU Sports Complex - โรงยิมเนเซียม 1 สนามหลักพื้นปาร์เก้)',
        deposit_amount REAL DEFAULT 0,
        deposit_rate REAL DEFAULT 0.05,
        payment_status TEXT DEFAULT 'deposit_paid',
        payment_method TEXT DEFAULT 'promptpay_0918303564',
        slip_image TEXT DEFAULT '',
        invoice_no TEXT DEFAULT '',
        note TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        admin_note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

  # ตรวจสอบและอัปเกรดคอลัมน์ใน users อัตโนมัติหากยังไม่มี
  cursor.execute("PRAGMA table_info(users)")
  user_cols = [c["name"] for c in cursor.fetchall()]
  if "avatar" not in user_cols:
    cursor.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
  if "province" not in user_cols:
    cursor.execute("ALTER TABLE users ADD COLUMN province TEXT DEFAULT 'ขอนแก่น'")

  # ตรวจสอบและอัปเกรดคอลัมน์ใน bookings อัตโนมัติหากยังไม่มี
  cursor.execute("PRAGMA table_info(bookings)")
  booking_cols = [c["name"] for c in cursor.fetchall()]
  if "event_time" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN event_time TEXT DEFAULT '08:00 - 17:00 น. (เต็มวัน 8 ชม.)'")
  if "venue" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN venue TEXT DEFAULT 'ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น (KKU Sports Complex - โรงยิมเนเซียม 1 สนามหลักพื้นปาร์เก้)'")
  if "deposit_amount" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN deposit_amount REAL DEFAULT 0")
  if "deposit_rate" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN deposit_rate REAL DEFAULT 0.05")
  if "payment_status" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN payment_status TEXT DEFAULT 'deposit_paid'")
  if "payment_method" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN payment_method TEXT DEFAULT 'promptpay_0918303564'")
  if "slip_image" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN slip_image TEXT DEFAULT ''")
  if "invoice_no" not in booking_cols:
    cursor.execute("ALTER TABLE bookings ADD COLUMN invoice_no TEXT DEFAULT ''")

  # สร้างบัญชีผู้ดูแลระบบเริ่มต้น (Default Admin) หากยังไม่มี และรับรองสิทธิ์ admin
  cursor.execute("SELECT * FROM users WHERE username = 'admin'")
  if not cursor.fetchone():
    cursor.execute(
        """
        INSERT INTO users (username, password, email, fullname, phone, province, role)
        VALUES ('admin', 'admin1234', 'basketballeventbooking@gmail.com', 'ผู้ดูแลระบบ (Admin)', '081-234-5678', 'ขอนแก่น', 'admin')
        """
    )
  else:
    cursor.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")

  conn.commit()
  conn.close()


def init_db():
  """สร้างตารางในฐานข้อมูล SQLite อัตโนมัติ พร้อมระบบตรวจจับและกู้คืนอัตโนมัติหากไฟล์ถูกลบ"""
  if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
    if (os.path.exists(BACKUP_DB_PATH) and os.path.getsize(BACKUP_DB_PATH) > 0) or (os.path.exists(BACKUP_SQL_PATH) and os.path.getsize(BACKUP_SQL_PATH) > 0):
      restore_db()
      return

  init_db_schema_only()
  backup_db()


# เรียกสร้างตารางทันทีเมื่อรันแอป
init_db()

# ตัวเก็บ OTP ชั่วคราวในหน่วยความจำ {email: otp_code}
otp_store = {}


# ส่งข้อมูล current_user, admin_user, member_user ให้ Jinja Template ทุกหน้าโดยอัตโนมัติ
# เพื่อแยกแท็บการทำงานระหว่างผู้ดูแลระบบ (Admin) และสมาชิก (Member) ได้อย่างอิสระ
@app.context_processor
def inject_user():
  admin_username = session.get("admin_user")
  member_username = session.get("user")

  admin_user_obj = None
  member_user_obj = None

  if admin_username or member_username:
    try:
      conn = get_db()
      cursor = conn.cursor()
      if admin_username:
        cursor.execute(
            "SELECT * FROM users WHERE username = ?", (admin_username,)
        )
        row = cursor.fetchone()
        if row and row["role"] == "admin":
          admin_user_obj = row
      if member_username:
        cursor.execute(
            "SELECT * FROM users WHERE username = ?", (member_username,)
        )
        member_user_obj = cursor.fetchone()
      conn.close()
    except Exception:
      admin_user_obj = None
      member_user_obj = None

  # ตัดสินใจ current_user และ admin_user ตามบริบทของ URL ที่เปิดใช้งาน
  if request.path.startswith("/admin"):
    # ในหน้าคอนโซลแอดมิน: ใช้บัญชี admin_user เป็นหลัก
    current_user_obj = admin_user_obj or (
        member_user_obj if member_user_obj and member_user_obj["role"] == "admin" else None
    )
    admin_context = admin_user_obj
  else:
    # ในหน้าสาธารณะ/สมาชิกลูกค้า: ใช้บัญชี member_user เป็นหลัก
    # ผู้ใช้งานทั่วไป (role != 'admin') หรือผู้เยี่ยมชม (Guest):
    # ต้องไม่ส่ง admin_user ให้ template หน้าสาธารณะเด็ดขาด ไม่ว่ากรณีใดก็ตาม
    current_user_obj = member_user_obj
    if current_user_obj and current_user_obj["role"] == "admin":
      admin_context = current_user_obj
    else:
      admin_context = None

  return dict(
      current_user=current_user_obj,
      admin_user=admin_context,
      member_user=member_user_obj,
  )


# --- ข้อมูลแพ็กเกจและโปรแกรมจัดงานอีเว้นท์แข่งขันบาสเกตบอล (ตรงกันทุกหน้า) ---
EVENT_PACKAGES = {
    "friendly": {
        "id": "friendly",
        "name": "กระชับมิตร & มินิทัวร์นาเมนต์ (Friendly Cup)",
        "scale_badge": "5x5 FULL COURT",
        "scale_name": "สเกลกระชับมิตร (4 - 6 ทีม)",
        "duration": "ครึ่งวัน (4-5 ชม.)",
        "price": 5000,
        "description": "รูปแบบการแข่งขันกระชับมิตรระหว่างสโมสร ชุมชน หรือกลุ่มเพื่อน เน้นความสนุกสนานและมาตรฐานการแข่งขันที่เป็นระเบียบ",
        "image": "/static/images/catalogs/catalog1.jpg",
        "category": "5v5",
        "features": [
            "สนามแข่งมาตรฐาน 1 สนาม (เวลาแข่งขัน 4-5 ชั่วโมง)",
            "กรรมการสมาคมฯ 2 ท่าน + โต๊ะเทคนิค",
            "สกอร์บอร์ดดิจิทัลและจับเวลา 24 วินาที",
            "ระบบปรับอากาศ & แสงสว่าง 800+ Lux",
            "อุปกรณ์การแข่งขันครบชุด (ลูกบาส, นกหวีด, ป้ายคะแนน)",
            "ผู้ดูแลสนามและประสานงาน 1 ท่าน",
        ],
    },
    "youth": {
        "id": "youth",
        "name": "ลีกบาสเกตบอลเยาวชน (Youth League)",
        "scale_badge": "YOUTH & SCHOOL",
        "scale_name": "สเกลเยาวชน (8 - 12 ทีม)",
        "duration": "เต็มวัน (8 ชม.)",
        "price": 12000,
        "description": "ทัวร์นาเมนต์สำหรับโรงเรียน อะคาเดมี และเยาวชน พร้อมระบบบันทึกสถิติรายบุคคลเพื่อการพัฒนาทักษะนักกีฬา",
        "image": "/static/images/catalogs/catalog2.jpg",
        "category": "youth",
        "features": [
            "สนามแข่งมาตรฐานเต็มวัน (8 ชั่วโมง)",
            "กรรมการตัดสินมืออาชีพมีใบอนุญาต 2 ท่าน",
            "บันทึกสถิติคะแนนรายบุคคล (Box Score)",
            "ถ่ายทอดสด Live Streaming Full HD",
            "เครื่องดื่มเกลือแร่และน้ำดื่มสำหรับนักกีฬา",
            "เจ้าหน้าที่ปฐมพยาบาลเบื้องต้นและหน่วยกู้ชีพสแตนด์บาย",
        ],
    },
    "corporate": {
        "id": "corporate",
        "name": "การแข่งขันกีฬาองค์กร (Corporate Derby)",
        "scale_badge": "CORPORATE CUP",
        "scale_name": "สเกลองค์กร (8 - 16 ทีม)",
        "duration": "1 - 2 วัน",
        "price": 18000,
        "description": "ทัวร์นาเมนต์เชื่อมความสัมพันธ์ระหว่างบริษัทและองค์กรธุรกิจ พร้อมระบบอำนวยความสะดวกครบครันและภาพลักษณ์ระดับมืออาชีพ",
        "image": "/static/images/catalogs/catalog3.jpg",
        "category": "corp",
        "features": [
            "สนามแข่งมาตรฐาน 2 สนาม พร้อมระบบปรับอากาศ",
            "ป้ายแบนเนอร์และบูธสปอนเซอร์รอบสนาม",
            "โต๊ะลงทะเบียน & ซุ้มเครื่องดื่มรับรอง",
            "ชุดถ้วยรางวัลเกียรติยศและเหรียญรางวัล",
            "กรรมการสมาคมฯ พร้อมโต๊ะเทคนิคควบคุมกติกาสากล",
            "ช่างภาพมืออาชีพบันทึกภาพตลอดการแข่งขัน",
        ],
    },
    "streetball": {
        "id": "streetball",
        "name": "สตรีทบาสเกตบอล 3x3 (Streetball Open)",
        "scale_badge": "3X3 FIBA RULES",
        "scale_name": "สเกล 3x3 สตรีท (16 - 32 ทีม)",
        "duration": "เต็มวัน (8 ชม.)",
        "price": 8500,
        "description": "การแข่งขันสตรีทบาสเกตบอล 3 คน กติกาสากล FIBA 3x3 รองรับทีมจำนวนมากด้วยการจัดสนามคู่ขนานรวดเร็วเร้าใจ",
        "image": "/static/images/catalogs/catalog4.jpg",
        "category": "3v3",
        "features": [
            "แบ่ง 2-4 คอร์ทย่อย กติกาสากล FIBA 3x3",
            "นาฬิกาช็อตคล็อก 12 วินาที และสกอร์บอร์ดดิจิทัล 3x3",
            "ระบบเครื่องเสียง ดีเจเปิดเพลงสร้างบรรยากาศ",
            "ทีมผู้ตัดสิน 3x3 ประจำทุกคอร์ท",
            "แบ่งสนามย่อยพร้อมกัน แข่งขันจบไวต่อเนื่อง",
            "ชุดถ้วยรางวัลเกียรติยศและเงินรางวัล",
        ],
    },
    "championship": {
        "id": "championship",
        "name": "ทัวร์นาเมนต์ชิงแชมป์ใหญ่ (Championship Open)",
        "scale_badge": "PREMIER TOURNAMENT",
        "scale_name": "สเกลใหญ่ (12 - 16 ทีม)",
        "duration": "2 วัน (สุดสัปดาห์)",
        "price": 25000,
        "description": "การแข่งขันชิงถ้วยเกียรติยศและเงินรางวัล ระดับมาตรฐานสูงสุด รองรับผู้ชมบนอัฒจันทร์และการถ่ายทอดสดเต็มรูปแบบ",
        "image": "/static/images/catalogs/catalog5.jpg",
        "category": "5v5",
        "features": [
            "สนามแข่งหลัก + อัฒจันทร์รองรับผู้ชมเต็มความจุ",
            "จัดแข่งขัน 2 วันเต็ม (สุดสัปดาห์)",
            "ผู้ตัดสินสมาคมฯ ระดับ A ควบคุมกติกาสากล",
            "ถ่ายทอดสด 4 มุมกล้องพร้อมทีมผู้บรรยายสด",
            "โพเดียมพิธีมอบรางวัลและช่างภาพมืออาชีพ",
            "ทีมแพทย์สนามและหน่วยกู้ชีพสแตนด์บายตลอด 2 วัน",
        ],
    },
    "special": {
        "id": "special",
        "name": "แมตช์พิเศษ & เปิดตัวสินค้า (All-Star Exhibition)",
        "scale_badge": "SPECIAL EVENT",
        "scale_name": "อีเวนต์พิเศษ (คัสตอม)",
        "duration": "ตามตกลง / คัสตอม",
        "price": 20000,
        "description": "งานอีเวนต์เปิดตัวแบรนด์ สปอนเซอร์ หรือแมตช์ดารา เซเลบริตี้ ที่ต้องการพื้นที่จัดการแสดง แสงสี และโซนต้อนรับแขกพิเศษ",
        "image": "/static/images/catalogs/catalog6.jpg",
        "category": "corp",
        "features": [
            "ระบบแสง สี เสียง และเวทีเปิดตัวงาน",
            "พื้นที่จัดแสดงบูธและโซนรับรองแขก VIP",
            "จอ LED สกอร์บอร์ดขนาดใหญ่แสดงมีเดียและโฆษณา",
            "เจ้าหน้าที่ประสานงานดูแลเฉพาะตลอดงาน",
            "บริการควบคุมความปลอดภัยและพื้นที่งาน",
            "ปรับแต่งสเกลและรูปแบบได้ตามความต้องการ",
        ],
    },
    "test": {
        "id": "test",
        "name": "แพ็กเกจทดสอบระบบ (System Test Package)",
        "scale_badge": "TEST & DEMO",
        "scale_name": "โหมดทดสอบระบบ (มัดจำ 1 บาท)",
        "duration": "1 ชั่วโมง (ทดสอบระบบ)",
        "price": 20,
        "description": "แพ็กเกจสำหรับทดสอบการทำงานของระบบจอง การโอนชำระเงินมัดจำ 5% (1 บาท) ผ่านพร้อมเพย์ 0918303564 และดาวน์โหลดใบเสร็จ/ใบกำกับภาษี (PDF) ได้ทันที",
        "image": "/static/images/packages/small.jpg",
        "category": "test",
        "features": [
            "ทดสอบชำระมัดจำเพียง 1 บาท ผ่านพร้อมเพย์ 0918303564",
            "ออกใบเสร็จรับเงิน / ใบกำกับภาษีเต็มรูปอัตโนมัติ",
            "ดาวน์โหลดใบเสร็จรับเงิน/ใบกำกับภาษี (PDF) ได้ทันที",
            "ระบบตัดคิวงานและแสดงในหน้าประวัติและการจัดการแอดมิน",
        ],
    },
}

# กำหนด Aliases เพื่อความเข้ากันได้ย้อนหลัง 100%
EVENT_PACKAGES["small"] = EVENT_PACKAGES["friendly"]
EVENT_PACKAGES["medium"] = EVENT_PACKAGES["youth"]
EVENT_PACKAGES["large"] = EVENT_PACKAGES["championship"]

# รายการคีย์โปรแกรมสำหรับแสดงผลในหน้าแคตตาล็อกและหน้าแพ็กเกจ (รวมแพ็กเกจทดสอบ 1 บาท)
UNIQUE_PROGRAM_KEYS = ["test", "friendly", "youth", "corporate", "streetball", "championship", "special"]

# --- ตั้งค่า Gmail SMTP (สำหรับส่ง OTP จริง ด้วย Google App Password 16 หลัก) ---
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "basketballeventbooking@gmail.com")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "lhko rxnl ubfz awvz")
EMAIL_SENDER = MAIL_USERNAME


def send_otp_email(receiver_email, otp_code):
  """ฟังก์ชันส่ง OTP ทาง Gmail SMTP พร้อมระบบความปลอดภัยและการจัดหน้าสวยงาม"""
  sender_email = MAIL_USERNAME.strip()
  app_password = MAIL_PASSWORD.replace(" ", "").strip()

  if (
      not sender_email
      or sender_email == "your_gmail_account@gmail.com"
      or "@" not in sender_email
  ):
    return (
        False,
        "ยังไม่ได้ระบุอีเมลผู้ส่ง กรุณาใส่อีเมล Gmail ของคุณในตัวแปร"
        " MAIL_USERNAME",
    )

  try:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"BKB EVENT BOOKING <{sender_email}>"
    msg["To"] = receiver_email
    msg["Subject"] = f"[{otp_code}] รหัส OTP ยืนยันตัวตน - BKB EVENT BOOKING"

    # ข้อความแบบ Plain Text เรียบง่าย เหมือนบิล
    body_plain = f"""========================================
BKB EVENT BOOKING
VERIFICATION SLIP // OTP
========================================
รหัส OTP: {otp_code}
========================================
• วัตถุประสงค์: ยืนยันการสมัครสมาชิก
• อายุการใช้งาน: 5 นาที
• ข้อควรระวัง: ห้ามเปิดเผยรหัสนี้แก่ผู้อื่น
========================================
"""

    # ข้อความแบบ บน OTP ที่ส่งไปในเมลล์จ้าคุณพรี่
    body_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: 'Courier New', Courier, monospace, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 30px 15px; color: #0f172a;">
        <div style="max-width: 360px; margin: 0 auto; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.03);">
            
            <div style="text-align: center; border-bottom: 1px dashed #94a3b8; padding-bottom: 12px; margin-bottom: 16px;">
                <div style="font-size: 15px; font-weight: bold; letter-spacing: 1px; color: #0f172a; text-transform: uppercase;">BKB EVENT BOOKING</div>
                <div style="font-size: 11px; color: #64748b; margin-top: 4px; letter-spacing: 0.5px;">VERIFICATION SLIP // OTP</div>
            </div>
            
            <div style="text-align: center; padding: 8px 0;">
                <div style="font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">ONE-TIME PASSWORD</div>
                <div style="display: inline-block; border: 1px solid #0f172a; padding: 10px 24px; background-color: #f8fafc; border-radius: 4px; margin: 4px 0 10px 0;">
                    <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #0f172a; font-family: 'Courier New', Courier, monospace; line-height: 1;">
                        {otp_code}
                    </span>
                </div>
            </div>
            
            <div style="border-top: 1px dashed #94a3b8; padding-top: 14px; margin-top: 14px; font-size: 11px; color: #475569; line-height: 1.6;">
                <table style="width: 100%; font-size: 11px; color: #475569; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 2px 0; color: #64748b;">วัตถุประสงค์:</td>
                        <td style="padding: 2px 0; text-align: right; font-weight: bold; color: #0f172a;">ยืนยันสมัครสมาชิก</td>
                    </tr>
                    <tr>
                        <td style="padding: 2px 0; color: #64748b;">อายุการใช้งาน:</td>
                        <td style="padding: 2px 0; text-align: right; font-weight: bold; color: #0f172a;">5 นาที</td>
                    </tr>
                    <tr>
                        <td style="padding: 2px 0; color: #64748b;">สถานะ:</td>
                        <td style="padding: 2px 0; text-align: right; font-weight: bold; color: #0f172a;">รหัสใช้ครั้งเดียว</td>
                    </tr>
                </table>
                <div style="margin-top: 12px; font-size: 10px; color: #94a3b8; text-align: center; border-top: 1px dotted #e2e8f0; padding-top: 8px;">
                    * โปรดอย่าเปิดเผยรหัสนี้แก่ผู้อื่นเพื่อความปลอดภัย
                </div>
            </div>
            
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15)
    server.starttls()
    server.login(sender_email, app_password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()
    return True, None
  except smtplib.SMTPServerDisconnected:
    return (
        False,
        "การเชื่อมต่อไปยัง Gmail ถูกตัด (กรุณาตรวจสอบว่าใส่อีเมล Gmail ใน"
        " MAIL_USERNAME ถูกต้อง)",
    )
  except smtplib.SMTPAuthenticationError:
    return (
        False,
        "เข้าสู่ระบบ Gmail ไม่สำเร็จ กรุณาตรวจสอบอีเมลและรหัสผ่านแอป 16 หลัก",
    )
  except Exception as e:
    try:
      print("Error sending email via Gmail:", repr(e))
    except Exception:
      pass
    return False, str(e)


def baht_text(amount):
  """แปลงตัวเลขจำนวนเงินเป็นข้อความภาษาไทย (บาทถ้วน / สตางค์) ตามมาตรฐานกรมสรรพากร"""
  try:
    amount = float(amount)
  except (ValueError, TypeError):
    return ""

  units = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
  positions = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน", "ล้าน"]

  def convert_group(n):
    res = ""
    s = str(n)
    l = len(s)
    for i, digit in enumerate(s):
      d = int(digit)
      pos = l - i - 1
      if d != 0:
        if pos == 1 and d == 1:
          res += "สิบ"
        elif pos == 1 and d == 2:
          res += "ยี่สิบ"
        elif pos == 0 and d == 1 and l > 1:
          res += "เอ็ด"
        else:
          res += units[d] + positions[pos]
    return res

  is_negative = amount < 0
  amount = abs(amount)
  baht = int(amount)
  satang = int(round((amount - baht) * 100))

  baht_str = ""
  if baht == 0:
    baht_str = "ศูนย์บาท"
  else:
    millions = baht // 1000000
    remainder = baht % 1000000
    if millions > 0:
      baht_str += convert_group(millions) + "ล้าน"
    if remainder > 0 or millions == 0:
      baht_str += convert_group(remainder)
    baht_str += "บาท"

  satang_str = ""
  if satang == 0:
    satang_str = "ถ้วน"
  else:
    satang_str = convert_group(satang) + "สตางค์"

  return ("ลบ" if is_negative else "") + baht_str + satang_str


def send_invoice_email(receiver_email, booking_dict, user_dict):
  """ฟังก์ชันส่งใบเสร็จรับเงิน/ใบกำกับภาษีอิเล็กทรอนิกส์ (e-Tax Invoice & e-Receipt) ตามแบบมาตรฐานกรมสรรพากร"""
  sender_email = MAIL_USERNAME.strip()
  app_password = MAIL_PASSWORD.replace(" ", "").strip()

  if not sender_email or "@" not in sender_email or not receiver_email:
    return False, "ยังไม่ได้กำหนดค่าอีเมลผู้ส่งหรือผู้รับ"

  invoice_no = booking_dict.get("invoice_no") or f"INV-{booking_dict.get('id', 1):05d}"
  pkg_name = booking_dict.get("package_name", "แพ็กเกจจัดงานบาสเกตบอล")
  base_price = booking_dict.get("base_price", 0)
  extra_price = booking_dict.get("extra_price", 0)
  total_price = booking_dict.get("total_price", 0)
  deposit_amount = booking_dict.get("deposit_amount", 0)
  remaining_balance = max(0, total_price - deposit_amount)
  event_date = booking_dict.get("event_date", "-")
  event_time = booking_dict.get("event_time", "-")
  venue = booking_dict.get("venue", "-")
  created_at = str(booking_dict.get("created_at", ""))[:16]
  fullname = user_dict.get("fullname") or user_dict.get("username", "ลูกค้าผู้มีอุปการคุณ")
  username = user_dict.get("username", "-")
  province = user_dict.get("province", "ขอนแก่น")
  phone = user_dict.get("phone", "-")
  options_list = booking_dict.get("options_list", [])

  subtotal_no_vat = round(total_price / 1.07, 2)
  vat_amount = round(total_price - subtotal_no_vat, 2)
  base_no_vat = round(base_price / 1.07, 2)
  extra_no_vat = round(extra_price / 1.07, 2)
  baht_total = baht_text(total_price)
  baht_deposit = baht_text(deposit_amount)

  options_html = ""
  if options_list:
    options_html = f"""<tr>
      <td style="text-align: center; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">2</td>
      <td style="padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">
        <b>ออพชั่นเสริมและอุปกรณ์เทคนิค:</b> {', '.join(options_list)}
      </td>
      <td style="text-align: center; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">1 ชุด</td>
      <td style="text-align: right; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">฿{extra_no_vat:,.2f}</td>
      <td style="text-align: right; font-family: monospace; font-weight: bold; padding: 8px 10px; border-bottom: 1px solid #000;">฿{extra_no_vat:,.2f}</td>
    </tr>"""

  signature_path = os.path.join(app.root_path, "static", "images", "signature.png")
  has_signature = os.path.exists(signature_path)
  if has_signature:
    signature_img_html = '<img src="cid:organizer_signature" alt="Digital Signature" style="height: 48px; width: auto; display: block; margin: 0 auto -6px auto;">'
  else:
    signature_img_html = '<div style="height: 42px;"></div>'

  try:
    msg = MIMEMultipart("related")
    msg["From"] = f"BKB EVENT BOOKING <{sender_email}>"
    msg["To"] = receiver_email
    msg["Subject"] = f"[ใบเสร็จรับเงิน/ใบกำกับภาษี] เลขที่ {invoice_no} - BKB EVENT BOOKING ({pkg_name})"

    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)

    body_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Sarabun', 'TH Sarabun New', Tahoma, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px 10px; color: #0f172a; line-height: 1.4; }}
            .invoice-box {{ max-width: 740px; margin: 0 auto; background: #ffffff; border: 1px solid #cbd5e1; padding: 28px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
            .table-main {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 11px; border: 1px solid #1e293b; }}
            .table-main th {{ background: #f1f5f9; color: #0f172a; padding: 8px 10px; border: 1px solid #1e293b; font-size: 11px; font-weight: bold; }}
            .table-main td {{ border: 1px solid #1e293b; }}
        </style>
    </head>
    <body>
        <div class="invoice-box">
            <!-- ส่วนหัวเอกสารทางการ: 2 ฝั่งซ้าย-ขวา -->
            <table style="width: 100%; border-bottom: 2px solid #0f172a; padding-bottom: 12px; margin-bottom: 14px;">
                <tr>
                    <td style="vertical-align: top; width: 62%;">
                        <div style="font-size: 14px; font-weight: bold; color: #0f172a;">บริษัท บีเคบี อีเวนต์ บุ๊คกิ้ง จำกัด (สำนักงานใหญ่)</div>
                        <div style="font-size: 11px; font-weight: bold; color: #475569;">BKB EVENT BOOKING CO., LTD. (HEAD OFFICE)</div>
                        <div style="font-size: 10px; color: #475569; margin-top: 2px;">123 ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น ถ.มิตรภาพ ต.ในเมือง อ.เมือง จ.ขอนแก่น 40002</div>
                        <div style="font-size: 10px; color: #475569;">โทรศัพท์: 091-830-3564 | โทรสาร: 043-000-000</div>
                        <div style="font-size: 10px; color: #0f172a; font-weight: bold; margin-top: 2px;">เลขประจำตัวผู้เสียภาษีอากร: ********* (สำนักงานใหญ่)</div>
                    </td>
                    <td style="vertical-align: top; width: 38%; text-align: right;">
                        <div style="display: inline-block; border: 1px solid #0f172a; padding: 2px 6px; font-size: 9px; font-weight: bold; font-family: monospace;">
                            ต้นฉบับ / ORIGINAL
                        </div>
                        <div style="font-size: 9px; color: #64748b; margin-top: 2px;">เอกสารออกเป็นชุด (ISSUED AS A SET)</div>
                        <div style="font-size: 15px; font-weight: bold; color: #0f172a; margin-top: 4px;">ใบเสร็จรับเงิน / ใบกำกับภาษี</div>
                        <div style="font-size: 10px; font-weight: bold; color: #475569; font-family: monospace;">RECEIPT / TAX INVOICE</div>
                        <div style="font-size: 9px; color: #64748b;">(ตามประมวลรัษฎากร มาตรา 86/4)</div>
                    </td>
                </tr>
            </table>

            <!-- กล่อง 2 กล่องคู่ขนาน (ข้อมูลผู้ซื้อ และ ข้อมูลเอกสาร) -->
            <table style="width: 100%; border-collapse: separate; border-spacing: 8px 0; margin-bottom: 12px; font-size: 11px;">
                <tr>
                    <!-- กล่องซ้าย: ผู้ซื้อ/ผู้รับบริการ -->
                    <td style="width: 55%; vertical-align: top; border: 1px solid #1e293b; padding: 10px 12px; line-height: 1.6;">
                        <div style="font-weight: bold; margin-bottom: 4px; border-bottom: 1px solid #cbd5e1; padding-bottom: 2px; font-size: 10px; color: #0f172a;">ข้อมูลผู้ซื้อ / ผู้รับบริการ (CUSTOMER DETAILS):</div>
                        <div><b>ชื่อผู้ซื้อ / องค์กร:</b> {fullname} (@{username})</div>
                        <div><b>ที่อยู่:</b> จังหวัด{province} 40000</div>
                        <div><b>เลขประจำตัวผู้เสียภาษี:</b> 01055xxxxxxxx (สำนักงานใหญ่)</div>
                        <div><b>โทรศัพท์ / อีเมล:</b> {phone} | {receiver_email}</div>
                    </td>
                    <!-- กล่องขวา: ข้อมูลเอกสาร -->
                    <td style="width: 45%; vertical-align: top; border: 1px solid #1e293b; padding: 10px 12px; line-height: 1.6;">
                        <div style="font-weight: bold; margin-bottom: 4px; border-bottom: 1px solid #cbd5e1; padding-bottom: 2px; font-size: 10px; color: #0f172a;">ข้อมูลเอกสาร (DOCUMENT DETAILS):</div>
                        <div><b>เลขที่ใบกำกับภาษี:</b> <span style="font-family: monospace; font-weight: bold; color: #0f172a;">{invoice_no}</span></div>
                        <div><b>วันที่ออกเอกสาร:</b> {created_at[:10]}</div>
                        <div><b>วันจัดงาน:</b> <span style="font-family: monospace;">{event_date}</span> ({event_time})</div>
                        <div><b>สถานที่จัดงาน:</b> {venue}</div>
                    </td>
                </tr>
            </table>

            <!-- ตารางรายการสินค้าและบริการ (Itemized Table) -->
            <table class="table-main">
                <thead>
                    <tr>
                        <th style="width: 35px; text-align: center;">ลำดับ<br><span style="font-size: 9px; font-weight: normal;">NO.</span></th>
                        <th style="text-align: left;">รายการสินค้าและบริการ<br><span style="font-size: 9px; font-weight: normal;">Description</span></th>
                        <th style="width: 60px; text-align: center;">จำนวน<br><span style="font-size: 9px; font-weight: normal;">Quantity</span></th>
                        <th style="width: 100px; text-align: right;">ราคาต่อหน่วย<br><span style="font-size: 9px; font-weight: normal;">Unit Price</span></th>
                        <th style="width: 110px; text-align: right;">จำนวนเงิน (ไม่รวมภาษี)<br><span style="font-size: 9px; font-weight: normal;">Amount (Baht)</span></th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="text-align: center; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">1</td>
                        <td style="padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">
                            <b>{pkg_name}</b>
                            <div style="font-size: 10px; color: #64748b;">บริการจัดการแข่งขันบาสเกตบอลครบวงจร พร้อมอุปกรณ์และมาตรฐานสากล</div>
                        </td>
                        <td style="text-align: center; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">1 งาน</td>
                        <td style="text-align: right; font-family: monospace; padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">฿{base_no_vat:,.2f}</td>
                        <td style="text-align: right; font-family: monospace; font-weight: bold; padding: 8px 10px; border-bottom: 1px solid #000;">฿{base_no_vat:,.2f}</td>
                    </tr>
                    {options_html}
                    <!-- ท้ายตารางสรุปภาษี -->
                    <tr>
                        <td colspan="3" rowspan="2" style="padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000; vertical-align: middle; background-color: #fafafa;">
                            <div style="font-size: 10px; color: #475569;">จำนวนเงินรวมทั้งสิ้น (ตัวอักษร):</div>
                            <div style="font-size: 12px; font-weight: bold; color: #000; margin-top: 2px;">({baht_total})</div>
                        </td>
                        <td style="padding: 6px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000; text-align: right; font-size: 10px;">ราคารวมเงินทั้งสิ้น (Sub Total):</td>
                        <td style="padding: 6px 10px; border-bottom: 1px solid #000; text-align: right; font-family: monospace;">฿{subtotal_no_vat:,.2f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000; text-align: right; font-size: 10px;">จำนวนภาษีมูลค่าเพิ่ม (VAT 7%):</td>
                        <td style="padding: 6px 10px; border-bottom: 1px solid #000; text-align: right; font-family: monospace;">฿{vat_amount:,.2f}</td>
                    </tr>
                    <tr style="background-color: #f1f5f9; font-weight: bold;">
                        <td colspan="3" style="padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000;">
                            <span style="color: #166534;">หัก เงินมัดจำชำระแล้ว (PromptPay 0918303564):</span>
                            <span style="font-family: monospace; font-size: 11px; margin-left: 6px;">-฿{deposit_amount:,.2f} ({baht_deposit})</span>
                        </td>
                        <td style="padding: 8px 10px; border-right: 1px solid #000; border-bottom: 1px solid #000; text-align: right;">จำนวนเงินทั้งสิ้น (Grand Total):</td>
                        <td style="padding: 8px 10px; border-bottom: 1px solid #000; text-align: right; font-family: monospace; font-size: 13px;">฿{total_price:,}</td>
                    </tr>
                    <tr style="background-color: #fff1f2; font-weight: bold;">
                        <td colspan="3" style="padding: 6px 10px; border-right: 1px solid #000; color: #991b1b; font-size: 10px;">
                            * ยอดคงค้างชำระ ณ จุดลงทะเบียนสนามแข่งในวันแข่งขันจริง
                        </td>
                        <td style="padding: 6px 10px; border-right: 1px solid #000; text-align: right; color: #dc2626;">ยอดคงค้างวันงาน (Remaining):</td>
                        <td style="padding: 6px 10px; text-align: right; font-family: monospace; font-size: 12px; color: #dc2626;">฿{remaining_balance:,.2f}</td>
                    </tr>
                </tbody>
            </table>

            <!-- กล่องด้านล่าง 3 ส่วน (ผู้รับเงิน, ชำระเงินโดย, ผู้มีอำนาจลงนาม) ตามแบบรูปที่ 2 -->
            <table style="width: 100%; border: 1px solid #000; border-radius: 8px; margin-top: 14px; font-size: 10px; border-collapse: collapse;">
                <tr>
                    <!-- ส่วน 1: ผู้รับเงิน (Collector) -->
                    <td style="width: 32%; padding: 12px 10px; vertical-align: bottom; border-right: 1px solid #000; text-align: center;">
                        {signature_img_html}
                        <div style="border-bottom: 1px dotted #000; width: 140px; margin-left: auto; margin-right: auto; margin-bottom: 4px;"></div>
                        <div style="font-weight: bold; margin-top: 4px;">ผู้รับเงิน (Collector)</div>
                        <div style="font-size: 10px; color: #475569;">(ฝ่ายการเงินและการจัดการแข่งขัน BKB EVENT)</div>
                        <div style="color: #64748b; margin-top: 2px;">วันที่/Date: {created_at[:10]}</div>
                    </td>

                    <!-- ส่วน 2: ชำระเงินโดย (Paid By) -->
                    <td style="width: 36%; padding: 10px 12px; vertical-align: top; border-right: 1px solid #000; line-height: 1.5;">
                        <div style="font-weight: bold; margin-bottom: 4px; border-bottom: 1px dashed #cbd5e1; padding-bottom: 2px;">ชำระเงินโดย (Paid By):</div>
                        <div><b>[X] โอนเงิน (Transfer)</b> พร้อมเพย์ 091-830-3564</div>
                        <div style="color: #64748b;">[ &nbsp; ] เงินสด (Cash)</div>
                        <div style="color: #64748b;">[ &nbsp; ] เช็คธนาคาร (Cheque Bank)</div>
                        <div style="margin-top: 4px; font-size: 9px; color: #475569;">
                            ธนาคาร: พร้อมเพย์ • เลขที่อ้างอิง: {invoice_no}-TXN<br>
                            วันที่: {created_at[:10]}
                        </div>
                    </td>

                    <!-- ส่วน 3: ผู้จ่ายเงิน / ผู้รับบริการ / ผู้เบิกงบ (Customer / Payer / Reimbursed By) -->
                    <td style="width: 32%; padding: 12px 10px; vertical-align: bottom; text-align: center;">
                        <div style="height: 48px;"></div>
                        <div style="border-bottom: 1px dashed #000; width: 140px; margin-left: auto; margin-right: auto; margin-bottom: 4px;"></div>
                        <div style="font-weight: bold; margin-top: 4px;">ผู้จ่ายเงิน / ผู้รับบริการ / ผู้เบิกงบ</div>
                        <div style="font-size: 9px; color: #475569;">(สำหรับใช้ประกอบการเบิกจ่าย / ตัวแทนหน่วยงาน)</div>
                        <div style="font-size: 10px; color: #000; margin-top: 1px;">({fullname})</div>
                        <div style="color: #64748b; margin-top: 2px;">วันที่/Date: ........................</div>
                    </td>
                </tr>
            </table>

            <!-- กล่องแจ้งเตือนนโยบายคุ้มครองและการคืนเงินมัดจำ -->
            <div style="margin-top: 14px; padding: 10px 14px; background-color: #f8fafc; border: 1px solid #cbd5e1; font-size: 10px; color: #334155; line-height: 1.5;">
                <b style="font-size: 11px; color: #0f172a;">เงื่อนไขและการคุ้มครองการจอง (REFUND POLICY):</b><br>
                หากท่านชำระเงินมัดจำแล้ว และทางผู้จัดมีความจำเป็นต้องปฏิเสธการจองเนื่องจากกรณีวันดังกล่าวบุคลากรหรือกรรมการตัดสินไม่เพียงพอ <b>ทางผู้จัดจะทำการคืนเงินมัดจำเต็มจำนวนทุกกรณี ภายในเวลาทำการ 3 - 5 วัน</b>
            </div>

            <!-- ข้อความรับรองอิเล็กทรอนิกส์ตามกฎหมายสรรพากร -->
            <div style="margin-top: 14px; text-align: center; font-size: 9px; color: #64748b; border-top: 1px dashed #cbd5e1; padding-top: 8px;">
                เอกสารนี้ได้จัดทำและส่งข้อมูลให้แก่กรมสรรพากรด้วยวิธีการทางอิเล็กทรอนิกส์ (e-Tax Invoice &amp; e-Receipt) • ออกโดยระบบอัตโนมัติ BKB EVENT BOOKING
            </div>
        </div>
    </body>
    </html>
    """

    msg_alt.attach(MIMEText(body_html, "html", "utf-8"))

    if has_signature:
      try:
        with open(signature_path, "rb") as sig_f:
          sig_img = MIMEImage(sig_f.read(), _subtype="png")
          sig_img.add_header("Content-ID", "<organizer_signature>")
          sig_img.add_header("Content-Disposition", "inline", filename="signature.png")
          msg.attach(sig_img)
      except Exception as sig_err:
        app.logger.warning(f"Could not attach signature to email: {sig_err}")

    server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15)
    server.starttls()
    server.login(sender_email, app_password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()
    return True, "ส่งใบเสร็จรับเงิน/ใบกำกับภาษีไปยังอีเมลเรียบร้อยแล้ว"
  except Exception as e:
    return False, f"ไม่สามารถส่งอีเมลใบเสร็จได้: {e}"


# ==========================================
# ROUTES
# ==========================================


# 1. หน้าเริ่มต้นก่อนเข้าเว็บ (Title Screen / Portal สไตล์ล็อบบี้เกมอนิเมะ ตามภาพตัวอย่าง)
@app.route("/")
def hello_world():
  banners = get_banner_images()
  open_modal = request.args.get("modal")  # 'login' or 'register'
  register_step = int(request.args.get("step", 1))
  temp_reg = session.get("temp_register") or {}
  return render_template(
      "portal.html",
      banners=banners,
      open_modal=open_modal,
      register_step=register_step,
      email=temp_reg.get("email", ""),
      province=temp_reg.get("province", "ขอนแก่น"),
      provinces=THAI_PROVINCES,
      temp_reg=temp_reg,
  )


# 1.1 เว็บหน้าหลัก (Main Lobby / Home สำหรับผู้ที่กดเข้าเยี่ยมชมเว็บเฉย ๆ)
@app.route("/home")
@app.route("/lobby")
def home_page():
  programs = {k: EVENT_PACKAGES[k] for k in UNIQUE_PROGRAM_KEYS if k in EVENT_PACKAGES}
  return render_template("home.html", programs=programs)


# 1.1 หน้าแพ็กเกจจัดงาน (Packages)
@app.route("/packages")
def packages_page():
  programs = {k: EVENT_PACKAGES[k] for k in UNIQUE_PROGRAM_KEYS if k in EVENT_PACKAGES}
  return render_template("packages.html", packages=programs)


# Route ให้บริการรูปภาพจากโฟลเดอร์ images/ โดยตรง (เพื่อให้ผู้ใช้ drop รูปภาพลงใน images/ หรือ static/images/ ได้ทันที)
@app.route("/images/<path:filename>")
def serve_project_images(filename):
  img_dir = os.path.join(app.root_path, "images")
  target_file = os.path.join(img_dir, filename)
  if os.path.exists(target_file):
    return send_from_directory(img_dir, filename)
  static_img_dir = os.path.join(app.root_path, "static", "images")
  return send_from_directory(static_img_dir, filename)


# 1.2 หน้าตรวจสอบวันว่าง & ปฏิทินคิวงาน (Schedule)
@app.route("/schedule")
def schedule_page():
  today_str = datetime.now().strftime("%Y-%m-%d")
  conn = get_db()
  cursor = conn.cursor()
  # ดึงรายการวันที่ตั้งแต่ปัจจุบันเป็นต้นไป (วันก่อนหน้าไม่นำมาแสดง) ที่มีคิวงานรอตรวจสอบหรืออนุมัติแล้ว
  cursor.execute(
      """
      SELECT id, event_date, event_time, package_name, venue, status, deposit_amount
      FROM bookings 
      WHERE event_date >= ? AND status IN ('confirmed', 'pending')
      ORDER BY event_date ASC, id ASC
      """,
      (today_str,),
  )
  booked_dates = cursor.fetchall()
  conn.close()
  return render_template(
      "schedule.html", booked_dates=booked_dates, today_str=today_str
  )


# 1.3 หน้ามาตรฐานสนาม & บริการจัดการแข่งขัน (Standards)
@app.route("/standards")
def standards_page():
  return render_template("standards.html")


# API สำหรับเช็กสถานะวันว่างแบบ Real-time พร้อมป้องกันวันในอดีตและแสดงข้อมูลรายการจอง
@app.route("/api/check-date")
def api_check_date():
  query_date = request.args.get("date", "").strip()
  if not query_date:
    return jsonify({"status": "invalid", "message": "กรุณาระบุวันที่ต้องการตรวจสอบ"})

  today_str = datetime.now().strftime("%Y-%m-%d")
  # ป้องกันไม่ให้กดดูหรือจองวันก่อนหน้าวันปัจจุบัน
  if query_date < today_str:
    return jsonify({
        "status": "past",
        "event_date": query_date,
        "message": f"วันที่ {query_date} เป็นวันก่อนหน้าวันปัจจุบัน ไม่สามารถเลือกตรวจสอบหรือจองได้",
    })

  conn = get_db()
  cursor = conn.cursor()
  cursor.execute(
      """
      SELECT id, package_name, venue, event_time, status, deposit_amount
      FROM bookings 
      WHERE event_date = ? AND status IN ('confirmed', 'pending')
      ORDER BY (CASE WHEN status = 'confirmed' THEN 1 ELSE 2 END) ASC, id DESC
      """,
      (query_date,),
  )
  rows = cursor.fetchall()
  conn.close()

  # ตรวจสอบสถานะการจองในวันนั้น
  confirmed_b = next((r for r in rows if r["status"] == "confirmed"), None)
  pending_b = next((r for r in rows if r["status"] == "pending"), None)

  if confirmed_b:
    return jsonify({
        "status": "confirmed",
        "event_date": query_date,
        "booking_id": confirmed_b["id"],
        "package_name": confirmed_b["package_name"],
        "venue": confirmed_b["venue"],
        "event_time": confirmed_b["event_time"],
        "message": f"วันที่ {query_date} มีการจัดงานแข่งขันแล้ว (คิวเต็ม)",
    })
  elif pending_b:
    return jsonify({
        "status": "pending",
        "event_date": query_date,
        "booking_id": pending_b["id"],
        "package_name": pending_b["package_name"],
        "venue": pending_b["venue"],
        "event_time": pending_b["event_time"],
        "message": f"วันที่ {query_date} มีคำขอจองส่งเข้ามาแล้ว (อยู่ระหว่างรอตรวจสอบ)",
    })
  else:
    return jsonify({
        "status": "available",
        "event_date": query_date,
        "message": f"วันที่ {query_date} สนามว่างพร้อมให้บริการทุกช่วงเวลา!",
    })


# 2. หน้า Login
@app.route("/login", methods=["GET", "POST"])
def login():
  init_db()  # กู้คืนบัญชีแอดมินเริ่มต้นอัตโนมัติหากฐานข้อมูลถูกล้าง
  if request.method == "GET":
    if "user" in session:
      return redirect(url_for("home_page"))
    return redirect(url_for("hello_world", modal="login"))

  username = (request.form.get("username") or "").strip()
  password = (request.form.get("password") or "").strip()

  conn = get_db()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT * FROM users WHERE username = ? AND password = ?",
      (username, password),
  )
  user = cursor.fetchone()
  conn.close()

  if user:
    if user["role"] == "admin":
      session["admin_user"] = user["username"]
      if "user" not in session or session.get("user") == user["username"]:
        session["user"] = user["username"]
      flash(
          f"ยินดีต้อนรับผู้ดูแลระบบ {user['fullname'] or user['username']}"
          " เข้าสู่ระบบสำเร็จ",
          "success",
      )
      return redirect(url_for("admin_bookings"))
    else:
      session["user"] = user["username"]
      flash(
          f"ยินดีต้อนรับคุณ {user['fullname'] or user['username']}"
          " เข้าสู่ระบบสำเร็จ!",
          "success",
      )
      return redirect(url_for("home_page"))
  else:
    flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง", "error")
    banners = get_banner_images()
    temp_reg = session.get("temp_register") or {}
    return render_template(
        "portal.html",
        banners=banners,
        open_modal="login",
        register_step=1,
        email=temp_reg.get("email", ""),
        province=temp_reg.get("province", "ขอนแก่น"),
        provinces=THAI_PROVINCES,
        temp_reg=temp_reg,
    )


# 3. หน้า Register (ส่ง OTP ทางอีเมล แล้วสร้างข้อมูลลง SQLite .db บนหน้าโปรโมทแบนเนอร์)
@app.route("/register", methods=["GET", "POST"])
def register():
  banners = get_banner_images()
  if request.method == "GET":
    if "user" in session:
      return redirect(url_for("home_page"))
    # ส่งต่อไปยังหน้าโปรโมทแบนเนอร์ พร้อมเปิดแท็บสมัครสมาชิก
    return redirect(url_for("hello_world", modal="register"))

  if request.method == "POST":
    action = request.form.get("action")

    # ขั้นตอนที่ 1: รับข้อมูลแล้วส่ง OTP ไปทางอีเมล
    if action == "request_otp":
      username = (request.form.get("username") or "").strip()
      password = (request.form.get("password") or "").strip()
      email = (request.form.get("email") or "").strip()
      province = (request.form.get("province") or "ขอนแก่น").strip()

      if not username or not password or not email:
        flash("กรุณากรอกข้อมูลให้ครบทุกช่อง", "error")
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=1,
            provinces=THAI_PROVINCES,
            reg_username=username,
            reg_email=email,
            reg_province=province,
        )

      is_valid_pwd, pwd_err = validate_password_strength(password)
      if not is_valid_pwd:
        flash(pwd_err, "error")
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=1,
            provinces=THAI_PROVINCES,
            reg_username=username,
            reg_email=email,
            reg_province=province,
        )

      conn = get_db()
      cursor = conn.cursor()
      cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
      existing_user = cursor.fetchone()
      conn.close()

      if existing_user:
        flash("ชื่อผู้ใช้นี้ถูกใช้งานแล้ว กรุณาใช้ชื่ออื่น", "error")
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=1,
            provinces=THAI_PROVINCES,
            reg_username=username,
            reg_email=email,
            reg_province=province,
        )

      generated_otp = str(random.randint(100000, 999999))
      session["temp_register"] = {
          "username": username,
          "password": password,
          "email": email,
          "province": province,
          "otp": generated_otp,
      }
      otp_store[email] = generated_otp

      email_sent, error_msg = send_otp_email(email, generated_otp)
      if email_sent:
        flash(
            f"ระบบได้ส่งรหัส OTP 6 หลักไปยังอีเมล {email} เรียบร้อยแล้ว",
            "success",
        )
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=2,
            email=email,
            province=province,
            provinces=THAI_PROVINCES,
            temp_reg=session.get("temp_register"),
        )
      else:
        flash(
            f"ไม่สามารถส่งอีเมลได้: {error_msg or 'กรุณาตรวจสอบการตั้งค่า Gmail'}",
            "error",
        )
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=1,
            provinces=THAI_PROVINCES,
        )

    # ขั้นตอนที่ 2: ตรวจสอบ OTP และบันทึกลงฐานข้อมูล SQLite
    elif action == "verify_otp":
      user_otp = (request.form.get("otp") or "").strip()
      temp_data = session.get("temp_register")

      if temp_data:
        email = temp_data["email"]
        province = temp_data.get("province") or "ขอนแก่น"
        correct_otp = (temp_data.get("otp") or otp_store.get(email) or "").strip()

        if user_otp and correct_otp and user_otp == correct_otp:
          try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (username, password, email, fullname, province, role)
                VALUES (?, ?, ?, ?, ?, 'user')
                """,
                (
                    temp_data["username"],
                    temp_data["password"],
                    temp_data["email"],
                    temp_data["username"],
                    province,
                ),
            )
            conn.commit()
            conn.close()

            session.pop("temp_register", None)
            otp_store.pop(email, None)

            # เข้าสู่ระบบให้อัตโนมัติหลังยืนยัน OTP สำเร็จ
            session["user"] = temp_data["username"]
            flash(
                "ยืนยัน OTP และสมัครสมาชิกสำเร็จ ยินดีต้อนรับ!",
                "success",
            )
            return redirect(url_for("home_page"))
          except Exception as e:
            flash(f"เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}", "error")
            return render_template(
                "portal.html",
                banners=banners,
                open_modal="register",
                register_step=1,
                provinces=THAI_PROVINCES,
            )
        else:
          flash(
              "รหัส OTP ไม่ถูกต้อง กรุณาตรวจสอบในอีเมลอีกครั้ง",
              "error",
          )
          return render_template(
              "portal.html",
              banners=banners,
              open_modal="register",
              register_step=2,
              email=email,
              province=province,
              provinces=THAI_PROVINCES,
              temp_reg=temp_data,
          )
      else:
        flash(
            "เซสชันหมดอายุ กรุณากรอกข้อมูลเพื่อขอรหัส OTP ใหม่อีกครั้ง",
            "error",
        )
        return render_template(
            "portal.html",
            banners=banners,
            open_modal="register",
            register_step=1,
            provinces=THAI_PROVINCES,
        )

  temp_reg = session.get("temp_register") or {}
  return render_template(
      "portal.html",
      banners=banners,
      open_modal="register",
      register_step=1,
      email=temp_reg.get("email", ""),
      province=temp_reg.get("province", "ขอนแก่น"),
      provinces=THAI_PROVINCES,
      temp_reg=temp_reg,
  )


# 4. หน้า Logout
@app.route("/logout")
def logout():
  curr_user = session.pop("user", None)
  # หากบัญชีที่ล็อกอินอยู่เป็นบัญชีเดียวกับ admin_user ให้เคลียร์ admin_user ด้วย
  if curr_user and session.get("admin_user") == curr_user:
    session.pop("admin_user", None)
  flash("ออกจากระบบเรียบร้อยแล้ว", "info")
  return redirect(url_for("hello_world"))


# 5. หน้าโปรไฟล์ & แก้ไขข้อมูลตัวเอง (Profile & Edit Profile)
@app.route("/profile", methods=["GET", "POST"])
def profile():
  username = session.get("user") or session.get("admin_user")
  if not username:
    flash("กรุณาเข้าสู่ระบบก่อนเข้าหน้าโปรไฟล์", "error")
    return redirect(url_for("login"))

  conn = get_db()
  cursor = conn.cursor()
  cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
  user_check = cursor.fetchone()
  if not user_check:
    conn.close()
    session.pop("user", None)
    session.pop("admin_user", None)
    flash("ไม่พบบัญชีผู้ใช้งานนี้ในระบบ กรุณาเข้าสู่ระบบใหม่อีกครั้ง", "error")
    return redirect(url_for("login"))

  if request.method == "POST":
    action = request.form.get("action")

    # อัปเดตข้อมูลส่วนตัว (ชื่อ-นามสกุล, เบอร์โทร, อีเมล, จังหวัด, และอัปโหลดรูปโปรไฟล์)
    if action == "update_profile":
      fullname = (request.form.get("fullname") or "").strip()
      phone = (request.form.get("phone") or "").strip()
      email = (request.form.get("email") or "").strip()
      province = (request.form.get("province") or "ขอนแก่น").strip()

      # ตรวจสอบการอัปโหลดรูปภาพโปรไฟล์ (Avatar)
      avatar_file = request.files.get("avatar")
      avatar_path = None
      if avatar_file and avatar_file.filename:
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        orig_filename = secure_filename(avatar_file.filename)
        ext = os.path.splitext(orig_filename)[1].lower()
        if ext in allowed_extensions:
          new_filename = f"avatar_{username}_{int(time.time())}{ext}"
          file_target = os.path.join(AVATAR_FOLDER, new_filename)
          avatar_file.save(file_target)
          avatar_path = f"/static/uploads/avatars/{new_filename}"
        else:
          flash("รูปแบบไฟล์รูปภาพไม่ถูกต้อง รองรับเฉพาะ .jpg, .jpeg, .png, .webp, .gif", "error")

      if avatar_path:
        cursor.execute(
            """
            UPDATE users SET fullname = ?, phone = ?, email = ?, province = ?, avatar = ?
            WHERE username = ?
            """,
            (fullname, phone, email, province, avatar_path, username),
        )
      else:
        cursor.execute(
            """
            UPDATE users SET fullname = ?, phone = ?, email = ?, province = ?
            WHERE username = ?
            """,
            (fullname, phone, email, province, username),
        )
      conn.commit()
      flash("บันทึกการแก้ไขข้อมูลโปรไฟล์เรียบร้อยแล้ว", "success")

    # นำรูปโปรไฟล์ออก (Remove Avatar)
    elif action == "remove_avatar":
      cursor.execute("UPDATE users SET avatar = '' WHERE username = ?", (username,))
      conn.commit()
      flash("นำรูปโปรไฟล์ออกเรียบร้อยแล้ว", "info")

    # เปลี่ยนรหัสผ่าน
    elif action == "change_password":
      current_pass = (request.form.get("current_password") or "").strip()
      new_pass = (request.form.get("new_password") or "").strip()
      confirm_pass = (request.form.get("confirm_password") or "").strip()

      cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
      user_row = cursor.fetchone()

      if not user_row or user_row["password"] != current_pass:
        flash("รหัสผ่านปัจจุบันไม่ถูกต้อง", "error")
      elif new_pass != confirm_pass:
        flash("รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน", "error")
      else:
        is_valid_new_pwd, new_pwd_err = validate_password_strength(new_pass)
        if not is_valid_new_pwd:
          flash(new_pwd_err, "error")
        else:
          cursor.execute(
              "UPDATE users SET password = ? WHERE username = ?",
              (new_pass, username),
          )
          conn.commit()
          flash("เปลี่ยนรหัสผ่านใหม่เรียบร้อยแล้ว", "success")

    # ลบบัญชีผู้ใช้งานถาวร (Delete Account)
    elif action == "delete_account":
      confirm_pass = (request.form.get("confirm_password") or "").strip()
      cursor.execute(
          "SELECT password FROM users WHERE username = ?", (username,)
      )
      user_row = cursor.fetchone()

      if not user_row or user_row["password"] != confirm_pass:
        flash(
            "รหัสผ่านไม่ถูกต้อง ไม่สามารถลบบัญชีได้ กรุณาตรวจสอบรหัสผ่านอีกครั้ง",
            "error",
        )
        conn.close()
        return redirect(url_for("profile"))

      # ลบข้อมูลการจองทั้งหมดของผู้ใช้นี้ และลบบัญชีผู้ใช้ออกจากฐานข้อมูล SQLite
      cursor.execute("DELETE FROM bookings WHERE username = ?", (username,))
      cursor.execute("DELETE FROM users WHERE username = ?", (username,))
      conn.commit()
      conn.close()

      # ล้างเซสชันและแจ้งเตือน
      if session.get("user") == username:
        session.pop("user", None)
      if session.get("admin_user") == username:
        session.pop("admin_user", None)
      flash(
          f"ลบบัญชีผู้ใช้งาน @{username} และข้อมูลที่เกี่ยวข้องทั้งหมดเรียบร้อยแล้ว",
          "info",
      )
      return redirect(url_for("hello_world"))

    conn.close()
    return redirect(url_for("profile"))

  conn.close()
  return render_template("profile.html", provinces=THAI_PROVINCES)


# 6. หน้าเลือกออพชั่นเสริมและส่งคำขอจองแพ็กเกจ (ชำระมัดจำ 5% ผ่านพร้อมเพย์ 0918303564)
@app.route("/book/<package_id>", methods=["GET", "POST"])
def book_package(package_id):
  booking_username = session.get("user") or session.get("admin_user")
  if not booking_username:
    flash("กรุณาเข้าสู่ระบบก่อนทำการจองอีเว้นท์", "error")
    return redirect(url_for("login"))

  pkg = EVENT_PACKAGES.get(package_id)
  if not pkg:
    flash("ไม่พบแพ็กเกจที่คุณเลือก", "error")
    return redirect(url_for("hello_world"))

  conn = get_db()
  cursor = conn.cursor()
  # ดึงรายการวันที่ที่ได้รับการอนุมัติแล้ว (คิวเต็ม) ทั้งหมด เพื่อนำไปปิดการจอง
  cursor.execute("SELECT event_date FROM bookings WHERE status = 'confirmed'")
  confirmed_dates = [r["event_date"] for r in cursor.fetchall()]

  # คำนวณยอดมัดจำเริ่มต้น (สำหรับแสดงผลหน้าเว็บ)
  if pkg["id"] == "test":
    base_deposit = 1.0  # แพ็กเกจทดสอบ โอนมัดจำเพียง 1 บาท
  else:
    base_deposit = max(1.0, round(pkg["price"] * 0.05, 2))

  promptpay_png_b64 = generate_promptpay_png_b64(PROMPTPAY_NUMBER, base_deposit)
  promptpay_svg = generate_promptpay_svg(PROMPTPAY_NUMBER, base_deposit)

  if request.method == "POST":
    options = request.form.getlist("options")
    event_date = (request.form.get("event_date") or "").strip()
    event_time = (request.form.get("event_time") or "08:00 - 17:00 น. (เต็มวัน 8 ชม.)").strip()
    venue = (request.form.get("venue") or KHON_KAEN_VENUES[0]["name"]).strip()
    note = (request.form.get("note") or "").strip()

    if not event_date:
      conn.close()
      flash("กรุณาเลือกวันที่ต้องการจัดงานแข่งขัน", "error")
      return render_template(
          "book.html",
          package=pkg,
          confirmed_dates=confirmed_dates,
          venues=KHON_KAEN_VENUES,
          time_slots=EVENT_TIME_SLOTS,
          promptpay_png_b64=promptpay_png_b64,
          promptpay_svg=promptpay_svg,
          deposit_amount=base_deposit,
          promptpay_number=PROMPTPAY_NUMBER,
      )

    # ตรวจสอบว่าวันที่ดังกล่าวได้รับการอนุมัติไปแล้วหรือไม่ (ปิดการจองวันนั้นทันที)
    cursor.execute(
        "SELECT id FROM bookings WHERE event_date = ? AND status = 'confirmed'",
        (event_date,),
    )
    already_confirmed = cursor.fetchone()
    if already_confirmed:
      conn.close()
      flash(
          f"ขออภัย วันที่ {event_date} มีผู้จองและได้รับการอนุมัติแล้ว (คิวเต็ม) ไม่สามารถส่งคำขอซ้ำได้ กรุณาเลือกวันอื่น",
          "error",
      )
      return render_template(
          "book.html",
          package=pkg,
          confirmed_dates=confirmed_dates,
          venues=KHON_KAEN_VENUES,
          time_slots=EVENT_TIME_SLOTS,
          promptpay_png_b64=promptpay_png_b64,
          promptpay_svg=promptpay_svg,
          deposit_amount=base_deposit,
          promptpay_number=PROMPTPAY_NUMBER,
      )

    # คำนวณราคาออพชั่นเสริม
    extra_price = 0
    option_details = []
    for opt in options:
      if opt == "ref_extra":
        extra_price += 1500
        option_details.append("เพิ่มกรรมการเสริม (+1,500 บาท)")
      elif opt == "live_stream":
        extra_price += 3000
        option_details.append("ระบบ Live Streaming สด (+3,000 บาท)")
      elif opt == "water_snack":
        extra_price += 1000
        option_details.append("ชุดน้ำดื่มและอาหารว่าง (+1,000 บาท)")

    total_price = pkg["price"] + extra_price

    # คำนวณเงินมัดจำจริง 5% ของยอดรวม (หรือ 1 บาท สำหรับแพ็กเกจทดสอบ)
    if pkg["id"] == "test":
      deposit_amount = 1.0
    else:
      deposit_amount = max(1.0, round(total_price * 0.05, 2))

    # อัปโหลดสลิปหลักฐานการโอนเงิน (ถ้ามีแนบมา)
    slip_file = request.files.get("slip")
    slip_url = ""
    if slip_file and slip_file.filename:
      allowed_slip_exts = {".jpg", ".jpeg", ".png", ".webp"}
      ext = os.path.splitext(slip_file.filename)[1].lower()
      if ext in allowed_slip_exts:
        slip_filename = f"slip_{booking_username}_{int(time.time())}{ext}"
        slip_target = os.path.join(SLIP_FOLDER, slip_filename)
        slip_file.save(slip_target)
        slip_url = f"/static/uploads/slips/{slip_filename}"

    # สร้างเลขที่ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างเป็นทางการ (Tax Invoice No.)
    invoice_no = f"INV-{time.strftime('%Y%m%d')}-{random.randint(1000, 9999)}"

    # บันทึกข้อมูลลงฐานข้อมูล SQLite ตาราง bookings
    cursor.execute(
        """
        INSERT INTO bookings (
            username, package_id, package_name, base_price, options, extra_price, total_price,
            event_date, event_time, venue, deposit_amount, deposit_rate, payment_status,
            payment_method, slip_image, invoice_no, note, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.05, 'deposit_paid', 'promptpay_0918303564', ?, ?, ?, 'pending')
        """,
        (
            booking_username,
            pkg["id"],
            pkg["name"],
            pkg["price"],
            json.dumps(option_details, ensure_ascii=False),
            extra_price,
            total_price,
            event_date,
            event_time,
            venue,
            deposit_amount,
            slip_url,
            invoice_no,
            note,
        ),
    )
    booking_id = cursor.lastrowid

    # ดึงข้อมูลผู้ใช้งานสำหรับส่งอีเมลใบเสร็จ/ใบกำกับภาษี
    cursor.execute("SELECT * FROM users WHERE username = ?", (booking_username,))
    user_row = cursor.fetchone()
    conn.commit()
    conn.close()

    # สร้าง Dictionary สำหรับส่งอีเมลใบเสร็จ
    booking_dict = {
        "id": booking_id,
        "username": booking_username,
        "package_name": pkg["name"],
        "base_price": pkg["price"],
        "extra_price": extra_price,
        "total_price": total_price,
        "deposit_amount": deposit_amount,
        "invoice_no": invoice_no,
        "event_date": event_date,
        "event_time": event_time,
        "venue": venue,
        "options_list": option_details,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
    }

    # ผู้ใช้สามารถตรวจสอบและดาวน์โหลด/บันทึก PDF ใบเสร็จรับเงิน/ใบกำกับภาษีได้ทันทีผ่านระบบหน้าเว็บ
    # สำรองข้อมูลฐานข้อมูลอัตโนมัติทันที
    backup_db()

    flash(
        f"ชำระเงินมัดจำ ฿{deposit_amount:,.2f} ผ่านพร้อมเพย์ {PROMPTPAY_NUMBER} และส่งคำขอจองสำเร็จ! คุณสามารถตรวจสอบและดาวน์โหลด / บันทึกไฟล์ PDF ใบเสร็จรับเงิน ({invoice_no}) ได้ทันที",
        "success",
    )
    return redirect(url_for("view_invoice", booking_id=booking_id))

  conn.close()
  return render_template(
      "book.html",
      package=pkg,
      confirmed_dates=confirmed_dates,
      venues=KHON_KAEN_VENUES,
      time_slots=EVENT_TIME_SLOTS,
      promptpay_png_b64=promptpay_png_b64,
      promptpay_svg=promptpay_svg,
      deposit_amount=base_deposit,
      promptpay_number=PROMPTPAY_NUMBER,
  )


# 6.1 หน้าแสดงใบเสร็จรับเงิน / ใบกำกับภาษี (Tax Invoice & Receipt Web View & Print)
@app.route("/invoice/<int:booking_id>")
def view_invoice(booking_id):
  current_username = session.get("user") or session.get("admin_user")
  if not current_username:
    flash("กรุณาเข้าสู่ระบบก่อนดูใบเสร็จรับเงิน/ใบกำกับภาษี", "error")
    return redirect(url_for("login"))

  conn = get_db()
  cursor = conn.cursor()

  # ตรวจสอบสิทธิ์ผู้ดูแลระบบ (Admin)
  is_admin = False
  admin_name = session.get("admin_user")
  if admin_name:
    cursor.execute("SELECT role FROM users WHERE username = ?", (admin_name,))
    admin_row = cursor.fetchone()
    if admin_row and admin_row["role"] == "admin":
      is_admin = True
  if not is_admin:
    cursor.execute("SELECT role FROM users WHERE username = ?", (current_username,))
    u_role = cursor.fetchone()
    is_admin = u_role and u_role["role"] == "admin"

  if is_admin:
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
  else:
    cursor.execute(
        "SELECT * FROM bookings WHERE id = ? AND username = ?",
        (booking_id, current_username),
    )
  b = cursor.fetchone()

  if not b:
    conn.close()
    flash("ไม่พบข้อมูลใบเสร็จรับเงิน/ใบกำกับภาษีที่ระบุ", "error")
    return redirect(url_for("my_bookings"))

  cursor.execute("SELECT * FROM users WHERE username = ?", (b["username"],))
  booking_user = cursor.fetchone()
  conn.close()

  item = dict(b)
  try:
    item["options_list"] = json.loads(b["options"]) if b["options"] else []
  except Exception:
    item["options_list"] = []

  total_price = item.get("total_price", 0)
  deposit_amt = float(item.get("deposit_amount") or 1.0)
  subtotal_no_vat = round(total_price / 1.07, 2)
  vat_amount = round(total_price - subtotal_no_vat, 2)
  base_no_vat = round(item.get("base_price", 0) / 1.07, 2)
  extra_no_vat = round(item.get("extra_price", 0) / 1.07, 2)
  remaining_balance = max(0, total_price - deposit_amt)
  baht_total = baht_text(total_price)
  baht_deposit = baht_text(deposit_amt)
  baht_remaining = baht_text(remaining_balance)

  promptpay_png_b64 = generate_promptpay_png_b64(PROMPTPAY_NUMBER, deposit_amt)

  return render_template(
      "invoice.html",
      booking=item,
      user=dict(booking_user) if booking_user else {},
      subtotal_no_vat=subtotal_no_vat,
      vat_amount=vat_amount,
      base_no_vat=base_no_vat,
      extra_no_vat=extra_no_vat,
      remaining_balance=remaining_balance,
      baht_total=baht_total,
      baht_deposit=baht_deposit,
      baht_remaining=baht_remaining,
      promptpay_png_b64=promptpay_png_b64,
      promptpay_number=PROMPTPAY_NUMBER,
  )


# 7. หน้าดูประวัติการจองของผู้ใช้ (My Bookings) & ยกเลิกการจอง
@app.route("/my-bookings", methods=["GET", "POST"])
def my_bookings():
  current_user = session.get("user") or session.get("admin_user")
  if not current_user:
    flash("กรุณาเข้าสู่ระบบก่อนดูประวัติการจอง", "error")
    return redirect(url_for("login"))

  conn = get_db()
  cursor = conn.cursor()

  if request.method == "POST":
    action = request.form.get("action")
    booking_id = request.form.get("booking_id")

    # ยกเลิกคำขอจอง (Cancel Booking)
    if action == "cancel_booking":
      cursor.execute(
          "SELECT * FROM bookings WHERE id = ? AND username = ?",
          (booking_id, current_user),
      )
      booking = cursor.fetchone()
      if booking:
        cursor.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ?",
            (booking_id,),
        )
        conn.commit()
        flash(
            f"ยกเลิกคำขอจอง #{booking_id} เรียบร้อยแล้ว"
            " (วันดังกล่าวถูกปลดล็อกให้ผู้อื่นจองได้แล้ว)",
            "info",
        )
      else:
        flash("ไม่พบรายการจองดังกล่าว หรือคุณไม่มีสิทธิ์ยกเลิก", "error")

    # ลบประวัติการจองออกจากรายการ (Delete Booking Record)
    elif action == "delete_booking":
      cursor.execute(
          "SELECT * FROM bookings WHERE id = ? AND username = ?",
          (booking_id, current_user),
      )
      booking = cursor.fetchone()
      if booking:
        cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        flash(f"ลบประวัติรายการจอง #{booking_id} เรียบร้อยแล้ว", "info")
      else:
        flash("ไม่พบรายการจองดังกล่าว", "error")

    conn.close()
    return redirect(url_for("my_bookings"))

  cursor.execute(
      "SELECT * FROM bookings WHERE username = ? ORDER BY id DESC",
      (current_user,),
  )
  raw_bookings = cursor.fetchall()
  conn.close()

  # แปลง options JSON เป็น list เพื่อแสดงผลใน template
  processed_bookings = []
  for b in raw_bookings:
    item = dict(b)
    try:
      item["options_list"] = json.loads(b["options"]) if b["options"] else []
    except Exception:
      item["options_list"] = []
    processed_bookings.append(item)

  return render_template("my_bookings.html", bookings=processed_bookings)


# 7.1 อัปโหลดหรือแนบสลิปหลักฐานการโอนเงินเพิ่มเติม (Upload/Attach Payment Slip)
@app.route("/upload-slip/<int:booking_id>", methods=["POST"])
def upload_booking_slip(booking_id):
  current_user = session.get("user") or session.get("admin_user")
  if not current_user:
    flash("กรุณาเข้าสู่ระบบก่อน", "error")
    return redirect(url_for("login"))

  conn = get_db()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT * FROM bookings WHERE id = ? AND username = ?",
      (booking_id, current_user),
  )
  booking = cursor.fetchone()

  if not booking:
    conn.close()
    flash("ไม่พบรายการจองดังกล่าว หรือคุณไม่มีสิทธิ์เข้าถึง", "error")
    return redirect(url_for("my_bookings"))

  slip_file = request.files.get("slip")
  if slip_file and slip_file.filename:
    allowed_slip_exts = {".jpg", ".jpeg", ".png", ".webp"}
    ext = os.path.splitext(slip_file.filename)[1].lower()
    if ext in allowed_slip_exts:
      slip_filename = f"slip_{current_user}_{int(time.time())}{ext}"
      slip_target = os.path.join(SLIP_FOLDER, slip_filename)
      slip_file.save(slip_target)
      slip_url = f"/static/uploads/slips/{slip_filename}"
      cursor.execute(
          """
          UPDATE bookings 
          SET slip_image = ?, payment_status = 'deposit_paid'
          WHERE id = ?
          """,
          (slip_url, booking_id),
      )
      conn.commit()
      backup_db()
      flash(
          f"แนบสลิปหลักฐานการโอนเงินสำหรับคิว #{booking_id} เรียบร้อยแล้ว",
          "success",
      )
    else:
      flash(
          "รูปแบบไฟล์ไม่ถูกต้อง กรุณาอัปโหลดไฟล์รูปภาพ (.jpg, .jpeg, .png,"
          " .webp)",
          "error",
      )
  else:
    flash("กรุณาเลือกไฟล์ภาพสลิปหลักฐานการโอนเงิน", "error")

  conn.close()
  return redirect(url_for("my_bookings"))


# ==========================================
# ADMIN SYSTEM (ระบบจัดการแยกเฉพาะสำหรับแอดมิน)
# ==========================================

# 8. หน้าล็อกอินเฉพาะผู้ดูแลระบบ (Admin Login - ล็อกอินด้วย Username & Password ได้ทันที)
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
  init_db()  # กู้คืนบัญชีแอดมินเริ่มต้นอัตโนมัติหากฐานข้อมูลถูกล้าง
  # หากมีเซสชันแอดมินอยู่แล้ว ให้พาไปหน้าคอนโซลทันที
  admin_username = session.get("admin_user")
  if not admin_username and session.get("user"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE username = ?", (session["user"],))
    u_row = cursor.fetchone()
    conn.close()
    if u_row and u_row["role"] == "admin":
      admin_username = session["user"]
      session["admin_user"] = admin_username

  if admin_username:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE username = ?", (admin_username,))
    user_row = cursor.fetchone()
    conn.close()
    if user_row and user_row["role"] == "admin":
      return redirect(url_for("admin_bookings"))

  if request.method == "POST":
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password),
    )
    user = cursor.fetchone()
    conn.close()

    if user:
      if user["role"] == "admin":
        session["admin_user"] = user["username"]
        # หากยังไม่มีสมาชิกล็อกอินอยู่ใน session["user"] ให้เชื่อมโยงไว้ด้วยเพื่อความสะดวก
        if "user" not in session:
          session["user"] = user["username"]
        flash(
            f"ยินดีต้อนรับผู้ดูแลระบบ {user['fullname'] or user['username']} เข้าสู่ระบบสำเร็จ",
            "success",
        )
        return redirect(url_for("admin_bookings"))
      else:
        flash("บัญชีนี้ไม่มีสิทธิ์ผู้ดูแลระบบ (Admin Only) กรุณาใช้บัญชีแอดมิน", "error")
    else:
      flash("ชื่อผู้ใช้หรือรหัสผ่านผู้ดูแลระบบไม่ถูกต้อง", "error")

  return render_template("admin_login.html")


# ออกจากระบบสำหรับแอดมิน
@app.route("/admin/logout")
def admin_logout():
  admin_name = session.pop("admin_user", None)
  # หาก session user เป็นแอดมินคนเดียวกัน ให้ล้างด้วย
  if admin_name and session.get("user") == admin_name:
    session.pop("user", None)
  flash("ออกจากระบบผู้ดูแลระบบเรียบร้อยแล้ว", "info")
  return redirect(url_for("admin_login"))


# ทางลัดและเส้นทางแยกตามส่วนงานของผู้ดูแลระบบ (Admin Console Routes)
@app.route("/admin")
@app.route("/admin/dashboard", methods=["GET", "POST"])
@app.route("/admin/workflow", methods=["GET", "POST"])
@app.route("/admin/users", methods=["GET", "POST"])
@app.route("/admin/database", methods=["GET", "POST"])
@app.route("/admin/bookings", methods=["GET", "POST"])
def admin_bookings():
  admin_username = session.get("admin_user")
  if not admin_username and session.get("user"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE username = ?", (session["user"],))
    u_row = cursor.fetchone()
    conn.close()
    if u_row and u_row["role"] == "admin":
      admin_username = session["user"]
      session["admin_user"] = admin_username

  if not admin_username:
    flash("กรุณาเข้าสู่ระบบด้วยบัญชีผู้ดูแลระบบ (Admin) ก่อนเข้าใช้งาน", "error")
    return redirect(url_for("admin_login"))

  username = admin_username
  conn = get_db()
  cursor = conn.cursor()
  cursor.execute("SELECT role FROM users WHERE username = ?", (username,))
  user_row = cursor.fetchone()

  # ตรวจสอบสิทธิ์ Admin
  if not user_row or user_row["role"] != "admin":
    conn.close()
    session.pop("admin_user", None)
    flash(
        "ขออภัย หน้าดังกล่าวสงวนไว้สำหรับผู้ดูแลระบบ (Admin) เท่านั้น", "error"
    )
    return redirect(url_for("admin_login"))

  # ตรวจสอบมุมมองหน้าปัจจุบัน (Active View: dashboard, workflow, users, database)
  active_view = request.args.get("view")
  if not active_view:
    if request.path == "/admin/workflow":
      active_view = "workflow"
    elif request.path == "/admin/users":
      active_view = "users"
    elif request.path == "/admin/database":
      active_view = "database"
    elif request.path in ["/admin", "/admin/dashboard"]:
      active_view = "dashboard"
    else:
      active_view = "dashboard"

  # ดำเนินการอัปเดตสถานะ, ยกเลิก หรือลบรายการ
  if request.method == "POST":
    action = request.form.get("action")
    booking_id = request.form.get("booking_id")

    if action == "update_status":
      new_status = request.form.get("new_status")
      admin_note = (request.form.get("admin_note") or "").strip()

      cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
      target_b = cursor.fetchone()

      if not target_b:
        conn.close()
        flash("ไม่พบรายการจองดังกล่าว", "error")
        return redirect(url_for("admin_bookings", view="workflow"))

      # ตรวจสอบ: หากคิวนี้ได้รับการอนุมัติหรือปฏิเสธไปแล้ว ไม่อนุญาตให้กดอนุมัติหรือปฏิเสธซ้ำได้
      if target_b["status"] in ["confirmed", "rejected"]:
        conn.close()
        status_th = "อนุมัติแล้ว" if target_b["status"] == "confirmed" else "ปฏิเสธแล้ว"
        flash(
            f"รายการจอง #{booking_id} ได้รับการตรวจสอบและบันทึกผลเป็น '{status_th}' ไปแล้ว ไม่สามารถกดอนุมัติหรือปฏิเสธซ้ำได้",
            "error",
        )
        return redirect(url_for("admin_bookings", view="workflow"))

      target_date = target_b["event_date"]

      if new_status == "confirmed":
        # ตรวจสอบว่าวันที่นี้มีคิวอื่นได้รับการอนุมัติไปแล้วหรือไม่ ป้องกันการอนุมัติซ้ำซ้อน
        cursor.execute(
            "SELECT id, username FROM bookings WHERE event_date = ? AND status = 'confirmed' AND id != ?",
            (target_date, booking_id),
        )
        conflicting = cursor.fetchone()
        if conflicting:
          conn.close()
          flash(
              f"ไม่สามารถอนุมัติได้: วันที่ {target_date} มีคิว #{conflicting['id']} (@{conflicting['username']}) ได้รับการอนุมัติไปแล้ว (คิวเต็ม)",
              "error",
          )
          return redirect(url_for("admin_bookings", view="workflow"))

        # อนุมัติรายการนี้
        cursor.execute(
            """
            UPDATE bookings SET status = 'confirmed', admin_note = ?
            WHERE id = ?
            """,
            (admin_note, booking_id),
        )

        # ปิดการจองและปฏิเสธคำขออื่นในวันเดียวกันที่ยังรออนุมัติ (Pending) ทั้งหมดอัตโนมัติ
        auto_reject_note = "คิวเต็ม เนื่องจากมีคิวแข่งขันที่จองมาก่อนได้รับการอนุมัติเรียบร้อยแล้ว"
        cursor.execute(
            """
            UPDATE bookings 
            SET status = 'rejected', admin_note = ?
            WHERE event_date = ? AND status = 'pending' AND id != ?
            """,
            (auto_reject_note, target_date, booking_id),
        )
        auto_rejected_count = cursor.rowcount
        conn.commit()

        if auto_rejected_count > 0:
          flash(
              f"อนุมัติคิว #{booking_id} สำหรับวันที่ {target_date} สำเร็จ (ระบบได้ปิดการจองและปฏิเสธคิวซ้ำอื่นอีก {auto_rejected_count} รายการให้อัตโนมัติแล้ว)",
              "success",
          )
        else:
          flash(f"อนุมัติคิว #{booking_id} สำหรับวันที่ {target_date} สำเร็จ (คิววันนี้ถูกปิดการจองแล้ว)", "success")

      else:
        cursor.execute(
            """
            UPDATE bookings SET status = ?, admin_note = ?
            WHERE id = ?
            """,
            (new_status, admin_note, booking_id),
        )
        conn.commit()
        status_text = "ปฏิเสธ (คิวเต็ม)" if new_status == "rejected" else new_status
        flash(f"อัปเดตรายการจอง #{booking_id} เป็น {status_text} สำเร็จ", "info")

    elif action == "cancel_booking":
      cursor.execute(
          "UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,)
      )
      conn.commit()
      flash(f"ยกเลิกรายการจอง #{booking_id} เรียบร้อยแล้ว", "info")

    elif action == "delete_booking":
      cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
      conn.commit()
      flash(f"ลบรายการจอง #{booking_id} เรียบร้อยแล้ว", "info")

    elif action == "delete_user":
      target_username = (request.form.get("target_username") or "").strip()
      if target_username and target_username != username:
        cursor.execute(
            "DELETE FROM bookings WHERE username = ?", (target_username,)
        )
        cursor.execute("DELETE FROM users WHERE username = ?", (target_username,))
        conn.commit()
        flash(f"ลบบัญชีผู้ใช้ @{target_username} และข้อมูลการจองออกจากระบบเรียบร้อยแล้ว", "info")
      elif target_username == username:
        flash("ไม่สามารถลบบัญชีของตนเองจากหน้านี้ได้ กรุณาไปที่หน้าโปรไฟล์ส่วนตัว", "error")

    target_view = "users" if action == "delete_user" else "workflow"
    conn.close()
    return redirect(url_for("admin_bookings", view=target_view))

  # ดึงข้อมูลการจองทั้งหมด พร้อมข้อมูลติดต่อและจังหวัดของผู้จอง
  cursor.execute("""
    SELECT b.*, u.email as user_email, u.phone as user_phone, u.fullname as user_fullname, u.province as user_province, u.avatar as user_avatar
    FROM bookings b
    LEFT JOIN users u ON b.username = u.username
    ORDER BY b.id DESC
    """)
  raw_bookings = cursor.fetchall()

  # สรุปสถิติพื้นฐาน
  total_bookings = len(raw_bookings)
  pending_count = sum(1 for b in raw_bookings if b["status"] == "pending")
  confirmed_count = sum(1 for b in raw_bookings if b["status"] == "confirmed")
  rejected_count = sum(1 for b in raw_bookings if b["status"] == "rejected")
  cancelled_count = sum(1 for b in raw_bookings if b["status"] == "cancelled")

  stats = {
      "total": total_bookings,
      "pending": pending_count,
      "confirmed": confirmed_count,
      "rejected": rejected_count,
      "cancelled": cancelled_count,
  }

  # สรุปผลสัมฤทธิ์และสถิติเชิงลึกสำหรับ Executive Dashboard
  total_confirmed_revenue = sum(b["total_price"] for b in raw_bookings if b["status"] == "confirmed")
  total_deposit_collected = sum(b["deposit_amount"] or 0 for b in raw_bookings if b["status"] == "confirmed")
  total_pending_pipeline = sum(b["total_price"] for b in raw_bookings if b["status"] == "pending")
  total_pending_deposit = sum(b["deposit_amount"] or 0 for b in raw_bookings if b["status"] == "pending")

  # คำนวณอัตราความสำเร็จในการปิดคิวงาน (% Success Conversion Rate)
  active_decided = confirmed_count + rejected_count
  success_rate = round((confirmed_count / total_bookings * 100), 1) if total_bookings > 0 else 0.0
  inspection_rate = round((active_decided / total_bookings * 100), 1) if total_bookings > 0 else 0.0

  # สถิติแยกตามแพ็กเกจการแข่งขัน
  package_counter = {}
  for b in raw_bookings:
    p_name = b["package_name"] or "ทั่วไป"
    if p_name not in package_counter:
      package_counter[p_name] = {"count": 0, "confirmed": 0, "revenue": 0}
    package_counter[p_name]["count"] += 1
    if b["status"] == "confirmed":
      package_counter[p_name]["confirmed"] += 1
      package_counter[p_name]["revenue"] += b["total_price"]
  package_stats = sorted(package_counter.items(), key=lambda x: x[1]["count"], reverse=True)

  # สถิติแยกตามสนามแข่งขันในขอนแก่น
  venue_counter = {}
  for b in raw_bookings:
    v_raw = b["venue"] or "ศูนย์กีฬา มข."
    v_clean = v_raw.split("(")[0].strip() if "(" in v_raw else v_raw
    if v_clean not in venue_counter:
      venue_counter[v_clean] = {"count": 0, "confirmed": 0}
    venue_counter[v_clean]["count"] += 1
    if b["status"] == "confirmed":
      venue_counter[v_clean]["confirmed"] += 1
  venue_stats = sorted(venue_counter.items(), key=lambda x: x[1]["count"], reverse=True)

  # สถิติจังหวัดของผู้จอง
  province_counter = {}
  for b in raw_bookings:
    p_name = b["user_province"] or "ขอนแก่น"
    province_counter[p_name] = province_counter.get(p_name, 0) + 1
  province_stats = sorted(province_counter.items(), key=lambda x: x[1], reverse=True)

  dashboard = {
      "total_bookings": total_bookings,
      "pending_count": pending_count,
      "confirmed_count": confirmed_count,
      "rejected_count": rejected_count,
      "cancelled_count": cancelled_count,
      "total_confirmed_revenue": total_confirmed_revenue,
      "total_deposit_collected": total_deposit_collected,
      "total_pending_pipeline": total_pending_pipeline,
      "total_pending_deposit": total_pending_deposit,
      "success_rate": success_rate,
      "inspection_rate": inspection_rate,
      "package_stats": package_stats,
      "venue_stats": venue_stats,
      "province_stats": province_stats,
  }

  # ตรวจจับวันที่ที่ได้รับการอนุมัติแล้ว (คิวเต็มแล้ว) เพื่อป้องกันการอนุมัติซ้อนในหน้า UI
  confirmed_slots = {b["event_date"]: b["id"] for b in raw_bookings if b["status"] == "confirmed"}

  processed_bookings = []
  for b in raw_bookings:
    item = dict(b)
    try:
      item["options_list"] = json.loads(b["options"]) if b["options"] else []
    except Exception:
      item["options_list"] = []
    # เช็กว่าวันที่นี้มีคิวอื่นที่ได้รับการอนุมัติไปแล้วหรือไม่
    has_conflict = (b["event_date"] in confirmed_slots and confirmed_slots[b["event_date"]] != b["id"])
    item["has_date_conflict"] = has_conflict
    item["confirmed_by_id"] = confirmed_slots.get(b["event_date"])
    processed_bookings.append(item)

  # แบ่งกลุ่มรายการจองตามลำดับปฏิบัติงาน (Operational Workflow Stages)
  pending_bookings = [b for b in processed_bookings if b["status"] == "pending"]
  confirmed_bookings = [b for b in processed_bookings if b["status"] == "confirmed"]
  rejected_cancelled_bookings = [b for b in processed_bookings if b["status"] in ["rejected", "cancelled"]]

  # ดึงรายชื่อบัญชีผู้ใช้ที่ลงทะเบียนทั้งหมด (สำหรับจัดการ/ลบบัญชีในหน้า Admin)
  cursor.execute("""
    SELECT id, username, fullname, email, phone, province, avatar, role, created_at,
           (SELECT COUNT(*) FROM bookings WHERE username = users.username) as booking_count
    FROM users
    ORDER BY id DESC
  """)
  all_users = cursor.fetchall()

  conn.close()
  return render_template(
      "admin_bookings.html",
      active_view=active_view,
      bookings=processed_bookings,
      pending_bookings=pending_bookings,
      confirmed_bookings=confirmed_bookings,
      rejected_cancelled_bookings=rejected_cancelled_bookings,
      stats=stats,
      dashboard=dashboard,
      users=all_users,
  )


# API สร้าง PromptPay QR Code แบบไดนามิกตามยอดเงิน (ฝังราคาใน QR Code อัตโนมัติ)
@app.route("/api/promptpay-qr")
def api_promptpay_qr():
  try:
    amount = float(request.args.get("amount", 1.0))
  except (ValueError, TypeError):
    amount = 1.0
  png_b64 = generate_promptpay_png_b64(PROMPTPAY_NUMBER, amount)
  svg = generate_promptpay_svg(PROMPTPAY_NUMBER, amount)
  payload = generate_promptpay_payload(PROMPTPAY_NUMBER, amount)
  return jsonify({
      "status": "success",
      "phone": PROMPTPAY_NUMBER,
      "amount": amount,
      "amount_formatted": f"{amount:,.2f}",
      "qr_image": png_b64,
      "svg": svg,
      "payload": payload,
  })


# แอดมิน: สั่งสำรองข้อมูลฐานข้อมูล SQLite ด้วยตนเอง (Manual Backup)
@app.route("/admin/backup", methods=["POST"])
def admin_backup_action():
  admin_username = session.get("admin_user")
  if not admin_username and session.get("user"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE username = ?", (session["user"],))
    u_row = cursor.fetchone()
    conn.close()
    if u_row and u_row["role"] == "admin":
      admin_username = session["user"]
      session["admin_user"] = admin_username

  if not admin_username:
    flash("กรุณาเข้าสู่ระบบด้วยบัญชีผู้ดูแลระบบก่อน", "error")
    return redirect(url_for("admin_login"))

  username = admin_username
  conn = get_db()
  cursor = conn.cursor()
  cursor.execute("SELECT role FROM users WHERE username = ?", (username,))
  user_row = cursor.fetchone()
  conn.close()
  if not user_row or user_row["role"] != "admin":
    flash("ขออภัย หน้าดังกล่าวสงวนไว้สำหรับผู้ดูแลระบบเท่านั้น", "error")
    return redirect(url_for("admin_login"))

  ok, msg = backup_db()
  if ok:
    flash(
        "สำรองข้อมูลฐานข้อมูลเรียบร้อยแล้ว (ไฟล์ database_backup.sql และ"
        " database_backup.db)",
        "success",
    )
  else:
    flash(f"เกิดข้อผิดพลาดในการสำรองข้อมูล: {msg}", "error")
  return redirect(url_for("admin_bookings", view="database"))


# แอดมิน: สั่งกู้คืนฐานข้อมูลจากไฟล์สำรองด้วยตนเอง (Manual Restore)
@app.route("/admin/restore", methods=["POST"])
def admin_restore_action():
  admin_username = session.get("admin_user")
  if not admin_username and session.get("user"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE username = ?", (session["user"],))
    u_row = cursor.fetchone()
    conn.close()
    if u_row and u_row["role"] == "admin":
      admin_username = session["user"]
      session["admin_user"] = admin_username

  if not admin_username:
    flash("กรุณาเข้าสู่ระบบด้วยบัญชีผู้ดูแลระบบก่อน", "error")
    return redirect(url_for("admin_login"))

  username = admin_username
  conn = get_db()
  cursor = conn.cursor()
  cursor.execute("SELECT role FROM users WHERE username = ?", (username,))
  user_row = cursor.fetchone()
  conn.close()
  if not user_row or user_row["role"] != "admin":
    flash("ขออภัย หน้าดังกล่าวสงวนไว้สำหรับผู้ดูแลระบบเท่านั้น", "error")
    return redirect(url_for("admin_login"))

  ok, msg = restore_db()
  if ok:
    flash(
        "กู้คืนข้อมูลฐานข้อมูลสำเร็จเรียบร้อยแล้ว ข้อมูลทั้งหมดพร้อมใช้งาน",
        "success",
    )
  else:
    flash(f"เกิดข้อผิดพลาดในการกู้คืนฐานข้อมูล: {msg}", "error")
  return redirect(url_for("admin_bookings", view="database"))


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port, debug=False)