BEGIN TRANSACTION;
CREATE TABLE bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        package_id TEXT NOT NULL,
        package_name TEXT NOT NULL,
        base_price INTEGER NOT NULL,
        options TEXT DEFAULT '',
        extra_price INTEGER DEFAULT 0,
        total_price INTEGER NOT NULL,
        event_date TEXT NOT NULL,
        note TEXT DEFAULT '',
        status TEXT DEFAULT 'pending', -- 'pending' (รอตรวจสอบ), 'confirmed' (ว่าง/อนุมัติ), 'rejected' (คิวเต็ม)
        admin_note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    , event_time TEXT DEFAULT '08:00 - 17:00 น. (เต็มวัน 8 ชม.)', venue TEXT DEFAULT 'ศูนย์กีฬาและสุขภาพ มหาวิทยาลัยขอนแก่น (KKU Sports Complex - โรงยิมเนเซียม 1 สนามหลักพื้นปาร์เก้)', deposit_amount REAL DEFAULT 0, deposit_rate REAL DEFAULT 0.05, payment_status TEXT DEFAULT 'deposit_paid', payment_method TEXT DEFAULT 'promptpay_0918303564', slip_image TEXT DEFAULT '', invoice_no TEXT DEFAULT '');
CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT NOT NULL,
        fullname TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        role TEXT DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    , avatar TEXT DEFAULT '', province TEXT DEFAULT 'ขอนแก่น');
INSERT INTO "users" VALUES(1,'admin','admin1234','basketballeventbooking@gmail.com','ผู้ดูแลระบบ (Admin)','081-234-5678','admin','2026-10-14 05:35:27','','ขอนแก่น');
INSERT INTO "users" VALUES(12,'Weslie','Wess_bt21','pongthitinu@gmail.com','Weslie','','user','2026-10-28 12:55:28','','ขอนแก่น');
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',12);
COMMIT;
