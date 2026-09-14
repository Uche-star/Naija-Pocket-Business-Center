"""Naija Pocket Business Center - Payment + Saved Document + Customer Care Delivery API."""
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
import json, os, re, sqlite3, urllib.parse
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
try:
    from docx import Document
except Exception:
    Document = None

APP_VERSION="payment-product-first-v6-delivery-recovery"
BASE_DIR=Path(__file__).resolve().parent
DOWNLOAD_DIR=BASE_DIR/"downloads"; DOWNLOAD_DIR.mkdir(parents=True,exist_ok=True)
PRODUCT_DB_PATH=BASE_DIR/"product_delivery.db"
PAYMENT_DB_PATH=BASE_DIR/"payment_gateway.db"
BACK_OFFICE_ADMIN_KEY="NPBC-2026"
PUBLIC_API_BASE_URL=os.getenv("PUBLIC_API_BASE_URL","").strip()
DELIVERY_EMAIL_WEBHOOK=os.getenv("DELIVERY_EMAIL_WEBHOOK","").strip()
DELIVERY_WHATSAPP_WEBHOOK=os.getenv("DELIVERY_WHATSAPP_WEBHOOK","").strip()
DELIVERY_TELEGRAM_WEBHOOK=os.getenv("DELIVERY_TELEGRAM_WEBHOOK","").strip()
DELIVERY_GOOGLE_DRIVE_WEBHOOK=os.getenv("DELIVERY_GOOGLE_DRIVE_WEBHOOK","").strip()

app=FastAPI(title="Naija Pocket Business Center Payment API",version=APP_VERSION)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=False,allow_methods=["*"],allow_headers=["*"])

def now_iso(): return datetime.now(timezone.utc).isoformat()
def clean(v): return "" if v is None else str(v).strip()
def keypart(v): return re.sub(r"\s+"," ",clean(v).casefold())
def bkey(service,title): return f"{keypart(service)}::{keypart(title)}"
def safe_name(v,default="document.docx"):
    s=Path(clean(v) or default).name
    s=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',s); s=re.sub(r"\s+"," ",s).strip(" .") or default
    if not s.lower().endswith((".docx",".pdf")): s += ".docx"
    return s
def safe_folder(v):
    s=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',clean(v) or "document"); s=re.sub(r"\s+"," ",s).strip(" .")
    return s[:120] or "document"
def first(*vals):
    for v in vals:
        if clean(v): return clean(v)
    return ""
def conn(path):
    c=sqlite3.connect(str(path),timeout=30); c.row_factory=sqlite3.Row; return c
def rd(row): return dict(row) if row else None
def j(v): return json.dumps(v,ensure_ascii=False)
def pj(v,default=None):
    if isinstance(v,(dict,list)): return v
    try: return json.loads(clean(v)) if clean(v) else default
    except Exception: return default

def pages(v):
    if v is None:return []
    if isinstance(v,str):
        s=v.strip()
        if not s:return []
        try:
            p=json.loads(s)
            if isinstance(p,(list,dict)): return pages(p)
        except Exception: pass
        return [s]
    if isinstance(v,dict):
        for k in ("pages","page_text","document_pages","content","text"):
            if k in v:return pages(v[k])
        return [json.dumps(v,ensure_ascii=False)]
    if isinstance(v,list):
        out=[]
        for x in v:
            if isinstance(x,dict): out.append(first(x.get("text"),x.get("content"),x.get("page_text")) or json.dumps(x,ensure_ascii=False))
            elif clean(x): out.append(clean(x))
        return out
    return [clean(v)] if clean(v) else []

def normalize_payload(p):
    p=p if isinstance(p,dict) else {"document_text":clean(p)}
    ps=pages(p.get("pages") if p.get("pages") is not None else p.get("document_pages"))
    text=first(p.get("document_text"),p.get("documentText"),p.get("text"),p.get("content"))
    if not ps and text: ps=[text]
    if not text and ps: text="\n\n".join(ps)
    return {"pages":ps,"document_text":text,"filename":safe_name(first(p.get("filename"),p.get("document_filename"),p.get("documentFilename")) or "document.docx"),"document_version":first(p.get("document_version"),p.get("documentVersion"),p.get("version")),"page_count":len(ps)}

def init_dbs():
    c=conn(PRODUCT_DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS document_products(id INTEGER PRIMARY KEY AUTOINCREMENT,business_key TEXT NOT NULL UNIQUE,service TEXT NOT NULL,document_title TEXT NOT NULL,customer_name TEXT,customer_id TEXT,amount REAL DEFAULT 0,currency TEXT DEFAULT 'NGN',document_version TEXT,document_filename TEXT,document_pages INTEGER DEFAULT 0,document_payload TEXT,document_saved_path TEXT,document_saved_at TEXT,download_unlocked INTEGER DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,activated_at TEXT,downloaded_at TEXT,download_count INTEGER DEFAULT 0,notes TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS delivery_events(id INTEGER PRIMARY KEY AUTOINCREMENT,business_key TEXT NOT NULL,service TEXT NOT NULL,document_title TEXT NOT NULL,channel TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,details TEXT)"""); c.commit(); c.close()
    c=conn(PAYMENT_DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS payment_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,business_key TEXT NOT NULL UNIQUE,service TEXT NOT NULL,document_title TEXT NOT NULL,customer_name TEXT,customer_id TEXT,amount REAL DEFAULT 0,currency TEXT DEFAULT 'NGN',payment_method TEXT,payment_status TEXT DEFAULT 'payment_ready',document_version TEXT,document_filename TEXT,document_pages INTEGER DEFAULT 0,document_text TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reported_at TEXT,verified_at TEXT,rejected_at TEXT,notes TEXT)"""); c.commit(); c.close()
@app.on_event("startup")
def startup(): init_dbs()


def get_product(service,title):
    c=conn(PRODUCT_DB_PATH); r=c.execute("SELECT * FROM document_products WHERE business_key=?",(bkey(service,title),)).fetchone(); c.close(); return rd(r)
def get_single_product():
    c=conn(PRODUCT_DB_PATH); r=c.execute("SELECT * FROM document_products ORDER BY updated_at DESC,id DESC LIMIT 1").fetchone(); c.close(); return rd(r)
def get_product_or_single(service,title): return get_product(service,title) or get_single_product()
def get_payment(service,title):
    c=conn(PAYMENT_DB_PATH); r=c.execute("SELECT * FROM payment_orders WHERE business_key=?",(bkey(service,title),)).fetchone(); c.close(); return rd(r)

def make_docx(path,title,ps):
    if Document is None: raise HTTPException(500,"PYTHON_DOCX_NOT_INSTALLED")
    path.parent.mkdir(parents=True,exist_ok=True); d=Document(); d.add_heading(title or "Naija Pocket Business Center Document",level=1)
    for i,p in enumerate(ps,1):
        if i>1:d.add_page_break()
        if len(ps)>1:d.add_paragraph(f"PAGE {i}")
        for line in str(p).splitlines(): d.add_paragraph(line)
    d.save(str(path)); return path

def existing_file(product):
    if not product:return None
    p=clean(product.get("document_saved_path"))
    if not p:return None
    q=Path(p); q=BASE_DIR/q if not q.is_absolute() else q
    return q if q.exists() and q.is_file() else None

def save_snapshot(service,title,payload,existing=None):
    old=existing_file(existing)
    if old:return old
    p=normalize_payload(payload)
    if not p["pages"] and not p["document_text"]: raise HTTPException(400,"SAVED_DOCUMENT_CONTENT_MISSING")
    folder=DOWNLOAD_DIR/safe_folder(service)/safe_folder(title); folder.mkdir(parents=True,exist_ok=True)
    fn=safe_name(p["filename"]); fn=Path(fn).stem+".docx" if fn.lower().endswith(".pdf") else fn
    target=folder/fn
    if target.exists() and target.is_file():return target
    return make_docx(target,title,p["pages"] or [p["document_text"]])

def store_path(service,title,path):
    c=conn(PRODUCT_DB_PATH); c.execute("UPDATE document_products SET document_saved_path=?,document_saved_at=COALESCE(document_saved_at,?),updated_at=? WHERE business_key=?",(str(path),now_iso(),now_iso(),bkey(service,title))); c.commit(); c.close()

def extract_text(product): return normalize_payload(pj(product.get("document_payload"),{}))["document_text"]

def upsert_product(d):
    service,title=clean(d["service"]),clean(d["document_title"]); p=normalize_payload(d.get("document_payload",d)); old=get_product(service,title); n=now_iso(); c=conn(PRODUCT_DB_PATH)
    if old:
        c.execute("""UPDATE document_products SET customer_name=?,customer_id=?,amount=?,currency=?,document_version=?,document_filename=?,document_pages=?,document_payload=CASE WHEN COALESCE(document_saved_path,'')='' THEN ? ELSE document_payload END,updated_at=? WHERE business_key=?""",(clean(d.get("customer_name")),clean(d.get("customer_id")),float(d.get("amount") or 0),clean(d.get("currency")) or "NGN",p["document_version"],p["filename"],p["page_count"],j(p),n,bkey(service,title)))
    else:
        c.execute("""INSERT INTO document_products(business_key,service,document_title,customer_name,customer_id,amount,currency,document_version,document_filename,document_pages,document_payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(bkey(service,title),service,title,clean(d.get("customer_name")),clean(d.get("customer_id")),float(d.get("amount") or 0),clean(d.get("currency")) or "NGN",p["document_version"],p["filename"],p["page_count"],j(p),n,n))
    c.commit(); c.close(); return get_product(service,title)

def ensure_payment(product):
    service,title=product["service"],product["document_title"]; old=get_payment(service,title); n=now_iso(); c=conn(PAYMENT_DB_PATH); vals=(clean(product.get("customer_name")),clean(product.get("customer_id")),float(product.get("amount") or 0),clean(product.get("currency")) or "NGN",clean(product.get("document_version")),clean(product.get("document_filename")),int(product.get("document_pages") or 0),extract_text(product))
    if old:c.execute("UPDATE payment_orders SET customer_name=?,customer_id=?,amount=?,currency=?,document_version=?,document_filename=?,document_pages=?,document_text=?,updated_at=? WHERE business_key=?",vals+(n,bkey(service,title)))
    else:c.execute("""INSERT INTO payment_orders(business_key,service,document_title,customer_name,customer_id,amount,currency,payment_method,payment_status,document_version,document_filename,document_pages,document_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(bkey(service,title),service,title,*vals,"bank_transfer","payment_ready",n,n))
    c.commit(); c.close(); return get_payment(service,title)

def repair(product):
    product=get_product(product["service"],product["document_title"]) or product
    if existing_file(product):return product
    payload=normalize_payload(pj(product.get("document_payload"),{}))
    if not payload["document_text"] and not payload["pages"]:
        pay=get_payment(product["service"],product["document_title"])
        if pay:payload=normalize_payload({"document_text":pay.get("document_text"),"filename":pay.get("document_filename"),"document_version":pay.get("document_version")})
    if not payload["document_text"] and not payload["pages"]:raise HTTPException(404,"SAVED_DOCUMENT_CONTENT_MISSING")
    path=save_snapshot(product["service"],product["document_title"],payload,product); store_path(product["service"],product["document_title"],path)
    return get_product(product["service"],product["document_title"]) or product

def public(product):
    if not product:return {"found":False,"saved_document":False,"download_unlocked":False}
    pay=get_payment(product["service"],product["document_title"]); saved=existing_file(product)
    return {"found":True,"id":product.get("id"),"service":product.get("service"),"document_title":product.get("document_title"),"customer_name":product.get("customer_name"),"customer_id":product.get("customer_id"),"amount":product.get("amount"),"currency":product.get("currency") or "NGN","document_version":product.get("document_version"),"document_filename":product.get("document_filename"),"document_pages":product.get("document_pages") or 0,"document_saved_path":str(saved) if saved else clean(product.get("document_saved_path")),"document_saved_at":product.get("document_saved_at"),"saved_document":bool(saved),"download_unlocked":bool(product.get("download_unlocked")),"activated_at":product.get("activated_at"),"download_count":product.get("download_count") or 0,"payment_status":pay.get("payment_status") if pay else "payment_ready","payment_reported":bool(pay and pay.get("reported_at")),"payment_verified":bool(pay and pay.get("verified_at")),"payment_rejected":bool(pay and pay.get("rejected_at"))}

def activate(service,title):
    c=conn(PRODUCT_DB_PATH); c.execute("UPDATE document_products SET download_unlocked=1,activated_at=?,updated_at=? WHERE business_key=?",(now_iso(),now_iso(),bkey(service,title))); c.commit(); c.close(); return get_product(service,title)
def update_payment(service,title,status=None,**kw):
    sets=[]; vals=[]
    if status:sets.append("payment_status=?");vals.append(status)
    for k,v in kw.items():
        if k in {"reported_at","verified_at","rejected_at","notes","payment_method"}:sets.append(k+"=?");vals.append(v)
    sets.append("updated_at=?");vals.append(now_iso()); vals.append(bkey(service,title)); c=conn(PAYMENT_DB_PATH); c.execute("UPDATE payment_orders SET "+",".join(sets)+" WHERE business_key=?",vals);c.commit();c.close();return get_payment(service,title)

def base_url(request):return PUBLIC_API_BASE_URL.rstrip("/") or str(request.base_url).rstrip("/")
def download_url(request,s,t):return base_url(request)+"/api/download?service="+urllib.parse.quote(s)+"&title="+urllib.parse.quote(t)
def channels(request,p):
    s,t=p["service"],p["document_title"]; u=download_url(request,s,t); text=urllib.parse.quote(f"{t} — {s}\nNaija Pocket Business Center document download:\n{u}"); sub=urllib.parse.quote(f"{t} — Naija Pocket Business Center")
    return {"available":bool(p.get("download_unlocked")),"document_saved":bool(existing_file(p)),"channels":[{"id":"phone","name":"Download to Phone","type":"download","available":bool(p.get("download_unlocked")),"url":u},{"id":"whatsapp","name":"WhatsApp","type":"share","available":bool(p.get("download_unlocked")),"url":"https://wa.me/?text="+text},{"id":"email","name":"Email","type":"share","available":bool(p.get("download_unlocked")),"url":"mailto:?subject="+sub+"&body="+text},{"id":"telegram","name":"Telegram","type":"share","available":bool(p.get("download_unlocked")),"url":"https://t.me/share/url?url="+urllib.parse.quote(u)+"&text="+urllib.parse.quote(t)},{"id":"google_drive","name":"Google Drive","type":"share","available":bool(p.get("download_unlocked")),"url":"https://drive.google.com/drive/my-drive","note":"Download the exact saved file first, then upload that same file to Google Drive."}]}
def channel(c,which):
    a={"download":"phone","direct":"phone","phone":"phone","whatsapp":"whatsapp","email":"email","telegram":"telegram","drive":"google_drive","google drive":"google_drive","google_drive":"google_drive"}; w=a.get(clean(which).casefold(),clean(which).casefold()); return next((x for x in c["channels"] if x["id"]==w),None)
def log(p,ch,status,details=None):
    c=conn(PRODUCT_DB_PATH);c.execute("INSERT INTO delivery_events(business_key,service,document_title,channel,status,created_at,details) VALUES(?,?,?,?,?,?,?)",(bkey(p["service"],p["document_title"]),p["service"],p["document_title"],ch,status,now_iso(),j(details or {})));c.commit();c.close()
def require_key(k):
    if clean(k)!=BACK_OFFICE_ADMIN_KEY:raise HTTPException(401,"INVALID_BACK_OFFICE_KEY")

class PaymentCreateRequest(BaseModel):
    customer_name:str="";customer_id:str="";service:str;document_title:str;amount:float;currency:str="NGN";payment_method:str="bank_transfer";document_version:str="";pages:Any=None;document_pages:Any=None;document_text:str="";filename:str="";document_filename:str="";document_payload:Any=None;documentText:str=""
class PaymentReportRequest(BaseModel):service:str;document_title:str;note:str=""
class PaymentCompleteRequest(BaseModel):service:str;document_title:str
class BackOfficeActivateRequest(BaseModel):service:str;document_title:str;verified:bool=True
class BackOfficeRejectRequest(BaseModel):service:str;document_title:str;reason:str=""
class DeliveryRequest(BaseModel):service:str;document_title:str;channel:str

@app.post("/api/payment/create")
def payment_create(body:PaymentCreateRequest,request:Request):
    if not clean(body.service) or not clean(body.document_title):raise HTTPException(400,"SERVICE_AND_TITLE_REQUIRED")
    if body.amount<=0:raise HTTPException(400,"VALID_PAYMENT_AMOUNT_REQUIRED")
    raw=pj(body.document_payload,{}) if isinstance(body.document_payload,str) else body.document_payload
    payload=normalize_payload(raw if isinstance(raw,dict) else {"pages":body.pages if body.pages is not None else body.document_pages,"document_text":first(body.document_text,body.documentText),"filename":first(body.filename,body.document_filename),"document_version":body.document_version})
    if not payload["document_text"] and not payload["pages"]:raise HTTPException(400,"DOCUMENT_TEXT_REQUIRED")
    old=get_product(body.service,body.document_title)
    p=upsert_product({"service":body.service,"document_title":body.document_title,"customer_name":body.customer_name,"customer_id":body.customer_id,"amount":body.amount,"currency":body.currency,"document_version":body.document_version,"filename":payload["filename"],"document_payload":payload})
    path=save_snapshot(body.service,body.document_title,payload,p);store_path(body.service,body.document_title,path);p=get_product(body.service,body.document_title) or p;pay=ensure_payment(p)
    return {"ok":True,"message":"Payment record prepared.","product":public(p),"payment":pay,"saved_document":True,"saved_document_path":str(path),"download_unlocked":bool(p.get("download_unlocked")),"delivery":channels(request,p)}

@app.post("/api/payment/report")
def payment_report(body:PaymentReportRequest):
    p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"SAVED_DOCUMENT_NOT_FOUND")
    p=repair(p);ensure_payment(p);pay=update_payment(body.service,body.document_title,"payment_reported",reported_at=now_iso(),notes=body.note)
    return {"ok":True,"message":"Payment reported. Customer Care will verify it.","product":public(p),"payment":pay,"saved_document":True,"download_unlocked":bool(p.get("download_unlocked"))}

@app.get("/api/payment/status")
def payment_status(service:str,title:str,request:Request):
    p=get_product_or_single(service,title)
    if not p:return {"ok":True,"found":False,"payment":None,"product":public(None)}
    p=repair(p);return {"ok":True,"found":True,"product":public(p),"payment":get_payment(service,title),"delivery":channels(request,p)}

@app.post("/api/payment/complete")
def payment_complete(body:PaymentCompleteRequest):
    p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);pay=update_payment(body.service,body.document_title,"payment_verified",verified_at=now_iso());return {"ok":True,"message":"Payment marked verified.","product":public(p),"payment":pay,"download_unlocked":bool(p.get("download_unlocked"))}

@app.get("/api/customer-care/payments")
def cc_payments(x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);c=conn(PAYMENT_DB_PATH);r=c.execute("SELECT * FROM payment_orders ORDER BY updated_at DESC,id DESC").fetchall();c.close();return {"ok":True,"payments":[dict(x) for x in r]}

@app.post("/api/customer-care/payment/verify")
def cc_verify(body:PaymentCompleteRequest,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);pay=update_payment(body.service,body.document_title,"payment_verified",verified_at=now_iso());return {"ok":True,"message":"Payment verified. Download still requires activation.","product":public(p),"payment":pay,"download_unlocked":bool(p.get("download_unlocked"))}

@app.post("/api/back-office/login")
def bo_login(x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);return {"ok":True,"authenticated":True,"message":"Customer Care Back Office access granted."}

@app.get("/api/back-office/payments")
def bo_payments(x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);c=conn(PRODUCT_DB_PATH);rows=c.execute("SELECT * FROM document_products ORDER BY updated_at DESC,id DESC").fetchall();c.close();items=[public(repair(dict(x))) for x in rows];return {"ok":True,"payments":items,"records":items,"items":items,"summary":{"saved":len(items),"reported":sum(x["payment_reported"] for x in items),"activated":sum(x["download_unlocked"] for x in items)}}

@app.get("/api/back-office/jobs")
def bo_jobs(x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);c=conn(PRODUCT_DB_PATH);r=c.execute("SELECT * FROM document_products ORDER BY updated_at DESC,id DESC").fetchall();c.close();return {"ok":True,"jobs":[dict(x) for x in r]}

@app.get("/api/back-office/payment")
def bo_payment(service:str,title:str,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);return {"ok":True,"product":public(p),"payment":get_payment(service,title)}

@app.get("/api/back-office/document-info")
def bo_doc_info(service:str,title:str,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);f=existing_file(p);return {"ok":True,"service":p["service"],"document_title":p["document_title"],"filename":p.get("document_filename"),"pages":p.get("document_pages") or 0,"saved_document":bool(f),"saved_path":str(f) if f else "","download_unlocked":bool(p.get("download_unlocked"))}

@app.get("/api/back-office/document")
def bo_document(service:str,title:str,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);q=normalize_payload(pj(p.get("document_payload"),{}));return {"ok":True,"service":p["service"],"document_title":p["document_title"],"filename":p.get("document_filename"),"pages":q["pages"],"page_count":q["page_count"],"document_text":q["document_text"],"saved_document":bool(existing_file(p)),"download_unlocked":bool(p.get("download_unlocked"))}

@app.post("/api/back-office/payment/verify")
def bo_payment_verify(body:PaymentCompleteRequest,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);pay=update_payment(body.service,body.document_title,"payment_verified",verified_at=now_iso());return {"ok":True,"message":"Payment verified. Activate download separately when ready.","product":public(p),"payment":pay,"download_unlocked":bool(p.get("download_unlocked"))}

@app.post("/api/back-office/activate-download")
def bo_activate(body:BackOfficeActivateRequest,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key)
    if not body.verified:raise HTTPException(400,"VERIFICATION_REQUIRED")
    p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p)
    if not existing_file(p):raise HTTPException(404,"SAVED_DOCUMENT_FILE_MISSING")
    p=activate(body.service,body.document_title);return {"ok":True,"message":"Download activated.","product":public(p),"download_unlocked":True}

@app.post("/api/back-office/payment/reject")
def bo_reject(body:BackOfficeRejectRequest,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    pay=update_payment(body.service,body.document_title,"payment_rejected",rejected_at=now_iso(),notes=body.reason);return {"ok":True,"message":"Payment marked rejected.","product":public(p),"payment":pay,"download_unlocked":bool(p.get("download_unlocked"))}

@app.get("/api/download")
def download(service:str,title:str):
    p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    if not p.get("download_unlocked"):raise HTTPException(403,"DOWNLOAD_NOT_UNLOCKED")
    p=repair(p);f=existing_file(p)
    if not f:raise HTTPException(404,"SAVED_DOCUMENT_FILE_MISSING")
    c=conn(PRODUCT_DB_PATH);c.execute("UPDATE document_products SET download_count=COALESCE(download_count,0)+1,downloaded_at=?,updated_at=? WHERE business_key=?",(now_iso(),now_iso(),bkey(service,title)));c.commit();c.close()
    return FileResponse(str(f),filename=f.name,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" if f.suffix.lower()==".docx" else "application/octet-stream")

@app.get("/api/delivery/channels")
def delivery_channels(service:str,title:str,request:Request):
    p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p)
    if not existing_file(p):raise HTTPException(404,"SAVED_DOCUMENT_FILE_MISSING")
    return {"ok":True,"product":public(p),**channels(request,p)}

@app.post("/api/delivery/prepare")
def delivery_prepare(body:DeliveryRequest,request:Request):
    p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p)
    if not p.get("download_unlocked"):raise HTTPException(403,"DOWNLOAD_NOT_UNLOCKED")
    x=channel(channels(request,p),body.channel)
    if not x:raise HTTPException(404,"DELIVERY_CHANNEL_NOT_FOUND")
    log(p,x["id"],"prepared",{"url":x.get("url")});return {"ok":True,"channel":x,"product":public(p)}

@app.get("/api/back-office/delivery")
def bo_delivery(service:str,title:str,channel:str,request:Request,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p)
    if not p.get("download_unlocked"):raise HTTPException(403,"DOWNLOAD_NOT_UNLOCKED")
    x=channel(channels(request,p),channel)
    if not x:raise HTTPException(404,"DELIVERY_CHANNEL_NOT_FOUND")
    log(p,x["id"],"ready",{"url":x.get("url")});return {"ok":True,"channel":x,"product":public(p)}

@app.get("/api/back-office/delivery-file")
def bo_delivery_file(service:str,title:str,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(service,title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p);f=existing_file(p)
    if not f:raise HTTPException(404,"SAVED_DOCUMENT_FILE_MISSING")
    return FileResponse(str(f),filename=f.name,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" if f.suffix.lower()==".docx" else "application/octet-stream")

@app.get("/api/back-office/delivery-history")
def bo_history(service:Optional[str]=None,title:Optional[str]=None,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);c=conn(PRODUCT_DB_PATH)
    if service and title:r=c.execute("SELECT * FROM delivery_events WHERE business_key=? ORDER BY created_at DESC,id DESC",(bkey(service,title),)).fetchall()
    else:r=c.execute("SELECT * FROM delivery_events ORDER BY created_at DESC,id DESC").fetchall()
    c.close();return {"ok":True,"events":[dict(x) for x in r]}

@app.get("/api/product/status")
def product_status(service:str,title:str,request:Request):
    p=get_product_or_single(service,title)
    if not p:return {"ok":True,"found":False,"product":public(None)}
    p=repair(p);return {"ok":True,"found":True,"product":public(p),"delivery":channels(request,p)}

@app.get("/health")
def health():return {"ok":True,"service":"Naija Pocket Business Center Payment API","version":APP_VERSION,"back_office_key_configured":True}
@app.get("/")
def root():return {"ok":True,"service":"Naija Pocket Business Center Payment API","version":APP_VERSION,"message":"Payment, saved-document, Customer Care and delivery API is running."}

@app.post("/api/back-office/delivery")
async def bo_delivery_post(body:DeliveryRequest,request:Request,x_back_office_key:str=Header(default="")):
    require_key(x_back_office_key);p=get_product_or_single(body.service,body.document_title)
    if not p:raise HTTPException(404,"PRODUCT_NOT_FOUND")
    p=repair(p)
    if not p.get("download_unlocked"):raise HTTPException(403,"DOWNLOAD_NOT_UNLOCKED")
    x=channel(channels(request,p),body.channel)
    if not x:raise HTTPException(404,"DELIVERY_CHANNEL_NOT_FOUND")
    # Standard share/download channels work without any external API key.
    log(p,x["id"],"ready",{"url":x.get("url")})
    return {"ok":True,"status":"ready","channel":x,"message":"The exact saved document is ready through this channel."}

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
