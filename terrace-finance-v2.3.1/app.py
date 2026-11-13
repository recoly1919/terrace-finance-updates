"""Terrace Investment Manager — local-first FastAPI + SQLite prototype."""
import os, sqlite3, secrets, hashlib, hmac, json, uuid, mimetypes, io, zipfile, shutil, csv, re
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TERRACE_DATA_DIR', ROOT / 'data'))
UPLOADS = DATA / 'uploads'
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(parents=True, exist_ok=True)
DB = DATA / 'terrace.db'
APP = FastAPI(title='Terrace Finance API', docs_url=None, redoc_url=None)
APP.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
CATEGORIES = ['construction','kitchen','furniture','rent','licenses','staff','marketing','logistics','utilities','other']

def conn():
    c=sqlite3.connect(DB, timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=5000')
    return c

def init():
    with conn() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, fullname TEXT NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'owner', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expiry TEXT NOT NULL, csrf TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT DEFAULT '', phone TEXT DEFAULT '', wechat TEXT DEFAULT '', notes TEXT DEFAULT '', created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, date TEXT NOT NULL, amount_fen INTEGER NOT NULL CHECK(amount_fen>=0), supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL, notes TEXT DEFAULT '', created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY, expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE, amount_fen INTEGER NOT NULL CHECK(amount_fen>0), date TEXT NOT NULL, method TEXT NOT NULL, paid_by TEXT DEFAULT '', notes TEXT DEFAULT '', created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS budgets (category TEXT PRIMARY KEY, amount_fen INTEGER NOT NULL CHECK(amount_fen>=0), updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, expense_id INTEGER REFERENCES expenses(id) ON DELETE SET NULL, filename TEXT NOT NULL, path TEXT NOT NULL UNIQUE, mime TEXT NOT NULL, bytes INTEGER NOT NULL, uploaded_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
        CREATE INDEX IF NOT EXISTS idx_payments_expense ON payments(expense_id);
        CREATE INDEX IF NOT EXISTS idx_documents_expense ON documents(expense_id);
        ''')
init()
# Non-destructive migration: existing rows, passwords and sessions remain untouched.
def migrate():
    with conn() as c:
        existing={r['name'] for r in c.execute('PRAGMA table_info(expenses)')}
        for name,decl in {'subcategory':"TEXT DEFAULT ''",'due_date':"TEXT DEFAULT ''",'reference':"TEXT DEFAULT ''",'purchase_status':"TEXT NOT NULL DEFAULT 'planned'",'delivery_date':"TEXT DEFAULT ''",'delivery_status':"TEXT NOT NULL DEFAULT 'pending'",'transaction_kind':"TEXT NOT NULL DEFAULT 'service'"}.items():
            if name not in existing:c.execute(f'ALTER TABLE expenses ADD COLUMN {name} {decl}')
        c.execute('CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, record_type TEXT NOT NULL, record_id INTEGER, details TEXT, created_at TEXT NOT NULL)')
migrate()

def audit(c, s,action,kind,rid,detail=''):
    c.execute('INSERT INTO audit_log(user_id,action,record_type,record_id,details,created_at) VALUES(?,?,?,?,?,?)',(s['user_id'],action,kind,rid,detail,now()))

def now(): return datetime.now(timezone.utc).isoformat()
def hash_pass(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),310000).hex()
def money(n):
    try:
        from decimal import Decimal, ROUND_HALF_UP
        v=Decimal(str(n))
        if not v.is_finite() or v<0 or v>9999999999: raise ValueError()
        return int((v*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except Exception: raise HTTPException(422,'Invalid RMB amount')
def d(row):return dict(row) if row else None

def auth(req:Request, csrf=False):
    token=req.cookies.get('terrace_session')
    if not token:raise HTTPException(401,'Login required')
    with conn() as c:
        s=c.execute('SELECT s.*, u.username,u.fullname,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expiry>?',(hashlib.sha256(token.encode()).hexdigest(),now())).fetchone()
    if not s:raise HTTPException(401,'Session expired')
    if csrf and req.headers.get('x-csrf-token')!=s['csrf']:raise HTTPException(403,'CSRF token missing')
    return s

class Login(BaseModel):
    username:str
    password:str
class Signup(Login):
    fullname:str
class Supplier(BaseModel):
    name:str=Field(min_length=1,max_length=180)
    contact:str='';phone:str='';wechat:str='';notes:str=''
class Expense(BaseModel):
    title:str=Field(min_length=1,max_length=240)
    category:str
    date:str
    amount:float
    supplier_id:Optional[int]=None
    notes:str=''
    subcategory:str=''
    due_date:str=''
    reference:str=''
    purchase_status:str='planned'
    delivery_date:str=''
    delivery_status:str='pending'
    transaction_kind:str='service'
class Payment(BaseModel):
    expense_id:int
    amount:float
    date:str
    method:str
    paid_by:str=''
    notes:str=''
class Budget(BaseModel):
    category:str
    amount:float

def purchase_ok(x):
    if x.transaction_kind not in ('service','product','order'):raise HTTPException(422,'Invalid transaction kind')
    if x.purchase_status not in ('planned','ordered','cancelled'):raise HTTPException(422,'Invalid purchase status')
    if x.delivery_status not in ('pending','partial','delivered','na'):raise HTTPException(422,'Invalid delivery status')
    if x.delivery_date:date_ok(x.delivery_date)

def date_ok(v):
    try:datetime.strptime(v,'%Y-%m-%d')
    except:raise HTTPException(422,'Use YYYY-MM-DD')

def login_response(user):
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
    with conn() as c:c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),csrf))
    resp=JSONResponse({'ok':True,'user':{'id':user['id'],'fullname':user['fullname'],'username':user['username'],'role':user['role']},'csrf':csrf})
    resp.set_cookie('terrace_session',token,httponly=True,secure=os.environ.get('TERRACE_HTTPS','')=='1',samesite='lax',max_age=604800,path='/')
    return resp

@APP.get('/')
def index():return FileResponse(ROOT/'static'/'index.html',headers={'Cache-Control':'no-store, max-age=0'})
@APP.get('/api/bootstrap')
def bootstrap():
    with conn() as c:n=c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    return {'needs_setup':n==0}
@APP.post('/api/setup')
def setup(x:Signup):
    if len(x.password)<10:raise HTTPException(422,'Password must have 10+ characters')
    if not x.username.strip() or not x.fullname.strip():raise HTTPException(422,'Missing fields')
    with conn() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:raise HTTPException(403,'Already configured')
        salt=secrets.token_hex(16)
        c.execute('INSERT INTO users(username,fullname,salt,digest,role,created_at) VALUES(?,?,?,?,?,?)',(x.username.strip(),x.fullname.strip(),salt,hash_pass(x.password,salt),'owner',now()))
        u=c.execute('SELECT * FROM users WHERE username=?',(x.username.strip(),)).fetchone()
    return login_response(u)
@APP.post('/api/login')
def login(x:Login):
    with conn() as c:u=c.execute('SELECT * FROM users WHERE username=?',(x.username.strip(),)).fetchone()
    if not u or not hmac.compare_digest(hash_pass(x.password,u['salt']),u['digest']):raise HTTPException(401,'Invalid credentials')
    return login_response(u)
@APP.post('/api/logout')
def logout(req:Request):
    s=auth(req,True)
    with conn() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(s['token_hash'],))
    r=JSONResponse({'ok':True});r.delete_cookie('terrace_session');return r
@APP.get('/api/me')
def me(req:Request):
    s=auth(req)
    return {'id':s['user_id'],'fullname':s['fullname'],'username':s['username'],'role':s['role'],'csrf':s['csrf']}
@APP.get('/api/users')
def users(req:Request):
    auth(req)
    with conn() as c:return [d(x) for x in c.execute('SELECT id,username,fullname,role,created_at FROM users ORDER BY id')]
@APP.post('/api/users')
def add_user(x:Signup,req:Request):
    s=auth(req,True)
    if s['role']!='owner':raise HTTPException(403,'Owner only')
    if len(x.password)<10:raise HTTPException(422,'Password must have 10+ characters')
    salt=secrets.token_hex(16)
    try:
        with conn() as c:
            cur=c.execute('INSERT INTO users(username,fullname,salt,digest,role,created_at) VALUES(?,?,?,?,?,?)',(x.username.strip(),x.fullname.strip(),salt,hash_pass(x.password,salt),'partner',now()))
            audit(c,s,'create','users',cur.lastrowid,x.fullname.strip())
            return {'id':cur.lastrowid}
    except sqlite3.IntegrityError:raise HTTPException(409,'Username already exists')
@APP.get('/api/data')
def data(req:Request):
    auth(req)
    with conn() as c:
        return {'suppliers':[d(x) for x in c.execute('SELECT * FROM suppliers ORDER BY id DESC')],
                'expenses':[d(x) for x in c.execute('SELECT e.*,s.name supplier_name,coalesce((SELECT SUM(amount_fen) FROM payments WHERE expense_id=e.id),0) paid_fen FROM expenses e LEFT JOIN suppliers s ON s.id=e.supplier_id ORDER BY e.date DESC,e.id DESC')],
                'payments':[d(x) for x in c.execute('SELECT p.*,e.title expense_title FROM payments p JOIN expenses e ON e.id=p.expense_id ORDER BY p.date DESC,p.id DESC')],
                'budgets':[d(x) for x in c.execute('SELECT * FROM budgets')],
                'documents':[d(x) for x in c.execute('SELECT id,expense_id,filename,mime,bytes,created_at FROM documents ORDER BY id DESC')]}

@APP.get('/api/activity')
def activity(req:Request):
    auth(req)
    with conn() as c:
        return [d(x) for x in c.execute('''SELECT a.id,a.action,a.record_type,a.record_id,a.details,a.created_at,u.fullname actor,
            CASE WHEN a.record_type='expenses' THEN e.title WHEN a.record_type='payments' THEN pe.title WHEN a.record_type='suppliers' THEN su.name END record_name,
            CASE WHEN a.record_type='payments' THEN p.amount_fen END payment_amount_fen,
            CASE WHEN a.record_type='payments' THEN p.date END payment_date
            FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
            LEFT JOIN expenses e ON a.record_type='expenses' AND e.id=a.record_id
            LEFT JOIN payments p ON a.record_type='payments' AND p.id=a.record_id
            LEFT JOIN expenses pe ON pe.id=p.expense_id
            LEFT JOIN suppliers su ON a.record_type='suppliers' AND su.id=a.record_id
            ORDER BY a.id DESC LIMIT 10000''')]

@APP.post('/api/suppliers')
def supplier_add(x:Supplier,req:Request):
    s=auth(req,True)
    with conn() as c:
        cur=c.execute('INSERT INTO suppliers(name,contact,phone,wechat,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(x.name.strip(),x.contact,x.phone,x.wechat,x.notes,s['user_id'],now()))
        audit(c,s,'create','suppliers',cur.lastrowid)
        return {'id':cur.lastrowid}
@APP.post('/api/expenses')
def expense_add(x:Expense,req:Request):
    s=auth(req,True);date_ok(x.date);purchase_ok(x)
    if x.category not in CATEGORIES:raise HTTPException(422,'Invalid category')
    with conn() as c:
        if x.supplier_id and not c.execute('SELECT id FROM suppliers WHERE id=?',(x.supplier_id,)).fetchone():raise HTTPException(422,'Supplier not found')
        if x.due_date: date_ok(x.due_date)
        cur=c.execute('INSERT INTO expenses(title,category,date,amount_fen,supplier_id,notes,created_by,created_at,subcategory,due_date,reference,purchase_status,delivery_date,delivery_status,transaction_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x.title.strip(),x.category,x.date,money(x.amount),x.supplier_id,x.notes,s['user_id'],now(),x.subcategory.strip(),x.due_date,x.reference.strip(),x.purchase_status,x.delivery_date,x.delivery_status,x.transaction_kind))
        audit(c,s,'create','expenses',cur.lastrowid,x.title)
        return {'id':cur.lastrowid}
@APP.put('/api/expenses/{id}')
def expense_update(id:int,x:Expense,req:Request):
    s=auth(req,True);date_ok(x.date);purchase_ok(x)
    if x.due_date:date_ok(x.due_date)
    if x.category not in CATEGORIES:raise HTTPException(422,'Invalid category')
    with conn() as c:
        old=c.execute('SELECT * FROM expenses WHERE id=?',(id,)).fetchone()
        if not old:raise HTTPException(404,'Expense not found')
        if x.supplier_id and not c.execute('SELECT 1 FROM suppliers WHERE id=?',(x.supplier_id,)).fetchone():raise HTTPException(422,'Supplier not found')
        new_amount=money(x.amount)
        paid=c.execute('SELECT COALESCE(SUM(amount_fen),0) FROM payments WHERE expense_id=?',(id,)).fetchone()[0]
        if new_amount<paid:raise HTTPException(422,'New amount is less than payments already recorded')
        if x.purchase_status=='cancelled' and paid:raise HTTPException(422,'Paid purchases cannot be cancelled; first resolve payments/refunds')
        c.execute('UPDATE expenses SET title=?,category=?,date=?,amount_fen=?,supplier_id=?,notes=?,subcategory=?,due_date=?,reference=?,purchase_status=?,delivery_date=?,delivery_status=?,transaction_kind=? WHERE id=?',(x.title.strip(),x.category,x.date,new_amount,x.supplier_id,x.notes,x.subcategory.strip(),x.due_date,x.reference.strip(),x.purchase_status,x.delivery_date,x.delivery_status,x.transaction_kind,id))
        audit(c,s,'update','expenses',id,json.dumps(dict(old),ensure_ascii=False))
    return {'ok':True}

@APP.put('/api/suppliers/{id}')
def supplier_update(id:int,x:Supplier,req:Request):
    s=auth(req,True)
    with conn() as c:
        old=c.execute('SELECT * FROM suppliers WHERE id=?',(id,)).fetchone()
        if not old:raise HTTPException(404,'Not found')
        c.execute('UPDATE suppliers SET name=?,contact=?,phone=?,wechat=?,notes=? WHERE id=?',(x.name.strip(),x.contact,x.phone,x.wechat,x.notes,id))
        audit(c,s,'update','suppliers',id,json.dumps(dict(old),ensure_ascii=False))
    return {'ok':True}

@APP.put('/api/payments/{id}')
def payment_update(id:int,x:Payment,req:Request):
    s=auth(req,True);date_ok(x.date);amt=money(x.amount)
    if not amt:raise HTTPException(422,'Zero payment')
    if x.method not in ['wechat','alipay','bank','cash','other']:raise HTTPException(422,'Invalid method')
    with conn() as c:
        old=c.execute('SELECT * FROM payments WHERE id=?',(id,)).fetchone()
        if not old:raise HTTPException(404,'Not found')
        ex=c.execute('SELECT amount_fen,purchase_status FROM expenses WHERE id=?',(x.expense_id,)).fetchone()
        if not ex:raise HTTPException(404,'Expense not found')
        if ex['purchase_status']=='cancelled':raise HTTPException(422,'Cannot pay cancelled purchase')
        others=c.execute('SELECT COALESCE(SUM(amount_fen),0) FROM payments WHERE expense_id=? AND id<>?',(x.expense_id,id)).fetchone()[0]
        if others+amt>ex['amount_fen']:raise HTTPException(422,'Payment exceeds expense')
        c.execute('UPDATE payments SET expense_id=?,amount_fen=?,date=?,method=?,paid_by=?,notes=? WHERE id=?',(x.expense_id,amt,x.date,x.method,x.paid_by,x.notes,id))
        audit(c,s,'update','payments',id,json.dumps(dict(old),ensure_ascii=False))
    return {'ok':True}

@APP.delete('/api/expenses/{id}')
def expense_delete(id:int,req:Request):
    s=auth(req,True)
    with conn() as c:
        if c.execute('SELECT COUNT(*) FROM documents WHERE expense_id=?',(id,)).fetchone()[0]:raise HTTPException(409,'Remove linked documents first')
        old_title=(c.execute('SELECT title FROM expenses WHERE id=?',(id,)).fetchone() or {'title':'İşlem'})['title']
        cur=c.execute('DELETE FROM expenses WHERE id=?',(id,))
        if not cur.rowcount:raise HTTPException(404,'Not found')
        audit(c,s,'delete','expenses',id,old_title)
    return {'ok':True}
@APP.post('/api/payments')
def payment_add(x:Payment,req:Request):
    s=auth(req,True);date_ok(x.date);amt=money(x.amount)
    if amt==0:raise HTTPException(422,'Zero payment')
    if x.method not in ['wechat','alipay','bank','cash','other']:raise HTTPException(422,'Invalid method')
    with conn() as c:
        ex=c.execute('SELECT amount_fen,purchase_status FROM expenses WHERE id=?',(x.expense_id,)).fetchone()
        if not ex:raise HTTPException(404,'Expense not found')
        if ex['purchase_status']=='cancelled':raise HTTPException(422,'Cannot pay cancelled purchase')
        paid=c.execute('SELECT coalesce(sum(amount_fen),0) FROM payments WHERE expense_id=?',(x.expense_id,)).fetchone()[0]
        if paid+amt>ex['amount_fen']:raise HTTPException(422,'Payment exceeds expense')
        cur=c.execute('INSERT INTO payments(expense_id,amount_fen,date,method,paid_by,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(x.expense_id,amt,x.date,x.method,x.paid_by,x.notes,s['user_id'],now()))
        audit(c,s,'create','payments',cur.lastrowid, f'{ex["amount_fen"] / 100:g} RMB toplamlı işlem için {amt/100:g} RMB ödeme')
        return {'id':cur.lastrowid}
@APP.delete('/api/payments/{id}')
def payment_delete(id:int,req:Request):
    s=auth(req,True)
    with conn() as c:
        old_title=(c.execute('SELECT e.title FROM payments p JOIN expenses e ON e.id=p.expense_id WHERE p.id=?',(id,)).fetchone() or {'title':'Ödeme'})['title']
        if not c.execute('DELETE FROM payments WHERE id=?',(id,)).rowcount:raise HTTPException(404,'Not found')
        audit(c,s,'delete','payments',id,old_title)
    return {'ok':True}
@APP.post('/api/budgets')
def budget_upsert(x:Budget,req:Request):
    s=auth(req,True)
    if x.category not in CATEGORIES:raise HTTPException(422,'Invalid category')
    with conn() as c:
        prior=c.execute('SELECT category FROM budgets WHERE category=?',(x.category,)).fetchone()
        c.execute('INSERT INTO budgets(category,amount_fen,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(category) DO UPDATE SET amount_fen=excluded.amount_fen,updated_by=excluded.updated_by,updated_at=excluded.updated_at',(x.category,money(x.amount),s['user_id'],now()))
        audit(c,s,'update' if prior else 'create','budgets',None,x.category)
    return {'ok':True}
@APP.post('/api/documents')
async def upload(req:Request, file:UploadFile=File(...),expense_id:Optional[int]=None):
    s=auth(req,True)
    if expense_id:
        with conn() as c:
            if not c.execute('SELECT 1 FROM expenses WHERE id=?',(expense_id,)).fetchone():raise HTTPException(404,'Expense not found')
    allowed={'.pdf','.png','.jpg','.jpeg','.webp','.xlsx','.xls','.docx','.txt'}
    original=Path(file.filename or 'document').name
    ext=Path(original).suffix.lower()
    if ext not in allowed:raise HTTPException(422,'File type not supported')
    blob=await file.read(10*1024*1024+1)
    if len(blob)>10*1024*1024:raise HTTPException(413,'Max 10 MB')
    fname=uuid.uuid4().hex+ext
    (UPLOADS/fname).write_bytes(blob)
    with conn() as c:
        cur=c.execute('INSERT INTO documents(expense_id,filename,path,mime,bytes,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?)',(expense_id,original,fname,mimetypes.guess_type(original)[0] or 'application/octet-stream',len(blob),s['user_id'],now()))
        audit(c,s,'create','documents',cur.lastrowid,original)
        return {'id':cur.lastrowid}
@APP.get('/api/documents/{id}')
def download(id:int,req:Request):
    auth(req)
    with conn() as c: doc=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone()
    if not doc:raise HTTPException(404,'Not found')
    return FileResponse(UPLOADS/doc['path'],filename=doc['filename'],media_type='application/octet-stream')
@APP.delete('/api/documents/{id}')
def delete_document(id:int,req:Request):
    s=auth(req,True)
    with conn() as c:
        doc=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone()
        if not doc:raise HTTPException(404,'Not found')
        c.execute('DELETE FROM documents WHERE id=?',(id,))
        audit(c,s,'delete','documents',id,doc['filename'])
    (UPLOADS/doc['path']).unlink(missing_ok=True)
    return {'ok':True}
@APP.get('/api/export')
def export(req:Request):
    auth(req)
    with conn() as c:
        result={'exported_at':now(),'currency':'CNY', 'suppliers':[d(x) for x in c.execute('SELECT * FROM suppliers')], 'expenses':[d(x) for x in c.execute('SELECT * FROM expenses')], 'payments':[d(x) for x in c.execute('SELECT * FROM payments')], 'budgets':[d(x) for x in c.execute('SELECT * FROM budgets')], 'documents_metadata':[d(x) for x in c.execute('SELECT id,expense_id,filename,mime,bytes,created_at FROM documents')]}
    return JSONResponse(result,headers={'Content-Disposition':'attachment; filename="terrace-export.json"'})


# Excel XLSX without additional package dependencies: standard OpenXML container.
def xlsx_bytes(sheets):
    """Styled, dependency-free XLSX for Python 3.15: branded report + frozen data tables."""
    NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    def col(n):
        v=''
        while n:n,r=divmod(n-1,26);v=chr(65+r)+v
        return v
    def text_cell(addr,value,style=0):
        v=str(value if value is not None else '')
        # Escape formula-looking user content to ensure data is never interpreted as a formula
        return f'<c r="{addr}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(v)}</t></is></c>'
    def num_cell(addr,value,style=4):
        return f'<c r="{addr}" s="{style}" t="n"><v>{value}</v></c>'
    def fmt_cell(addr,value,style=0,financial=False):
        return num_cell(addr,value, 4 if financial else 5) if isinstance(value,(float,int)) and not isinstance(value,bool) else text_cell(addr,value,style)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>''' +''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>')
        z.writestr('_rels/.rels','<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        wb='<?xml version="1.0" encoding="utf-8"?><workbook xmlns="'+NS+'" xmlns:r="'+rel+'"><sheets>'+''.join(f'<sheet name="{xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_,_) in enumerate(sheets,1))+'</sheets></workbook>'
        z.writestr('xl/workbook.xml',wb)
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+'<Relationship Id="rIdStyles" Type="'+rel+'/styles" Target="styles.xml"/></Relationships>')
        # styles: 0 body / 1 title / 2 subtitle / 3 column header / 4 currency / 5 integer / 6 alternating / 7 negative / 8 highlight / 9  alternating-currency / 10 positive
        fonts='<fonts count="5"><font><sz val="11"/><name val="Aptos"/><color rgb="FF273D41"/></font><font><b/><sz val="20"/><name val="Aptos Display"/><color rgb="FFFFFFFF"/></font><font><sz val="10"/><name val="Aptos"/><color rgb="FFD5E9E3"/></font><font><b/><sz val="10"/><name val="Aptos"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="12"/><name val="Aptos"/><color rgb="FF176D62"/></font></fonts>'
        fills='<fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF18383C"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1C7569"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF1F7F5"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE6F3ED"/></patternFill></fill></fills>'
        borders='<borders count="2"><border/><border><bottom style="hair"><color rgb="FFE0E8E6"/></bottom></border></borders>'
        nfmt='<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;¥&quot;#,##0.00;[Red](&quot;¥&quot;#,##0.00)"/></numFmts>'
        def xf(font=0,fill=0,border=0,fmt=0,align=None):
            return f'<xf numFmtId="{fmt}" fontId="{font}" fillId="{fill}" borderId="{border}" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1">'+(f'<alignment horizontal="{align}" vertical="center"/>' if align else '<alignment vertical="center"/>')+'</xf>'
        xfs='<cellXfs count="11">'+''.join([xf(),xf(1,2),xf(2,2),xf(3,3),xf(0,0,1,164,'right'),xf(0,0,1,1,'right'),xf(0,4,1),xf(4,5,1),xf(4,5,1,164,'right'),xf(0,4,1,164,'right'),xf(4,0,1,164,'right')])+'</cellXfs>'
        styles='<?xml version="1.0" encoding="utf-8"?><styleSheet xmlns="'+NS+'">'+nfmt+fonts+fills+borders+'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'+xfs+'</styleSheet>'
        z.writestr('xl/styles.xml',styles)
        financial_col={'Özet':{2},'Giderler':{7,8,9},'Ödemeler':{5},'Bütçeler':{2,3,4}}
        for i,(name,headers,rows) in enumerate(sheets,1):
            count=len(headers);last=col(count)
            xml=['<?xml version="1.0" encoding="utf-8"?><worksheet xmlns="'+NS+'"><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="5" topLeftCell="A6" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/><cols>']
            widths={'Özet':[38,23],'Giderler':[9,16,38,24,23,30,20,20,20,20,18,24,50],'Ödemeler':[9,16,12,40,20,22,25,45],'Bütçeler':[30,22,22,25],'Tedarikçiler':[9,37,24,22,25,46]}
            for j in range(1,count+1):xml.append(f'<col min="{j}" max="{j}" width="{(widths.get(name,[])+[20]*count)[j-1]}" customWidth="1"/>')
            xml.append('</cols><sheetData>')
            xml.append('<row r="1" ht="38" customHeight="1">'+text_cell('A1','TERRACE  /  FINANCE',1)+'</row>')
            xml.append('<row r="2" ht="25" customHeight="1">'+text_cell('A2',name.upper()+'  ·  AÇILIŞ ÖNCESİ YATIRIM RAPORU',2)+'</row>')
            xml.append('<row r="3" ht="24" customHeight="1">'+text_cell('A3','Terrace Anatolian Cuisine  •  RMB / CNY  •  '+datetime.now().strftime('%d.%m.%Y'),6)+'</row>')
            xml.append('<row r="4" ht="10" customHeight="1"/>')
            xml.append('<row r="5" ht="29" customHeight="1">'+''.join(text_cell(col(j)+"5",h,3) for j,h in enumerate(headers,1))+'</row>')
            for ridx,values in enumerate(rows,6):
                xml.append(f'<row r="{ridx}" ht="23" customHeight="1">')
                for j,val in enumerate(values,1):
                    addr=col(j)+str(ridx);financial=j in financial_col.get(name,set()) and isinstance(val,(int,float))
                    base=6 if ridx%2 else 0
                    style=(9 if ridx%2 else 4) if financial else base
                    if name=='Özet' and j==2 and isinstance(val,(float,int)) and ridx<=10:style=8
                    if name=='Bütçeler' and j==4 and isinstance(val,(float,int)) and val<0:style=7
                    if isinstance(val,(float,int)) and not isinstance(val,bool):xml.append(num_cell(addr,val,style if financial else 5))
                    else:xml.append(text_cell(addr,val,style))
                xml.append('</row>')
            xml.append('</sheetData>')
            for a,b in [(1,1),(2,2)]:
                if count>1:xml.append(f'<mergeCells count="2"><mergeCell ref="A1:{last}1"/><mergeCell ref="A2:{last}2"/></mergeCells>');break
            if name!='Özet':xml.append(f'<autoFilter ref="A5:{last}{max(5,len(rows)+5)}"/>')
            xml.append('<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/></worksheet>')
            z.writestr(f'xl/worksheets/sheet{i}.xml',''.join(xml))
    return out.getvalue()

@APP.get('/api/export/excel')
def export_excel(req:Request):
    auth(req)
    with conn() as c:
        expenses=c.execute("SELECT e.*,COALESCE(s.name,'') supplier_name,COALESCE((SELECT SUM(amount_fen) FROM payments WHERE expense_id=e.id),0) paid_fen FROM expenses e LEFT JOIN suppliers s ON s.id=e.supplier_id ORDER BY e.date,e.id").fetchall()
        payments=c.execute('SELECT p.*,e.title expense_title FROM payments p JOIN expenses e ON e.id=p.expense_id ORDER BY p.date,p.id').fetchall()
        budgets=c.execute('SELECT * FROM budgets').fetchall()
        suppliers=c.execute('SELECT * FROM suppliers').fetchall()
    cats={'construction':'İnşaat ve dekorasyon','kitchen':'Mutfak ekipmanı','furniture':'Mobilya','rent':'Kira / depozito','licenses':'Ruhsat ve danışmanlık','staff':'Personel ve eğitim','marketing':'Pazarlama ve marka','logistics':'Nakliye ve gümrük','utilities':'Altyapı ve faturalar','other':'Diğer'}
    methods={'wechat':'WeChat Pay','alipay':'Alipay','bank':'Banka transferi','cash':'Nakit','other':'Diğer'}
    active_expenses=[x for x in expenses if x['purchase_status']!='cancelled']
    total=sum(x['amount_fen'] for x in active_expenses);paid=sum(x['amount_fen'] for x in payments if any(e['id']==x['expense_id'] for e in active_expenses));budget=sum(x['amount_fen'] for x in budgets)
    sheets=[('Özet',['Gösterge','Tutar (RMB)'],[['Toplam yatırım taahhüdü',total/100],['Ödenen',paid/100],['Kalan ödeme',(total-paid)/100],['Toplam bütçe',budget/100],['Bütçe farkı',(budget-total)/100],['Rapor tarihi',now()[:10]]]),
        ('Giderler',['ID','Tarih','Açıklama','Kategori','Alt kategori','Tedarikçi','Toplam (RMB)','Ödenen (RMB)','Kalan (RMB)','Durum','Vade tarihi','Referans','Notlar'],[[e['id'],e['date'],e['title'],cats.get(e['category'],e['category']),e['subcategory'],e['supplier_name'],e['amount_fen']/100,e['paid_fen']/100,(e['amount_fen']-e['paid_fen'])/100,'Ödendi' if e['paid_fen']>=e['amount_fen'] else 'Kısmen ödendi' if e['paid_fen'] else 'Ödenmedi',e['due_date'],e['reference'],e['notes']] for e in expenses]),
        ('Ödemeler',['ID','Tarih','Gider ID','Gider','Tutar (RMB)','Ödeme yöntemi','Ödemeyi yapan','Notlar'],[[p['id'],p['date'],p['expense_id'],p['expense_title'],p['amount_fen']/100,methods.get(p['method'],p['method']),p['paid_by'],p['notes']] for p in payments]),
        ('Bütçeler',['Kategori','Bütçe (RMB)','Taahhüt (RMB)','Kalan bütçe (RMB)'],[[cats.get(b['category'],b['category']),b['amount_fen']/100,sum(e['amount_fen'] for e in active_expenses if e['category']==b['category'])/100,(b['amount_fen']-sum(e['amount_fen'] for e in active_expenses if e['category']==b['category']))/100] for b in budgets]),
        ('Tedarikçiler',['ID','Firma','Yetkili','Telefon','WeChat','Notlar'],[[s['id'],s['name'],s['contact'],s['phone'],s['wechat'],s['notes']] for s in suppliers])]
    data=xlsx_bytes(sheets)
    return StreamingResponse(io.BytesIO(data),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="Terrace_Finance_Rapor.xlsx"','Cache-Control':'no-store'})

@APP.get('/api/backup')
def full_backup(req:Request):
    s=auth(req)
    if s['role']!='owner':raise HTTPException(403,'Owner only')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        with conn() as src:
            temp=io.BytesIO()
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                path=Path(td)/'terrace.db'
                with sqlite3.connect(path) as dst:src.backup(dst)
                z.write(path,'data/terrace.db')
        for f in UPLOADS.iterdir():
            if f.is_file():z.write(f,'data/uploads/'+f.name)
        z.writestr('BACKUP_INFO.txt','Terrace Finance complete backup. Restore the data folder only while the application is stopped.\n')
    out.seek(0)
    return StreamingResponse(out,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="Terrace_Tam_Yedek.zip"','Cache-Control':'no-store'})

# V2.3: Local-only, owner-only signed update management.
import update_service as updates

class UpdateConfig(BaseModel):
    feed_url: str = Field(min_length=10, max_length=1500)
    public_key: str = Field(min_length=40, max_length=150)

class UpdateRollback(BaseModel):
    backup_id: str = Field(min_length=12, max_length=80)


def update_owner(req: Request, write=False):
    session = auth(req, csrf=write)
    if session['role'] != 'owner':
        raise HTTPException(403, 'Yalnızca yönetici güncelleme işlemi yapabilir')
    return session


def update_call(call):
    try:
        return call()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

@APP.get('/api/health')
def health():
    return {'ok': True, 'version': updates.version()}

@APP.get('/api/update/status')
def update_status(req: Request):
    update_owner(req)
    return update_call(updates.status)

@APP.post('/api/update/config')
def update_config(x: UpdateConfig, req: Request):
    update_owner(req, True)
    return update_call(lambda: updates.set_config(x.feed_url.strip(), x.public_key.strip()))

@APP.post('/api/update/check')
def update_check(req: Request):
    update_owner(req, True)
    return update_call(updates.check_release)

@APP.post('/api/update/apply')
def update_apply(req: Request):
    update_owner(req, True)
    def apply():
        # One click: signed manifest → verified archive → supervisor restart.
        result = updates.check_release()
        if not result['available']:
            return {'available': False, 'version': updates.version()}
        updates.stage_release()
        return updates.request_install()
    return update_call(apply)

@APP.post('/api/update/rollback')
def update_rollback(x: UpdateRollback, req: Request):
    update_owner(req, True)
    return update_call(lambda: updates.request_rollback(x.backup_id))
