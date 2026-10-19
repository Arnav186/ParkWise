from pathlib import Path
import json
import sys
import pdfplumber

sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"C:\Users\LENOVO\Desktop\V SEM")
pdf_path = root / "Vehicle_Parking_System_Project_Synopsis.pdf"
with pdfplumber.open(pdf_path) as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
print(json.dumps({"pages": len(pages), "text": "\n\n".join(pages)}, ensure_ascii=False))
