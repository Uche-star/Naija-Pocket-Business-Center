from __future__ import annotations
import asyncio, io, os, re, traceback, uuid, zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from ada_response import AdaResponse, get_ada_model, is_configured, normalize_document_pages, document_text_to_pages

DEBUG=os.getenv('ADA_DEBUG_ERRORS','true').lower() in {'1','true','yes','on'}
MAX_UPLOAD=int(os.getenv('ADA_MAX_UPLOAD_BYTES',str(25*1024*1024)))
BASE=Path(__file__).resolve().parent
_sessions:dict[str,AdaResponse]={}; _jobs:dict[str,dict[str,Any]]={}; _review_tasks={}; _correction_tasks={}
app=FastAPI(title='Naija Pocket Business Center',version='review-intelligence-v7')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])

def find_file(n):
    for p in [BASE/n,BASE/'app'/n,BASE/'static'/n,BASE/'public'/n,BASE/'assets'/n]:
        if p.is_file(): return p

def key(c,j): return f"{str(c or 'anonymous').strip() or 'anonymous'}:{str(j or 'default').strip() or 'default'}"
def session(c,j,s=None):
    k=key(c,j); a=_sessions.get(k)
    if a is None: a=AdaResponse(service=s); _sessions[k]=a
    elif s: a.set_service(s)
    return a

def err(stage,e,status=500,code='APPLICATION_ERROR'):
    print(f'[{stage}] {type(e).__name__}: {e}'); traceback.print_exc()
    return JSONResponse(status_code=status,content={'success':False,'stage':stage,'error':code,'error_type':type(e).__name__,'error_message':str(e) if DEBUG else 'An internal application error occurred.'})

class Chat(BaseModel):
    message:str=''; service:str|None=None; event:str|None=None; customer_id:str|None=None; job_id:str|None=None; client_request_id:str|None=None
    activate_intelligence:bool=True; context:str|None=None; form_data:dict[str,Any]|None=None; guidance_only:bool=False; create_work:bool=False
    document_pages:list[Any]|None=None; document_text:str|None=None
class Correction(BaseModel): job_id:str; instruction:str
class Approval(BaseModel): job_id:str; version_id:str

def ev(x): return str(x or '').strip().lower()
def form_request(r):
    p=[]
    if r.service:p.append('SELECTED SERVICE:\n'+r.service.strip())
    if r.form_data:
        p.append('CUSTOMER PROVIDED SERVICE INFORMATION:\n'+'\n'.join(f"{str(k).replace('_',' ').title()}: {v}" for k,v in r.form_data.items() if str(v or '').strip()))
    if r.context and r.context.strip():p.append('ADDITIONAL CONTEXT:\n'+r.context.strip())
    if r.message.strip():p.append('CUSTOMER REQUEST:\n'+r.message.strip())
    return '\n\n'.join(p).strip()
def ctx(r):
    p=[r.context.strip()] if r.context and r.context.strip() else []
    if r.customer_id:p.append('CUSTOMER ID:\n'+r.customer_id)
    if r.client_request_id:p.append('CLIENT REQUEST ID:\n'+r.client_request_id)
    return '\n\n'.join(p) or None

def stored(pages):
    out=[]
    for i,p in enumerate(normalize_document_pages(pages or []),1):
        if isinstance(p,dict): out.append({**p,'page_number':int(p.get('page_number',i) or i),'position':i,'content':str(p.get('content','') or '')})
    return out

def job_response(j):
    pages=stored(j.get('document_pages',[])); j['document_pages']=pages
    return {'success':True,'job_id':j['job_id'],'customer_id':j.get('customer_id'),'service':j.get('service'),'status':j.get('status'),'current_version':j.get('current_version',1),'version_id':j.get('version_id'),'review_started':j.get('review_started',False),'review_finished':j.get('review_finished',False),'approved':j.get('approved',False),'paid':j.get('paid',False),'progress':{'completed':j.get('progress',{}).get('completed',0),'total':len(pages)},'total_pages':len(pages),'document_pages':pages,'pages':pages,'review_pages':j.get('review_pages',[]),'assembled_review':j.get('assembled_review',''),'error':j.get('review_error'),'review_url':f"/review.html?job_id={j['job_id']}"}
def review_pages(p): return [{'page_number':x.get('page_number',i),'position':i,'status':'queued','content':str(x.get('content','') or ''),'review':'','error':None} for i,x in enumerate(stored(p),1)]
def new_job(jid,r,request,pages):
    pages=stored(pages)
    if not pages: raise ValueError('Cannot create a job without document pages.')
    j={'job_id':jid,'customer_id':r.customer_id,'service':r.service,'original_request':request,'context':ctx(r),'client_request_id':r.client_request_id,'status':'reviewing','review_started':True,'review_finished':False,'review_error':None,'progress':{'completed':0,'total':len(pages)},'document_pages':pages,'review_pages':review_pages(pages),'assembled_review':'','current_version':1,'version_id':jid+':1','approved':False,'paid':False}; _jobs[jid]=j; return j

def cb_review(jid):
    def cb(u):
        j=_jobs.get(jid)
        if not j:return
        t=ev(u.get('type')); n=str(u.get('page_number',''))
        if t=='page_started':
            for p in j['review_pages']:
                if str(p['page_number'])==n:p['status']='reviewing'
        elif t=='page_completed':
            for p in j['review_pages']:
                if str(p['page_number'])==n:
                    p['status']='reviewed'; p['review']=str(u.get('review','') or ''); p['content']=str(u.get('content',p['content']) or ''); p['error']=None
            j['progress']['completed']=int(u.get('position') or j['progress']['completed'])
        elif t=='page_error':
            for p in j['review_pages']:
                if str(p['page_number'])==n:p['status']='error';p['error']=str(u.get('error','Page review failed.'))
        elif t=='review_completed':
            j['status']='review_complete';j['review_finished']=True;j['progress']={'completed':len(j['document_pages']),'total':len(j['document_pages'])};j['assembled_review']=str(u.get('assembled_review','') or '')
    return cb
async def run_review(jid):
    j=_jobs.get(jid)
    if not j:return
    try:
        a=session(j.get('customer_id'),jid,j.get('service')); pages=stored(j['document_pages'])
        r=await asyncio.to_thread(a.review_document_pages,pages=pages,service=j.get('service'),context=j.get('context'),customer_request=j.get('original_request'),event='send_for_review',progress_callback=cb_review(jid))
        if not isinstance(r,dict):raise TypeError('Invalid review result.')
        for rp in r.get('pages',[]) or []:
            if isinstance(rp,dict):
                for p in j['review_pages']:
                    if str(p['page_number'])==str(rp.get('page_number')): p.update({k:rp[k] for k in ('review','content') if k in rp});p['status']='reviewed'
        j['assembled_review']=str(r.get('assembled_review','') or '')
        j['status']='review_complete';j['review_finished']=True;j['review_error']=None;j['progress']={'completed':len(j['document_pages']),'total':len(j['document_pages'])}
    except asyncio.CancelledError: raise
    except Exception as e:j['status']='review_error';j['review_finished']=True;j['review_error']={'type':type(e).__name__,'message':str(e)};traceback.print_exc()
def start_review(jid):
    j=_jobs.get(jid)
    if not j or not j.get('document_pages') or j.get('status')!='reviewing':return False
    t=_review_tasks.get(jid)
    if t and not t.done():return False
    _review_tasks[jid]=asyncio.create_task(run_review(jid));return True

def extract(data,name):
    s=Path(name).suffix.lower()
    if s in {'.txt','.csv'}:return data.decode('utf-8','replace')
    if s=='.pdf':
        from pypdf import PdfReader
        return '\n\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(data)).pages)
    if s in {'.docx','.xlsx','.pptx'}:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=z.namelist(); texts=[]
            pats={'docx':'word/document.xml','pptx':r'ppt/slides/slide\d+\.xml','xlsx':r'xl/worksheets/sheet\d+\.xml'}
            if s=='.docx': names=[pats['docx']] if pats['docx'] in names else []
            else:names=[n for n in names if re.match(pats[s[1:]],n)]
            for n in sorted(names):
                root=ET.fromstring(z.read(n)); vals=[(x.text or '') for x in root.iter() if isinstance(x.tag,str) and x.tag.rsplit('}',1)[-1]=='t']
                if vals:texts.append(' '.join(vals))
            return '\n\n'.join(texts)
    raise RuntimeError(f'Unsupported document type: {s or "unknown"}')
def upload_pages(name,data):
    text=extract(data,name).strip()
    if not text:raise ValueError('The uploaded document contains no extractable text.')
    return stored(document_text_to_pages(text))

def generated_pages(r):
    for k in ('pages','document_pages','prepared_pages','content_pages'):
        if isinstance(r,dict) and isinstance(r.get(k),list):
            p=stored(r[k])
            if p:return p
    if isinstance(r,dict):
        for k in ('document_text','prepared_work','document','content','text','reply','response','message'):
            if isinstance(r.get(k),str) and r[k].strip():return stored(document_text_to_pages(r[k]))
    if isinstance(r,str) and r.strip():return stored(document_text_to_pages(r))
    raise ValueError('AdaResponse returned no usable document work.')
async def create_work(a,r,req,context):
    # Use the deployed AdaResponse creation interface when present; never create documents by keyword rules.
    for name in ('create_document','generate_document','create_work','generate_work'):
        fn=getattr(a,name,None)
        if callable(fn):
            try:return generated_pages(await asyncio.to_thread(fn,customer_request=req,service=r.service,form_data=r.form_data,context=context,event=r.event))
            except TypeError:
                return generated_pages(await asyncio.to_thread(fn,message=req,service=r.service,context=context,event=r.event))
    # Compatibility path: the intelligence layer's normal response may itself return structured document pages.
    fn=getattr(a,'respond',None)
    if not callable(fn):raise AttributeError('AdaResponse has no document creation method.')
    return generated_pages(await asyncio.to_thread(fn,message=req,service=r.service,event=r.event,context=context,create_work=True,form_data=r.form_data))

def html(n):
    p=find_file(n)
    if not p:return err('PAGE',f'{n} was not found.',404,'HTML_NOT_FOUND')
    return FileResponse(p,media_type='text/html')
@app.get('/')
async def root():return html('index.html')
@app.get('/index.html')
async def index():return html('index.html')
@app.get('/conversation.html')
async def conversation():return html('conversation.html')
@app.get('/workspace.html')
async def workspace():return html('workspace.html')
@app.get('/review.html')
async def review_page():return html('review.html')
@app.get('/payment.html')
async def payment_page():return html('payment.html')
@app.get('/download.html')
async def download_page():return html('download.html')
@app.get('/health')
async def health():return {'success':True,'status':'ok','api':'FastAPI','intelligence':'AdaResponse','model':get_ada_model(),'configured':is_configured()}
@app.get('/api/status')
async def status():return {'success':True,'api':'FastAPI','intelligence':'AdaResponse','model':get_ada_model(),'configured':is_configured(),'active_sessions':len(_sessions),'active_jobs':len(_jobs)}

@app.post('/api/upload')
async def upload(file:UploadFile=File(...),customer_id:str|None=Form(None),job_id:str|None=Form(None),client_request_id:str|None=Form(None),service:str|None=Form(None)):
    try:
        data=await file.read()
        if not data:return err('UPLOAD','The uploaded file is empty.',400,'EMPTY_FILE')
        if len(data)>MAX_UPLOAD:return err('UPLOAD','The uploaded document is too large.',413,'FILE_TOO_LARGE')
        pages=await asyncio.to_thread(upload_pages,file.filename or 'document',data); jid=str(job_id or '').strip() or str(uuid.uuid4())
        return {'success':True,'filename':file.filename,'job_id':jid,'customer_id':customer_id,'client_request_id':client_request_id,'service':service,'total_pages':len(pages),'document_pages':pages,'pages':pages}
    except Exception as e:return err('UPLOAD',e,400,'DOCUMENT_UPLOAD_ERROR')

@app.post('/api/chat')
async def chat(r:Chat):
    if not r.activate_intelligence:return err('INTELLIGENCE', 'Intelligence activation is disabled.',400,'INTELLIGENCE_NOT_ACTIVATED')
    if not is_configured():return err('INTELLIGENCE','AdaResponse is not configured.',503,'INTELLIGENCE_NOT_CONFIGURED')
    jid=str(r.job_id or '').strip() or str(uuid.uuid4()); application=ctx(r); pages=stored(r.document_pages or [])
    if not pages and r.document_text and r.document_text.strip():pages=stored(document_text_to_pages(r.document_text))
    try:
        a=session(r.customer_id,jid,r.service)
        if r.guidance_only:
            if not r.message.strip():return err('GUIDANCE','The guidance message is empty.',400,'EMPTY_GUIDANCE_MESSAGE')
            reply=await asyncio.to_thread(a.respond,message=r.message.strip(),service=r.service,event=r.event,context=application)
            return {'success':True,'reply':str(reply or '').strip(),'job_id':jid,'created_work':False}
        request=form_request(r)
        # A form submission creates the document first, then immediately enters the same review pipeline.
        create_requested=r.create_work or ev(r.event) in {'form_submitted_create_work','create_work','create_document'}
        if create_requested and not pages:
            if not request:return err('WORK_CREATION','The customer service request contains no usable information.',400,'EMPTY_WORK_REQUEST')
            made=await create_work(a,r,request,application); j=new_job(jid,r,request,made); started=start_review(jid)
            out=job_response(j);out.update({'reply':'Your request has been prepared and sent into document review.','created_work':True,'work_created':True,'review_started':started});return out
        # Any request carrying the authoritative pages is treated as document intake/review, regardless of button wording.
        if pages:
            j=_jobs.get(jid)
            if j is None:j=new_job(jid,r,request,r.document_pages or pages)
            else:
                t=_review_tasks.get(jid)
                if t and not t.done() and j.get('status')=='reviewing':return job_response(j)
                j.update({'document_pages':pages,'review_pages':review_pages(pages),'assembled_review':'','status':'reviewing','review_started':True,'review_finished':False,'review_error':None,'approved':False,'paid':False,'progress':{'completed':0,'total':len(pages)},'customer_id':r.customer_id,'service':r.service or j.get('service'),'original_request':request,'context':application})
            started=start_review(jid);out=job_response(j);out.update({'reply':'Your document has been received. It is now being reviewed page by page.','created_work':True,'review_started':started});return out
        if not r.message.strip():return err('CHAT','The chat message is empty.',400,'EMPTY_MESSAGE')
        reply=await asyncio.to_thread(a.respond,message=r.message.strip(),service=r.service,event=r.event,context=application)
        return {'success':True,'reply':str(reply or '').strip(),'job_id':jid,'service':r.service or a.service,'created_work':False}
    except Exception as e:return err('CHAT',e,500,'CHAT_ERROR')

@app.get('/api/review')
async def get_review(job_id:str):
    j=_jobs.get(job_id)
    if not j:return err('REVIEW','The requested review job does not exist.',404,'JOB_NOT_FOUND')
    start_review(job_id);return job_response(j)
@app.get('/api/review/pages')
async def get_pages(job_id:str):
    j=_jobs.get(job_id)
    if not j:return err('REVIEW_PAGES','The requested review job does not exist.',404,'JOB_NOT_FOUND')
    start_review(job_id);return {'success':True,'job_id':job_id,'current_version':j['current_version'],'version_id':j['version_id'],'status':j['status'],'total_pages':len(j['document_pages']),'pages':stored(j['document_pages']),'document_pages':stored(j['document_pages']),'review_pages':j['review_pages'],'progress':j['progress'],'approved':j['approved'],'paid':j['paid']}

@app.post('/api/correct')
async def correct(r:Correction):
    j=_jobs.get(r.job_id); instruction=r.instruction.strip()
    if not j:return err('CORRECTION','Job not found.',404,'JOB_NOT_FOUND')
    if not instruction:return err('CORRECTION','Correction instruction is empty.',400,'EMPTY_CORRECTION')
    if j.get('status') in {'reviewing','correcting'}:return err('CORRECTION','The document is still being processed.',409,'DOCUMENT_STILL_PROCESSING')
    if not j.get('document_pages'):return err('CORRECTION','There is no document available for correction.',409,'NO_DOCUMENT')
    j['current_version']+=1;j['version_id']=f"{r.job_id}:{j['current_version']}";j.update({'status':'correcting','approved':False,'paid':False,'review_started':False,'review_finished':False,'review_error':None,'correction_instruction':instruction,'progress':{'completed':0,'total':len(j['document_pages'])}})
    async def worker():
        try:
            a=session(j.get('customer_id'),r.job_id,j.get('service'))
            result=await asyncio.to_thread(a.correct_document,document_pages=stored(j['document_pages']),correction=instruction,service=j.get('service'),context=j.get('context'),progress_callback=None)
            pages=generated_pages(result);j['document_pages']=pages;j['review_pages']=review_pages(pages);j['status']='reviewing';j['review_started']=True;j['progress']={'completed':0,'total':len(pages)};start_review(r.job_id)
        except Exception as e:j['status']='correction_error';j['review_error']={'type':type(e).__name__,'message':str(e)};traceback.print_exc()
    old=_correction_tasks.get(r.job_id)
    if old and not old.done():old.cancel()
    _correction_tasks[r.job_id]=asyncio.create_task(worker())
    return {'success':True,'job_id':r.job_id,'status':'correcting','version_id':j['version_id'],'current_version':j['current_version'],'message':'Correction has started. The corrected document will be reviewed again.'}

@app.post('/api/approve')
async def approve(r:Approval):
    j=_jobs.get(r.job_id)
    if not j:return err('APPROVAL','Job not found.',404,'JOB_NOT_FOUND')
    if r.version_id!=j['version_id']:return err('APPROVAL','The supplied document version does not match.',409,'VERSION_MISMATCH')
    if j['status']!='review_complete':return err('APPROVAL','The document review is not complete.',409,'REVIEW_NOT_COMPLETE')
    j['approved']=True;j['status']='approved';return {'success':True,'job_id':r.job_id,'version_id':r.version_id,'current_version':j['current_version'],'approved':True,'status':'approved','total_pages':len(j['document_pages']),'pages':j['document_pages'],'payment_url':f"/payment.html?job_id={r.job_id}&version_id={r.version_id}"}

@app.post('/api/payment/complete')
async def payment_complete(job_id:str,version_id:str):
    j=_jobs.get(job_id)
    if not j:return err('PAYMENT','Job not found.',404,'JOB_NOT_FOUND')
    if version_id!=j['version_id']:return err('PAYMENT','Version mismatch.',409,'VERSION_MISMATCH')
    if not j['approved']:return err('PAYMENT','The document must be approved before payment.',409,'DOCUMENT_NOT_APPROVED')
    j['paid']=True;j['status']='paid';return {'success':True,'job_id':job_id,'version_id':version_id,'paid':True,'status':'paid','total_pages':len(j['document_pages']),'download_url':f"/download.html?job_id={job_id}&version_id={version_id}",'api_download_url':f"/api/download?job_id={job_id}&version_id={version_id}"}
@app.get('/api/payment')
async def payment(job_id:str,version_id:str):
    j=_jobs.get(job_id)
    if not j:return err('PAYMENT_STATE','Job not found.',404,'JOB_NOT_FOUND')
    if version_id!=j['version_id']:return err('PAYMENT_STATE','Version mismatch.',409,'VERSION_MISMATCH')
    return {'success':True,'job_id':job_id,'version_id':version_id,'status':j['status'],'approved':j['approved'],'paid':j['paid'],'total_pages':len(j['document_pages']),'payment_complete':j['paid']}
@app.get('/api/download')
async def download(job_id:str,version_id:str):
    j=_jobs.get(job_id)
    if not j:return err('DOWNLOAD','Job not found.',404,'JOB_NOT_FOUND')
    if version_id!=j['version_id']:return err('DOWNLOAD','Version mismatch.',409,'VERSION_MISMATCH')
    if not j['approved']:return err('DOWNLOAD','The current document version has not been approved.',409,'DOCUMENT_NOT_APPROVED')
    if not j['paid']:return err('DOWNLOAD','Payment for the current document version has not been completed.',409,'PAYMENT_NOT_COMPLETED')
    return {'success':True,'job_id':job_id,'version_id':version_id,'status':'paid','total_pages':len(j['document_pages']),'pages':j['document_pages'],'document_pages':j['document_pages'],'message':'The approved and paid document is ready for final document generation.'}
@app.post('/api/chat/clear')
async def clear(customer_id:str|None=None,job_id:str|None=None):
    a=_sessions.get(key(customer_id,job_id))
    if a:a.clear_history()
    return {'success':True,'message':'Conversation cleared.'}

@app.on_event('startup')
async def startup():
    print('='*70);print('NAIJA POCKET BUSINESS CENTER — FASTAPI');print('AdaResponse:',get_ada_model(),'configured=',is_configured());print('Complete page workflow: ENABLED');print('Keyword intelligence: DISABLED');print('='*70)
if __name__=='__main__':
    import uvicorn
    uvicorn.run('ada_api:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')),reload=False)
