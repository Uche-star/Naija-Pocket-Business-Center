"""
Naija Pocket Business Center
Complete payment, saved-document and delivery API.

CANONICAL RULE:
- Document saved once as canonical file
- All downloads return that exact file
- No regeneration during download
"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
import json
import os
import re
import secrets
import sqlite3
import urllib.parse
import zipfile
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

APP_VERSION = "payment-product-canonical-v17-fixed-download"
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"
BACK_OFFICE_ADMIN_KEY = "NPBC-2026"
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "").strip()
BACK_OFFICE_DOWNLOAD_TOKEN_MINUTES = 60

app = FastAPI(title="Naija Pocket Business Center Payment API", version=APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()

def key_part(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value).casefold())

def business_key(service: Any, title: Any) -> str:
    return f"{key_part(service)}::{key_part(title)}"

def first(*values: Any) -> str:
    for v in values:
        if clean(v):
            return clean(v)
    return ""

def db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=30)
    c.row_factory = sqlite3.Row
    return c

def as_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None

def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)

def from_json(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = clean(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default

def safe_filename(value: Any, default: str = "document.docx") -> str:
    name = Path(clean(value) or default).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(".")
    if not name:
        name = default
    if not name.lower().endswith((".docx", ".pdf")):
        name += ".docx"
    return name

def safe_folder(value: Any, default: str = "document") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean(value) or default)
    name = re.sub(r"\s+", " ", name).strip(".")
    return name[:120] or default

# ===================== MARKDOWN CLEANER - COMPLETE =====================
# Handles: **bold**, __bold__, *italic*, _italic_, `code`, ```blocks```,
# # headings, > quotes, --- separators, [link](url), tables,
# - lists, * lists, + lists, 1. numbered lists
def _strip_md(text: str) -> str:
    if not text:
        return ""
    t = str(text)
    # code blocks first
    t = re.sub(r'```.*?```', '', t, flags=re.DOTALL)
    t = t.replace('`', '')
    # headings at line start
    t = re.sub(r'^\s{0,3}#{1,6}\s+', '', t, flags=re.MULTILINE)
    # horizontal rules
    t = re.sub(r'^\s*[-*_]{3,}\s*$', '', t, flags=re.MULTILINE)
    # blockquotes
    t = re.sub(r'^\s*>\s*', '', t, flags=re.MULTILINE)
    # bold / italic - remove markers keep text
    t = re.sub(r'\*\*(.*?)\*\*', r'\1', t)
    t = re.sub(r'__(.*?)__', r'\1', t)
    t = re.sub(r'\*(.*?)\*', r'\1', t)
    t = re.sub(r'_(.*?)_', r'\1', t)
    # links [text](url) -> text
    t = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', t)
    # unordered list markers: - item, * item, + item (only at line start)
    t = re.sub(r'^\s*[-*+]\s+', '', t, flags=re.MULTILINE)
    # ordered list markers: 1. item, 2. item (only at line start)
    t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
    # table pipe cleanup for remaining
    t = t.replace('|', ' ')
    # collapse multiple spaces from table cleanup
    t = re.sub(r' {2,}', ' ', t)
    return t.strip()

def normalize_pages(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return []
        parsed = from_json(txt)
        if isinstance(parsed, (list, dict)):
            return normalize_pages(parsed)
        return [txt]
    if isinstance(value, dict):
        for k in ("pages","page_text","document_pages","content","text"):
            if k in value:
                return normalize_pages(value[k])
        return [json.dumps(value, ensure_ascii=False)]
    if isinstance(value, list):
        res=[]
        for item in value:
            if isinstance(item, dict):
                txt=first(item.get("text"), item.get("content"), item.get("page_text"))
                if txt:
                    res.append(txt)
            elif clean(item):
                res.append(clean(item))
        return res
    return [clean(value)] if clean(value) else []

def normalize_payload(value: Any) -> dict:
    payload = value if isinstance(value, dict) else {"document_text": clean(value)}
    raw_pages = payload.get("pages") if payload.get("pages") is not None else payload.get("document_pages")
    page_list = normalize_pages(raw_pages)
    text = first(payload.get("document_text"), payload.get("documentText"), payload.get("text"), payload.get("content"))
    if not page_list and text:
        page_list=[text]
    if not text and page_list:
        text="\n\n".join(page_list)
    filename=safe_filename(first(payload.get("filename"), payload.get("document_filename"), payload.get("documentFilename")) or "document.docx")
    return {"pages": page_list, "document_text": text, "filename": filename, "document_version": first(payload.get("document_version"), payload.get("documentVersion"), payload.get("version")), "page_count": len(page_list)}

def clean_saved_document_payload(payload: dict) -> dict:
    normalized = normalize_payload(dict(payload or {}))
    cleaned_pages=[]
    for p in (normalized.get("pages") or []):
        page_lines=[]
        for raw_line in str(p).splitlines():
            # strip markdown per line to handle list markers correctly
            cl = _strip_md(raw_line)
            if not cl:
                continue
            if cl in {'---','***','___'}:
                continue
            # skip markdown table separator lines like |---|---|
            if re.match(r'^[\-\:\s]+$', cl):
                continue
            if cl:
                page_lines.append(cl)
        if page_lines:
            cleaned_pages.append("\n".join(page_lines))
    txt = _strip_md(normalized.get("document_text") or "")
    # rebuild text from cleaned pages if needed
    if not cleaned_pages and txt:
        cleaned_pages=[txt]
    if not txt and cleaned_pages:
        txt="\n\n".join(cleaned_pages)
    else:
        # re-derive txt from cleaned pages to ensure consistency
        txt = "\n\n".join(cleaned_pages) if cleaned_pages else txt
    normalized["pages"]=cleaned_pages
    normalized["document_text"]=txt
    normalized["page_count"]=len(cleaned_pages)
    return normalized

def init_databases() -> None:
    c=db(PRODUCT_DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS document_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business_key TEXT NOT NULL UNIQUE, service TEXT NOT NULL, document_title TEXT NOT NULL,
        customer_name TEXT, customer_email TEXT, customer_phone TEXT, customer_id TEXT, amount TEXT, currency TEXT DEFAULT 'NGN', job_id TEXT,
        document_payload TEXT, document_text TEXT, document_filename TEXT, document_version TEXT, document_pages INTEGER DEFAULT 0,
        document_saved_path TEXT, document_saved_at TEXT, download_unlocked INTEGER DEFAULT 0, download_count INTEGER DEFAULT 0,
        downloaded_at TEXT, activated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, notes TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS delivery_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business_key TEXT NOT NULL, service TEXT, document_title TEXT, channel TEXT NOT NULL,
        status TEXT NOT NULL, recipient TEXT, detail TEXT, created_at TEXT NOT NULL, details TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS back_office_download_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE, business_key TEXT NOT NULL, service TEXT NOT NULL,
        document_title TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, download_count INTEGER DEFAULT 0, last_downloaded_at TEXT)""")
    c.commit(); c.close()
    c=db(PAYMENT_DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS payment_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business_key TEXT NOT NULL UNIQUE, service TEXT NOT NULL, document_title TEXT NOT NULL,
        customer_name TEXT, customer_email TEXT, customer_phone TEXT, customer_id TEXT, amount TEXT, currency TEXT DEFAULT 'NGN',
        payment_method TEXT, payment_status TEXT DEFAULT 'pending', document_version TEXT, document_filename TEXT, document_pages INTEGER DEFAULT 0,
        document_text TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, reported_at TEXT, payment_reported_at TEXT,
        verified_at TEXT, payment_completed_at TEXT, completed_at TEXT, rejected_at TEXT, notes TEXT)""")
    c.commit(); c.close()

@app.on_event("startup")
def startup() -> None:
    init_databases()

def get_product(service: str, title: str) -> Optional[dict]:
    c=db(PRODUCT_DB_PATH)
    row=c.execute("SELECT * FROM document_products WHERE business_key=?", (business_key(service,title),)).fetchone()
    c.close()
    return as_dict(row)

def get_product_by_business_key(bk: str) -> Optional[dict]:
    c=db(PRODUCT_DB_PATH)
    row=c.execute("SELECT * FROM document_products WHERE business_key=?", (clean(bk),)).fetchone()
    c.close()
    return as_dict(row)

def get_payment(bk: str) -> Optional[dict]:
    c=db(PAYMENT_DB_PATH)
    row=c.execute("SELECT * FROM payment_orders WHERE business_key=?", (clean(bk),)).fetchone()
    c.close()
    return as_dict(row)

def existing_saved_file(product: Optional[dict]) -> Optional[Path]:
    if not product:
        return None
    raw=clean(product.get("document_saved_path"))
    if not raw:
        return None
    p=Path(raw)
    if not p.is_absolute():
        p=BASE_DIR / p
    return p if p.exists() and p.is_file() else None

def _docx_p(text: str) -> str:
    raw=str(text).replace("\r","").strip()
    if not raw:
        return ""
    # heading already stripped, but keep detection for style
    is_heading = bool(re.match(r'^\s*#{1,6}\s+', text))
    # bold detection after cleaning - we already removed ** but keep bold runs for legacy
    runs=[]
    # simple: whole paragraph as one run (cleaned already)
    runs.append(f'<w:r><w:t xml:space="preserve">{xml_escape(raw)}</w:t></w:r>')
    ppr='<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if is_heading else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"

def make_docx(path: Path, title: str, page_list: list[str]) -> Path:
    """
    Create valid DOCX package.
    Signature: make_docx(path: Path, title: str, page_list: list[str])
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use cleaned payload - caller already cleaned, but ensure again
    payload_clean = clean_saved_document_payload({"pages": page_list})
    pages = payload_clean.get("pages") or []
    if not pages:
        # fallback to title to avoid empty doc
        pages=[clean(title) or "Document"]
    body=[]
    for i, page in enumerate(pages):
        if i:
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        for line in str(page).splitlines():
            if line.strip():
                body.append(_docx_p(line))
    if not body:
        body.append(_docx_p(clean(title) or "Document"))
    document_xml=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{"".join(body)}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    content_types='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    root_rels='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", document_xml)
    return path

def save_exact_snapshot(service: str, title: str, payload: dict, existing_product: Optional[dict]=None) -> Path:
    """
    Save exact canonical snapshot once. Never overwrite existing.
    """
    # 1. If file already exists for this product, preserve it
    existing_file = existing_saved_file(existing_product or get_product(service, title))
    if existing_file is not None:
        return existing_file
    # 2. Also check filesystem for legacy file with same service/title
    sc=clean(service); tc=clean(title)
    folder = DOWNLOAD_DIR / safe_folder(sc) / safe_folder(tc)
    if folder.exists():
        candidates=[x for x in folder.iterdir() if x.is_file() and x.suffix.lower() in {".docx",".pdf"}]
        if candidates:
            candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            return candidates[0]
    # 3. Create new cleaned canonical file
    normalized = clean_saved_document_payload(payload)
    if not normalized["pages"] and not normalized["document_text"]:
        raise HTTPException(status_code=400, detail="SAVED_DOCUMENT_CONTENT_MISSING")
    folder.mkdir(parents=True, exist_ok=True)
    fname = safe_filename(normalized.get("filename") or tc)
    if fname.lower().endswith(".pdf"):
        fname = Path(fname).stem + ".docx"
    target = folder / fname
    if target.exists() and target.is_file():
        return target
    return make_docx(target, tc, normalized["pages"] or [normalized["document_text"]])

def store_saved_path(business_key_value: str, saved_path: Path) -> None:
    ts=now_iso()
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("UPDATE document_products SET document_saved_path=?, document_saved_at=COALESCE(document_saved_at,?), updated_at=? WHERE business_key=?", (str(saved_path), ts, ts, clean(business_key_value)))

def extract_document_text(payload: dict) -> str:
    return clean_saved_document_payload(payload).get("document_text", "")

def upsert_product(*, service: str, title: str, payload: dict, customer_name: str="", customer_email: str="", customer_phone: str="", amount: str="", currency: str="NGN", job_id: str="") -> dict:
    sc=clean(service); tc=clean(title)
    if not sc: raise HTTPException(400, "SERVICE_REQUIRED")
    if not tc: raise HTTPException(400, "DOCUMENT_TITLE_REQUIRED")
    bk=business_key(sc,tc)
    normalized=clean_saved_document_payload(payload)
    with db(PRODUCT_DB_PATH) as conn:
        existing=conn.execute("SELECT * FROM document_products WHERE business_key=? LIMIT 1", (bk,)).fetchone()
        ts=now_iso()
        if existing:
            conn.execute("""UPDATE document_products SET customer_name=CASE WHEN?<>'' THEN? ELSE customer_name END,
            customer_email=CASE WHEN?<>'' THEN? ELSE customer_email END, customer_phone=CASE WHEN?<>'' THEN? ELSE customer_phone END,
            amount=CASE WHEN?<>'' THEN? ELSE amount END, currency=CASE WHEN?<>'' THEN? ELSE currency END,
            job_id=CASE WHEN?<>'' THEN? ELSE job_id END,
            document_payload=CASE WHEN document_saved_path IS NULL OR document_saved_path='' THEN? ELSE document_payload END,
            document_text=CASE WHEN document_saved_path IS NULL OR document_saved_path='' THEN? ELSE document_text END,
            updated_at=? WHERE business_key=?""",
            (clean(customer_name),clean(customer_name),clean(customer_email),clean(customer_email),clean(customer_phone),clean(customer_phone),
             clean(amount),clean(amount),clean(currency),clean(currency),clean(job_id),clean(job_id),
             to_json(normalized), extract_document_text(normalized), ts, bk))
        else:
            conn.execute("""INSERT INTO document_products (business_key,service,document_title,customer_name,customer_email,customer_phone,amount,currency,job_id,document_payload,document_text,document_saved_path,document_saved_at,download_unlocked,download_count,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'',NULL,0,0,?,?)""",
            (bk,sc,tc,clean(customer_name),clean(customer_email),clean(customer_phone),clean(amount),clean(currency) or "NGN",clean(job_id),to_json(normalized),extract_document_text(normalized),ts,ts))
    prod=get_product(sc,tc)
    if not prod: raise HTTPException(500, "PRODUCT_SAVE_FAILED")
    return prod

def ensure_payment_record(product: dict) -> dict:
    bk=clean(product.get("business_key"))
    if not bk: raise HTTPException(400, "BUSINESS_KEY_REQUIRED")
    existing=get_payment(bk)
    if existing: return existing
    ts=now_iso()
    with db(PAYMENT_DB_PATH) as conn:
        conn.execute("""INSERT INTO payment_orders (business_key,service,document_title,customer_name,customer_email,customer_phone,amount,currency,document_text,payment_status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (bk,clean(product.get("service")),clean(product.get("document_title")),clean(product.get("customer_name")),clean(product.get("customer_email")),
         clean(product.get("customer_phone")),clean(product.get("amount")),clean(product.get("currency") or "NGN"),clean(product.get("document_text")),ts,ts))
    pay=get_payment(bk)
    if not pay: raise HTTPException(500, "PAYMENT_RECORD_SAVE_FAILED")
    return pay

def update_payment(bk: str, *, payment_status: Optional[str]=None, reported_at: Optional[str]=None, completed_at: Optional[str]=None) -> Optional[dict]:
    upd=[]; vals=[]
    if payment_status is not None:
        upd.append("payment_status=?"); vals.append(clean(payment_status))
    if reported_at is not None:
        upd.append("payment_reported_at=?"); vals.append(clean(reported_at))
    if completed_at is not None:
        upd.append("payment_completed_at=?"); vals.append(clean(completed_at))
    if not upd:
        return get_payment(bk)
    upd.append("updated_at=?"); vals.append(now_iso()); vals.append(clean(bk))
    with db(PAYMENT_DB_PATH) as conn:
        conn.execute(f"UPDATE payment_orders SET {', '.join(upd)} WHERE business_key=?", tuple(vals))
    return get_payment(bk)

def repair_saved_snapshot(product: dict) -> Optional[Path]:
    # Return existing saved file if it exists - do NOT regenerate
    saved=existing_saved_file(product)
    if saved:
        return saved
    # Try to find file on disk in product folder (migration for old DB path missing)
    sc=clean(product.get("service")); tc=clean(product.get("document_title"))
    if not sc or not tc:
        return None
    folder=DOWNLOAD_DIR / safe_folder(sc) / safe_folder(tc)
    if not folder.exists():
        return None
    cands=[x for x in folder.iterdir() if x.is_file() and x.suffix.lower() in {".docx",".pdf"}]
    if not cands:
        return None
    cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    rec=cands[0]
    store_saved_path(clean(product.get("business_key")), rec)
    return rec

def activate_download(bk: str) -> Optional[dict]:
    bk=clean(bk)
    if not bk: return None
    ts=now_iso()
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("UPDATE document_products SET download_unlocked=1, activated_at=?, updated_at=? WHERE business_key=?", (ts,ts,bk))
    return get_product_by_business_key(bk)

def public_product(product: Optional[dict]) -> Optional[dict]:
    if not product: return None
    return {"business_key": clean(product.get("business_key")), "service": clean(product.get("service")), "document_title": clean(product.get("document_title")),
            "customer_name": clean(product.get("customer_name")), "customer_email": clean(product.get("customer_email")), "customer_phone": clean(product.get("customer_phone")),
            "amount": clean(product.get("amount")), "currency": clean(product.get("currency") or "NGN"), "job_id": clean(product.get("job_id")),
            "document_saved": bool(clean(product.get("document_saved_path"))), "document_saved_path": clean(product.get("document_saved_path")),
            "document_saved_at": clean(product.get("document_saved_at")), "download_unlocked": bool(product.get("download_unlocked")),
            "download_count": int(product.get("download_count") or 0), "downloaded_at": clean(product.get("downloaded_at")),
            "activated_at": clean(product.get("activated_at")), "created_at": clean(product.get("created_at")), "updated_at": clean(product.get("updated_at"))}

def api_base(request: Optional[Request]=None) -> str:
    if PUBLIC_API_BASE_URL: return PUBLIC_API_BASE_URL.rstrip("/")
    if request is not None: return str(request.base_url).rstrip("/")
    return ""

def download_url(service: str, title: str, request: Optional[Request]=None) -> str:
    base=api_base(request)
    q=urllib.parse.urlencode({"service": clean(service), "title": clean(title)})
    return f"{base}/api/download?{q}"

def delivery_channels(product: dict, request: Optional[Request]=None) -> list[dict]:
    sc=clean(product.get("service")); tc=clean(product.get("document_title"))
    return [{"channel":"phone","label":"Download to Phone","available": bool(product.get("download_unlocked")), "url": download_url(sc,tc,request)},
            {"channel":"whatsapp","label":"WhatsApp","available": True, "url":""},
            {"channel":"email","label":"Email","available": True, "url":""},
            {"channel":"telegram","label":"Telegram","available": True, "url":""},
            {"channel":"google_drive","label":"Google Drive","available": True, "url":""}]

def create_back_office_download_token(product: dict) -> str:
    token=secrets.token_urlsafe(32)
    created=datetime.now(timezone.utc)
    expires=created+timedelta(minutes=BACK_OFFICE_DOWNLOAD_TOKEN_MINUTES)
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("INSERT INTO back_office_download_tokens (token,business_key,service,document_title,created_at,expires_at,download_count) VALUES (?,?,?,?,?,?,0)",
                     (token, clean(product.get("business_key")), clean(product.get("service")), clean(product.get("document_title")), created.isoformat(), expires.isoformat()))
    return token

def validate_back_office_download_token(token: str) -> Optional[dict]:
    tc=clean(token)
    if not tc: return None
    with db(PRODUCT_DB_PATH) as conn:
        row=conn.execute("SELECT * FROM back_office_download_tokens WHERE token=? LIMIT 1", (tc,)).fetchone()
    if not row: return None
    rec=dict(row)
    try:
        exp=datetime.fromisoformat(clean(rec.get("expires_at")))
        if exp.tzinfo is None: exp=exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp: return None
    except Exception:
        return None
    return rec

def record_back_office_token_download(token: str) -> None:
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("UPDATE back_office_download_tokens SET download_count=download_count+1, last_downloaded_at=? WHERE token=?", (now_iso(), clean(token)))

def back_office_delivery_channels(product: dict, request: Optional[Request]=None) -> list[dict]:
    token=create_back_office_download_token(product)
    base=api_base(request)
    phone_url=f"{base}/api/back-office/delivery-file?token={urllib.parse.quote(token)}"
    return [{"channel":"phone","label":"Download to Phone","available": True, "url": phone_url},
            {"channel":"whatsapp","label":"WhatsApp","available": True, "url":""},
            {"channel":"email","label":"Email","available": True, "url":""},
            {"channel":"telegram","label":"Telegram","available": True, "url":""},
            {"channel":"google_drive","label":"Google Drive","available": True, "url":""}]

def select_channel(channels: list[dict], requested: str) -> Optional[dict]:
    rc=clean(requested).lower()
    for ch in channels:
        if clean(ch.get("channel")).lower()==rc:
            return ch
    return None

def log_delivery(*, business_key_value: str, channel: str, status: str, recipient: str="", detail: str="") -> None:
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("INSERT INTO delivery_events (business_key,channel,status,recipient,detail,created_at) VALUES (?,?,?,?,?,?)",
                     (clean(business_key_value), clean(channel), clean(status), clean(recipient), clean(detail), now_iso()))

def require_back_office(admin_key: Optional[str]) -> None:
    if clean(admin_key)!=BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(status_code=401, detail="BACK_OFFICE_UNAUTHORIZED")

class PaymentCreateRequest(BaseModel):
    service: str; document_title: str; document_payload: dict
    customer_name: str=""; customer_email: str=""; customer_phone: str=""; amount: str=""; currency: str="NGN"; job_id: str=""

class PaymentReportRequest(BaseModel):
    service: str; document_title: str; customer_name: str=""; customer_email: str=""; customer_phone: str=""; amount: str=""; currency: str="NGN"; reported_at: Optional[str]=None

class PaymentCompleteRequest(BaseModel):
    service: str; document_title: str

class BackOfficeActivateRequest(BaseModel):
    service: str; document_title: str

class BackOfficeRejectRequest(BaseModel):
    service: str; document_title: str; reason: str=""

class DeliveryRequest(BaseModel):
    service: str; document_title: str; channel: str; recipient_email: str=""; recipient_phone: str=""; recipient: str=""

# ===================== MAKE PAYMENT - UNCHANGED BEHAVIOR =====================
@app.post("/api/payment/create")
def create_payment(body: PaymentCreateRequest):
    """
    MAKE PAYMENT:
    - Saves exact reviewed document if not already saved (cleaned before becoming canonical)
    - Creates payment record
    - Does NOT verify payment
    - Does NOT unlock download
    """
    product=upsert_product(service=body.service, title=body.document_title, payload=body.document_payload,
                           customer_name=body.customer_name, customer_email=body.customer_email, customer_phone=body.customer_phone,
                           amount=body.amount, currency=body.currency, job_id=body.job_id)
    saved_path=repair_saved_snapshot(product)
    if saved_path is None:
        saved_path=save_exact_snapshot(body.service, body.document_title, body.document_payload, product)
        store_saved_path(clean(product.get("business_key")), saved_path)
        product=get_product(body.service, body.document_title)
    if product is None:
        raise HTTPException(500, "PRODUCT_NOT_FOUND_AFTER_SAVE")
    payment=ensure_payment_record(product)
    return {"ok": True, "message": "PAYMENT_PREPARED", "product": public_product(product), "payment": payment, "payment_verified": False, "download_unlocked": bool(product.get("download_unlocked"))}

@app.post("/api/payment/report")
def report_payment(body: PaymentReportRequest):
    """
    I HAVE MADE PAYMENT - reports only, does NOT unlock
    """
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    payment=get_payment(clean(product.get("business_key")))
    if not payment: payment=ensure_payment_record(product)
    payment=update_payment(clean(product.get("business_key")), payment_status="reported", reported_at=clean(body.reported_at) or now_iso())
    return {"ok": True, "message": "PAYMENT_REPORTED", "product": public_product(product), "payment": payment, "download_unlocked": bool(product.get("download_unlocked"))}

@app.get("/api/payment/status")
def payment_status(service: str, title: str):
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    payment=get_payment(clean(product.get("business_key")))
    return {"ok": True, "product": public_product(product), "payment": payment, "download_unlocked": bool(product.get("download_unlocked"))}

@app.post("/api/payment/complete")
def complete_payment(body: PaymentCompleteRequest):
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    bk=clean(product.get("business_key"))
    payment=get_payment(bk)
    if not payment: raise HTTPException(404, "PAYMENT_NOT_FOUND")
    payment=update_payment(bk, payment_status="completed", completed_at=now_iso())
    return {"ok": True, "message": "PAYMENT_COMPLETED", "product": public_product(product), "payment": payment, "download_unlocked": bool(product.get("download_unlocked"))}

@app.get("/api/customer-care/payments")
def customer_care_payments():
    with db(PAYMENT_DB_PATH) as conn:
        rows=conn.execute("SELECT * FROM payment_orders ORDER BY created_at DESC").fetchall()
    return {"ok": True, "payments": [dict(r) for r in rows]}

@app.post("/api/customer-care/verify-payment")
def customer_care_verify_payment(service: str, title: str):
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    bk=clean(product.get("business_key"))
    payment=get_payment(bk)
    if not payment: raise HTTPException(404, "PAYMENT_NOT_FOUND")
    payment=update_payment(bk, payment_status="verified")
    return {"ok": True, "message": "PAYMENT_VERIFIED", "product": public_product(product), "payment": payment, "download_unlocked": bool(product.get("download_unlocked"))}

@app.post("/api/back-office/login")
def back_office_login(admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    return {"ok": True, "authenticated": True, "message": "BACK_OFFICE_AUTHENTICATED"}

@app.get("/api/back-office/payment-channels")
def back_office_payment_channels_endpoint(admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    return {"ok": True, "channels": [{"channel":"bank_transfer","label":"Bank Transfer","available": True}]}

@app.get("/api/back-office/product")
def back_office_product_endpoint(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    payload=from_json(product.get("document_payload"))
    return {"ok": True, "product": public_product(product), "document_payload": payload, "document_text": clean(product.get("document_text"))}

@app.get("/api/back-office/products")
def back_office_products(admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    with db(PRODUCT_DB_PATH) as conn:
        rows=conn.execute("SELECT * FROM document_products ORDER BY created_at DESC").fetchall()
    return {"ok": True, "products": [public_product(dict(r)) for r in rows], "count": len(rows)}

@app.get("/api/back-office/records")
def back_office_records(admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    with db(PRODUCT_DB_PATH) as conn:
        rows=conn.execute("SELECT * FROM document_products ORDER BY created_at DESC").fetchall()
    records=[]
    for row in rows:
        product=dict(row); bk=clean(product.get("business_key")); payment=get_payment(bk); saved=repair_saved_snapshot(product)
        records.append({"product": public_product(product), "payment": payment, "saved": bool(saved), "payment_status": clean(payment.get("payment_status")) if payment else "not_reported", "download_unlocked": bool(product.get("download_unlocked"))})
    return {"ok": True, "records": records, "count": len(records)}

@app.get("/api/back-office/payment")
def back_office_payment_endpoint(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    return {"ok": True, "product": public_product(product), "payment": get_payment(clean(product.get("business_key")))}

@app.get("/api/back-office/document")
def back_office_document(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    return {"ok": True, "service": clean(product.get("service")), "document_title": clean(product.get("document_title")), "saved": bool(saved), "saved_path": str(saved) if saved else "", "document_payload": from_json(product.get("document_payload")), "document_text": clean(product.get("document_text"))}

@app.get("/api/back-office/document-file")
def back_office_document_file(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    return {"ok": True, "saved": True, "filename": saved.name, "path": str(saved), "size": saved.stat().st_size, "content_type": "application/pdf" if saved.suffix.lower()==".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

@app.post("/api/back-office/verify-payment")
def back_office_verify_payment(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    bk=clean(product.get("business_key"))
    payment=get_payment(bk)
    if not payment: raise HTTPException(404, "PAYMENT_NOT_FOUND")
    payment=update_payment(bk, payment_status="verified")
    return {"ok": True, "message": "PAYMENT_VERIFIED", "product": public_product(product), "payment": payment, "download_unlocked": bool(product.get("download_unlocked"))}

@app.post("/api/back-office/activate-download")
def back_office_activate_download(body: BackOfficeActivateRequest, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    activated=activate_download(clean(product.get("business_key")))
    if not activated: raise HTTPException(500, "DOWNLOAD_ACTIVATION_FAILED")
    return {"ok": True, "message": "CUSTOMER_DOWNLOAD_ACTIVATED", "product": public_product(activated), "saved_document": True, "download_unlocked": True, "download_url": download_url(body.service, body.document_title)}

@app.post("/api/back-office/payment/reject")
def back_office_reject_payment(body: BackOfficeRejectRequest, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    bk=clean(product.get("business_key"))
    payment=get_payment(bk)
    if not payment: raise HTTPException(404, "PAYMENT_NOT_FOUND")
    payment=update_payment(bk, payment_status="rejected")
    log_delivery(business_key_value=bk, channel="payment", status="rejected", detail=clean(body.reason))
    return {"ok": True, "message": "PAYMENT_REJECTED", "product": public_product(product), "payment": payment}

def canonical_file_response(path: Path, attachment: bool=True) -> FileResponse:
    if not path.exists(): raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    if not path.is_file(): raise HTTPException(404, "SAVED_DOCUMENT_FILE_NOT_FOUND")
    media_type="application/pdf" if path.suffix.lower()==".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "X-Content-Type-Options": "nosniff"}
    fname=safe_filename(path.name)
    return FileResponse(path=str(path), media_type=media_type, filename=fname, headers=headers)

# ===================== CUSTOMER DOWNLOAD - EXACT RULE =====================
@app.get("/api/download")
def customer_download(service: str, title: str):
    """
    Customer download - returns exact canonical saved file.
    NO fallback, NO regeneration.
    """
    sc=clean(service)
    tc=clean(title)
    if not sc:
        raise HTTPException(status_code=400, detail="SERVICE_REQUIRED")
    if not tc:
        raise HTTPException(status_code=400, detail="DOCUMENT_TITLE_REQUIRED")

    # EXACT product lookup - no fallback
    product=get_product(sc, tc)
    if not product:
        raise HTTPException(status_code=404, detail="PRODUCT_NOT_FOUND")

    if not bool(product.get("download_unlocked")):
        raise HTTPException(status_code=403, detail="CUSTOMER_DOWNLOAD_LOCKED")

    # Locate canonical file - never regenerate
    saved_path=repair_saved_snapshot(product)
    if saved_path is None:
        raise HTTPException(status_code=404, detail="SAVED_DOCUMENT_NOT_FOUND")
    if not saved_path.exists():
        raise HTTPException(status_code=404, detail="SAVED_DOCUMENT_NOT_FOUND")
    if not saved_path.is_file():
        raise HTTPException(status_code=404, detail="SAVED_DOCUMENT_PATH_IS_NOT_FILE")

    # Increment count only after request is prepared
    with db(PRODUCT_DB_PATH) as conn:
        conn.execute("UPDATE document_products SET download_count=download_count+1, downloaded_at=?, updated_at=? WHERE business_key=?",
                     (now_iso(), now_iso(), clean(product.get("business_key"))))

    # Return exact canonical file
    return canonical_file_response(saved_path, attachment=True)

@app.get("/api/delivery/channels")
def customer_delivery_channels(service: str, title: str, request: Request):
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    return {"ok": True, "product": public_product(product), "channels": delivery_channels(product, request)}

@app.post("/api/delivery/prepare")
def prepare_customer_delivery(body: DeliveryRequest, request: Request):
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    channels=delivery_channels(product, request)
    channel=select_channel(channels, body.channel)
    if not channel: raise HTTPException(400, "DELIVERY_CHANNEL_NOT_FOUND")
    if body.channel.lower()=="phone" and not bool(product.get("download_unlocked")):
        raise HTTPException(403, "CUSTOMER_DOWNLOAD_LOCKED")
    log_delivery(business_key_value=clean(product.get("business_key")), channel=body.channel, status="prepared", recipient=clean(body.recipient or body.recipient_email or body.recipient_phone))
    return {"ok": True, "channel": channel.get("channel"), "label": channel.get("label"), "url": channel.get("url")}

@app.get("/api/back-office/delivery-channels")
def get_back_office_delivery_channels(service: str, title: str, request: Request, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    return {"ok": True, "product": public_product(product), "saved_document": True, "channels": back_office_delivery_channels(product, request)}

@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(token: str):
    record=validate_back_office_download_token(token)
    if not record: raise HTTPException(401, "INVALID_OR_EXPIRED_DOWNLOAD_TOKEN")
    product=get_product_by_business_key(clean(record.get("business_key")))
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    record_back_office_token_download(token)
    log_delivery(business_key_value=clean(product.get("business_key")), channel="back_office_phone", status="downloaded")
    return canonical_file_response(saved, attachment=True)

@app.get("/api/back-office/download")
def back_office_direct_download(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    log_delivery(business_key_value=clean(product.get("business_key")), channel="back_office_download", status="downloaded")
    return canonical_file_response(saved, attachment=True)

@app.get("/api/back-office/delivery-history")
def back_office_delivery_history(service: str, title: str, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    bk=clean(product.get("business_key"))
    with db(PRODUCT_DB_PATH) as conn:
        rows=conn.execute("SELECT * FROM delivery_events WHERE business_key=? ORDER BY created_at DESC", (bk,)).fetchall()
    return {"ok": True, "business_key": bk, "events": [dict(r) for r in rows]}

@app.get("/api/product/status")
def product_status(service: str, title: str):
    product=get_product(service,title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    payment=get_payment(clean(product.get("business_key")))
    return {"ok": True, "product": public_product(product), "saved": bool(saved), "saved_filename": saved.name if saved else "", "payment": payment, "payment_status": clean(payment.get("payment_status")) if payment else "not_reported", "download_unlocked": bool(product.get("download_unlocked")), "download_url": download_url(service,title) if product.get("download_unlocked") else ""}

@app.get("/health")
def health():
    return {"ok": True, "service": "Naija Pocket Business Center", "version": APP_VERSION, "time": now_iso()}

@app.get("/")
def root():
    return {"ok": True, "service": "Naija Pocket Business Center", "version": APP_VERSION, "message": "Payment and document delivery API is running."}

@app.post("/api/back-office/delivery")
def back_office_delivery(body: DeliveryRequest, request: Request, admin_key: Optional[str]=Header(default=None, alias="X-Back-Office-Key")):
    require_back_office(admin_key)
    product=get_product(body.service, body.document_title)
    if not product: raise HTTPException(404, "PRODUCT_NOT_FOUND")
    saved=repair_saved_snapshot(product)
    if not saved: raise HTTPException(404, "SAVED_DOCUMENT_NOT_FOUND")
    channels=back_office_delivery_channels(product, request)
    channel=select_channel(channels, body.channel)
    if not channel: raise HTTPException(400, "DELIVERY_CHANNEL_NOT_FOUND")
    recipient=clean(body.recipient or body.recipient_email or body.recipient_phone)
    log_delivery(business_key_value=clean(product.get("business_key")), channel=body.channel, status="prepared", recipient=recipient)
    return {"ok": True, "message": "DELIVERY_PREPARED", "channel": channel.get("channel"), "label": channel.get("label"), "url": channel.get("url"), "recipient": recipient, "saved_document": True, "filename": saved.name}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT","8000")))
