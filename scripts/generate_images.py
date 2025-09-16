from datetime import datetime
import os
import shutil

from src.common import tz_tr, today_slots, select_title, make_image

OUT_DIR = "out/daily"

def ensure_dir_clean(path: str):
    os.makedirs(path, exist_ok=True)
    # klasörü temizle
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if os.path.isfile(fp) or os.path.islink(fp):
            os.unlink(fp)
        elif os.path.isdir(fp):
            shutil.rmtree(fp)

def main():
    today = datetime.now(tz_tr())
    ensure_dir_clean(OUT_DIR)

    date_str = today.strftime("%Y-%m-%d")
    for dt in today_slots(today):
        title = select_title(dt)
        img_bytes = make_image(dt, title)
        fn = f"{date_str}_{dt.hour:02d}00.png"
        with open(os.path.join(OUT_DIR, fn), "wb") as f:
            f.write(img_bytes)
        print("✓", fn)

if __name__ == "__main__":
    main()
