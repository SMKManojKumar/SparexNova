"""SPREX NOVA — Flask Application"""
import os,json,uuid
from datetime import datetime
from functools import wraps
from flask import Flask,render_template,redirect,url_for,request,session,flash,jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash,check_password_hash
from werkzeug.utils import secure_filename

BASE=os.path.abspath(os.path.dirname(__file__))
UPDIR=os.path.join(BASE,"uploads"); EXTS={"csv","xlsx","xls"}
app=Flask(__name__)
app.secret_key="sprex-nova-secret-2024"
app.config["SQLALCHEMY_DATABASE_URI"]=f"sqlite:///{os.path.join(BASE,'instance','sprex.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"]=False
app.config["UPLOAD_FOLDER"]=UPDIR
app.config["MAX_CONTENT_LENGTH"]=32*1024*1024
os.makedirs(UPDIR,exist_ok=True); os.makedirs(os.path.join(BASE,"instance"),exist_ok=True)
db=SQLAlchemy(app)

class User(db.Model):
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False)
    email=db.Column(db.String(200),unique=True,nullable=False); password=db.Column(db.String(256),nullable=False)
    company=db.Column(db.String(200),default=""); role=db.Column(db.String(80),default="Analyst")
    avatar_color=db.Column(db.String(20),default="#2563eb"); created_at=db.Column(db.DateTime,default=datetime.utcnow)

class Upload(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    filename=db.Column(db.String(300)); stored_name=db.Column(db.String(300))
    parts_count=db.Column(db.Integer,default=0); status=db.Column(db.String(40),default="pending")
    result_json=db.Column(db.Text,default="{}"); uploaded_at=db.Column(db.DateTime,default=datetime.utcnow)

class Notif(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    message=db.Column(db.String(500)); ntype=db.Column(db.String(40),default="info")
    read=db.Column(db.Boolean,default=False); created_at=db.Column(db.DateTime,default=datetime.utcnow)

def allowed(fn): return "." in fn and fn.rsplit(".",1)[1].lower() in EXTS
def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if "uid" not in session: flash("Please log in first.","warning"); return redirect(url_for("login"))
        return f(*a,**kw)
    return dec
def me(): return User.query.get(session["uid"]) if "uid" in session else None
def notif(uid,msg,t="info"): db.session.add(Notif(user_id=uid,message=msg,ntype=t)); db.session.commit()

@app.context_processor
def ctx():
    u=me(); uc=Notif.query.filter_by(user_id=u.id,read=False).count() if u else 0
    return dict(cu=u,nc=uc,now=datetime.utcnow())

@app.route("/")
def index(): return redirect(url_for("dashboard") if "uid" in session else url_for("login"))

@app.route("/login",methods=["GET","POST"])
def login():
    if "uid" in session: return redirect(url_for("dashboard"))
    if request.method=="POST":
        u=User.query.filter_by(email=request.form.get("email","").strip().lower()).first()
        if u and check_password_hash(u.password,request.form.get("password","")):
            session["uid"]=u.id; flash(f"Welcome back, {u.name.split()[0]}! 👋","success"); return redirect(url_for("dashboard"))
        flash("Invalid email or password.","error")
    return render_template("login.html")

@app.route("/signup",methods=["GET","POST"])
def signup():
    if "uid" in session: return redirect(url_for("dashboard"))
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        pw=request.form.get("password",""); confirm=request.form.get("confirm",""); company=request.form.get("company","").strip()
        if not name or not email or not pw: flash("All fields are required.","error")
        elif pw!=confirm: flash("Passwords do not match.","error")
        elif len(pw)<6: flash("Password must be at least 6 characters.","error")
        elif User.query.filter_by(email=email).first(): flash("Email already registered.","error")
        else:
            u=User(name=name,email=email,password=generate_password_hash(pw),company=company)
            db.session.add(u); db.session.commit()
            notif(u.id,"Welcome to SPREX NOVA! Upload your first dataset to get started.","success")
            session["uid"]=u.id; flash(f"Account created! Welcome, {name.split()[0]}! 🎉","success"); return redirect(url_for("dashboard"))
    return render_template("signup.html")

@app.route("/forgot-password",methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); np_=request.form.get("new_password",""); cf=request.form.get("confirm","")
        u=User.query.filter_by(email=email).first()
        if not u: flash("No account with that email.","error")
        elif np_!=cf: flash("Passwords do not match.","error")
        elif len(np_)<6: flash("Password too short.","error")
        else: u.password=generate_password_hash(np_); db.session.commit(); flash("Password reset! Please log in.","success"); return redirect(url_for("login"))
    return render_template("forgot_password.html")

@app.route("/logout")
def logout(): session.clear(); flash("You've been logged out.","info"); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    u=me(); recent=Upload.query.filter_by(user_id=u.id).order_by(Upload.uploaded_at.desc()).limit(5).all()
    total=Upload.query.filter_by(user_id=u.id).count(); succ=Upload.query.filter_by(user_id=u.id,status="success").count()
    parts=sum(r.parts_count for r in Upload.query.filter_by(user_id=u.id).all())
    return render_template("dashboard.html",recent=recent,total=total,succ=succ,total_parts=parts)

@app.route("/upload",methods=["GET","POST"])
@login_required
def upload():
    u=me()
    if request.method=="POST":
        f=request.files.get("file")
        if not f or f.filename=="": flash("No file selected.","error"); return redirect(url_for("upload"))
        if not allowed(f.filename): flash("Only CSV / Excel files accepted.","error"); return redirect(url_for("upload"))
        steps=max(1,min(int(request.form.get("steps",6)),24))
        stored=f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"; path=os.path.join(UPDIR,stored); f.save(path)
        try:
            from ml_engine import run_forecast
            result=run_forecast(path,steps=steps); status="success"; pcount=result["total"]
        except Exception as e:
            result={"error":str(e),"parts":{},"warnings":[],"total":0}; status="error"; pcount=0
        rec=Upload(user_id=u.id,filename=f.filename,stored_name=stored,parts_count=pcount,status=status,result_json=json.dumps(result))
        db.session.add(rec); db.session.commit()
        if status=="success":
            notif(u.id,f'Forecast ready for "{f.filename}" — {pcount} parts analysed.',"success")
            flash(f"Forecast complete! {pcount} part(s) analysed.","success"); return redirect(url_for("results",uid=rec.id))
        else:
            notif(u.id,f'Forecast failed for "{f.filename}".',"error"); flash(f"Error: {result.get('error','Unknown')}","error")
    uploads=Upload.query.filter_by(user_id=u.id).order_by(Upload.uploaded_at.desc()).all()
    return render_template("upload.html",uploads=uploads)

@app.route("/results/<int:uid>")
@login_required
def results(uid):
    u=me(); rec=Upload.query.filter_by(id=uid,user_id=u.id).first()
    if not rec: flash("Result not found.","error"); return redirect(url_for("results_list"))
    data=json.loads(rec.result_json)
    return render_template("results.html",rec=rec,data=data)

@app.route("/results")
@login_required
def results_list():
    u=me(); recs=Upload.query.filter_by(user_id=u.id,status="success").order_by(Upload.uploaded_at.desc()).all()
    return render_template("results_list.html",recs=recs)

@app.route("/notifications")
@login_required
def notifications():
    u=me(); notifs=Notif.query.filter_by(user_id=u.id).order_by(Notif.created_at.desc()).limit(50).all()
    Notif.query.filter_by(user_id=u.id,read=False).update({"read":True}); db.session.commit()
    return render_template("notifications.html",notifs=notifs)

@app.route("/api/nc")
@login_required
def nc_api(): u=me(); return jsonify({"count":Notif.query.filter_by(user_id=u.id,read=False).count()})

@app.route("/profile",methods=["GET","POST"])
@login_required
def profile():
    u=me()
    if request.method=="POST":
        action=request.form.get("action")
        if action=="info":
            u.name=request.form.get("name",u.name).strip(); u.company=request.form.get("company","").strip()
            u.role=request.form.get("role","").strip(); u.avatar_color=request.form.get("avatar_color",u.avatar_color)
            db.session.commit(); flash("Profile updated!","success")
        elif action=="password":
            cur=request.form.get("current",""); new=request.form.get("new",""); cf=request.form.get("confirm","")
            if not check_password_hash(u.password,cur): flash("Current password incorrect.","error")
            elif new!=cf: flash("Passwords do not match.","error")
            elif len(new)<6: flash("Password too short.","error")
            else: u.password=generate_password_hash(new); db.session.commit(); flash("Password changed!","success")
        return redirect(url_for("profile"))
    return render_template("profile.html")

@app.route("/about")
def about(): return render_template("about.html")

with app.app_context(): db.create_all()
if __name__=="__main__": app.run(debug=True,port=5000)
