# app.py
from flask import Flask, request, redirect, render_template_string, session, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3, os
from datetime import datetime


# ---------- Config ----------
APP_TITLE = "SocketChat Ultimate"
DB_FILE = "chat.db"
DEFAULT_ROOM = "general"
ADMIN_PASSWORD = "admin123"
MOD_PASSWORD = "mod123"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key")
socketio = SocketIO(app, cors_allowed_origins="*")

# ---------- Data Stores ----------
roles = {}             # username -> role
sid_to_user = {}       # sid -> (user, room)
users_in_room = {}     # room -> set(users)
rooms = {}             # room_name -> {"locked": bool, "banned": set(), "muted": set()}

# ---------- DB Helpers ----------
def db_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                user TEXT NOT NULL,
                message TEXT NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        conn.commit()

def save_message(room, user, message, ts):
    with db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO messages(room,user,message,ts) VALUES (?,?,?,?)",
            (room, user, message, ts)
        )
        conn.commit()

def get_messages(room, limit=100):
    with db_conn() as conn:
        rows = conn.cursor().execute(
            "SELECT user,message,ts FROM messages WHERE room=? ORDER BY id DESC LIMIT ?",
            (room, limit)
        ).fetchall()
    return list(reversed([{"user": r[0],"message":r[1],"ts":r[2]} for r in rows]))

def ts_now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------- HTTP Routes ----------
@app.route("/")
def index():
    return render_template_string(HTML_INDEX, app_title=APP_TITLE, default_room=DEFAULT_ROOM)

# ---------- Admin Routes ----------
@app.route("/admin", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect(url_for("admin_panel"))
        return "Wrong password",403
    return """<form method="post"><input type="password" name="password"><button>Login</button></form>"""

@app.route("/admin/panel", methods=["GET","POST"])
def admin_panel():
    if not session.get("admin"): return redirect(url_for("admin_login"))
    action=request.form.get("action")
    target_user=request.form.get("user")
    room=request.form.get("room")
    if action and room and target_user:
        room_obj = rooms.setdefault(room, {"locked":False,"banned":set(),"muted":set()})
        users_in_room.setdefault(room,set())
        if action=="kick":
            for sid,(u,r) in list(sid_to_user.items()):
                if u==target_user and r==room:
                    socketio.emit("system",{"room":r,"text":f"{u} kicked by admin","ts":ts_now()},to=r)
                    socketio.disconnect(sid)
                    sid_to_user.pop(sid,None)
                    users_in_room[r].discard(u)
        elif action=="mute": room_obj["muted"].add(target_user)
        elif action=="unmute": room_obj["muted"].discard(target_user)
        elif action=="ban":
            room_obj["banned"].add(target_user)
            for sid,(u,r) in list(sid_to_user.items()):
                if u==target_user and r==room:
                    socketio.emit("system",{"room":r,"text":f"{u} banned by admin","ts":ts_now()},to=r)
                    socketio.disconnect(sid)
        elif action=="unban": room_obj["banned"].discard(target_user)
        elif action=="lock": room_obj["locked"]=True
        elif action=="unlock": room_obj["locked"]=False

    return render_template_string(HTML_ADMIN, users_list={r:list(u) for r,u in users_in_room.items()})

@app.route("/admin/broadcast", methods=["POST"])
def admin_broadcast():
    if not session.get("admin"): return redirect(url_for("admin_login"))
    msg=request.form.get("msg")
    socketio.emit("system",{"room":"all","text":f"[ADMIN]: {msg}","ts":ts_now()})
    return redirect(url_for("admin_panel"))

# ---------- Mod Routes ----------
@app.route("/mod", methods=["GET","POST"])
def mod_login():
    if request.method=="POST":
        if request.form.get("password")==MOD_PASSWORD:
            session["mod"]=True
            return redirect(url_for("mod_panel"))
        return "Wrong password",403
    return """<form method="post"><input type="password" name="password"><button>Login</button></form>"""

@app.route("/mod/panel", methods=["GET","POST"])
def mod_panel():
    if not session.get("mod"): return redirect(url_for("mod_login"))
    action=request.form.get("action")
    target_user=request.form.get("user")
    room=request.form.get("room")
    if action and room and target_user:
        room_obj = rooms.setdefault(room, {"locked":False,"banned":set(),"muted":set()})
        users_in_room.setdefault(room,set())
        if action=="kick":
            for sid,(u,r) in list(sid_to_user.items()):
                if u==target_user and r==room:
                    socketio.emit("system",{"room":r,"text":f"{u} kicked by mod","ts":ts_now()},to=r)
                    socketio.disconnect(sid)
                    sid_to_user.pop(sid,None)
                    users_in_room[r].discard(u)
        elif action=="mute": room_obj["muted"].add(target_user)
        elif action=="unmute": room_obj["muted"].discard(target_user)

    return render_template_string(HTML_MOD, users_list={r:list(u) for r,u in users_in_room.items()})

# ---------- Chat History ----------
@app.route("/history")
def history():
    room=request.args.get("room",DEFAULT_ROOM).strip() or DEFAULT_ROOM
    try: limit=max(1,min(1000,int(request.args.get("limit",200))))
    except: limit=200
    return jsonify(get_messages(room,limit))

# ---------- Socket.IO Events ----------
@socketio.on("join")
def on_join(data):
    user=data['user']; room=data['room']
    room_obj=rooms.setdefault(room, {"locked":False,"banned":set(),"muted":set()})
    if user in room_obj["banned"]: emit("system",{"room":room,"text":"You are banned","ts":ts_now()}); return
    if room_obj["locked"]: emit("system",{"room":room,"text":"Room locked","ts":ts_now()}); return
    sid_to_user[request.sid]=(user,room)
    join_room(room)
    users_in_room.setdefault(room,set()).add(user)
    socketio.emit('users',list(users_in_room[room]),room=room)
    emit("system",{"room":room,"text":f"{user} joined","ts":ts_now()},to=room)

@socketio.on("leave")
def on_leave(data):
    user,room=data['user'],data['room']
    leave_room(room)
    if room in users_in_room: users_in_room[room].discard(user)
    sid_to_user.pop(request.sid,None)
    socketio.emit('users',list(users_in_room.get(room,set())),room=room)
    emit("system",{"room":room,"text":f"{user} left","ts":ts_now()},to=room)

@socketio.on("send_message")
def on_send_message(data):
    sid=request.sid
    user,room=sid_to_user.get(sid,(data.get("user"),data.get("room",DEFAULT_ROOM)))
    room_obj=rooms.setdefault(room,{"locked":False,"banned":set(),"muted":set()})
    if user in room_obj["muted"]: emit("system",{"room":room,"text":"You are muted","ts":ts_now()}); return
    message=data.get("message","").strip()
    if not message: return
    ts=ts_now(); save_message(room,user,message,ts)
    emit("new_message",{"room":room,"user":user,"message":message,"ts":ts},to=room)

@socketio.on("typing")
def on_typing(data):
    room=data.get("room",DEFAULT_ROOM); user=data.get("user","Anon")
    emit("typing",{"room":room,"user":user},to=room,include_self=False)

@socketio.on("stop_typing")
def on_stop_typing(data):
    room=data.get("room",DEFAULT_ROOM)
    emit("stop_typing",{"room":room},to=room,include_self=False)

@socketio.on("disconnect")
def handle_disconnect():
    sid=request.sid
    if sid in sid_to_user:
        user,room=sid_to_user.pop(sid)
        if room in users_in_room: users_in_room[room].discard(user)
        socketio.emit('users',list(users_in_room.get(room,set())),room=room)
        emit("system",{"room":room,"text":f"{user} left","ts":ts_now()},to=room)

# ---------- HTML Templates ----------
HTML_INDEX = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ app_title }}</title>
<style>
body{margin:0;font-family:system-ui;background:#0f172a;color:#e2e8f0}
header{display:flex;justify-content:space-between;padding:12px 16px;background:#111827;position:sticky;top:0}
.card{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:16px;margin:16px auto;max-width:900px}
input,select{width:100%;padding:10px;border-radius:10px;border:1px solid #374151;background:#0b1220;color:#e5e7eb}
button{padding:10px 14px;border:0;border-radius:12px;background:#2563eb;color:white;cursor:pointer}
#messages{height:55vh;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px}
.msg{padding:10px 12px;border-radius:12px;background:#0b1220;border:1px solid #1f2937}
.me{background:#1e293b;border-color:#334155}
.meta{font-size:12px;color:#94a3b8;margin-bottom:4px}
.sys{text-align:center;color:#94a3b8;font-size:12px;padding:6px 0}
#typing{height:18px;font-size:12px;color:#94a3b8;padding:6px}
#composer{display:flex;gap:8px;margin-top:8px}
#composer input{flex:1}
</style>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
</head>
<body>
<header>
<h1>💬 {{ app_title }}</h1><span id="you"></span>
<button id="signoutBtn" style="background:#ef4444;">Sign Out</button>
</header>
<section id="roomForm" class="card">
<label>Username</label><input id="username" placeholder="e.g. alex">
<label>Room</label><input id="room" value="{{ default_room }}">
<button id="joinBtn">Join chat</button>
<button id="randomBtn">Random name</button>
</section>
<section id="chatUI" class="card" style="display:none;">
<div id="messages"></div>
<div id="typing"></div>
<div id="composer">
<input id="message" placeholder="Type a message…">
<button id="sendBtn">Send</button>
</div>
</section>
<script>
const $=(s)=>document.querySelector(s);
const youEl=$('#you'), form=$('#roomForm'), chatUI=$('#chatUI'), messagesEl=$('#messages');
const typingEl=$('#typing'), usernameEl=$('#username'), roomEl=$('#room');
const joinBtn=$('#joinBtn'), randomBtn=$('#randomBtn'), sendBtn=$('#sendBtn'), signoutBtn=$('#signoutBtn');
let socket=null, state={user:null,room:null};
function setYouLabel(){ youEl.textContent=state.user&&state.room?`${state.user} · #${state.room}`:''; }
function showChat(){ form.style.display='none'; chatUI.style.display='block'; setYouLabel(); messageEl.focus(); }
function showForm(){ chatUI.style.display='none'; form.style.display='block'; }
function addSystem(text,ts){ const d=document.createElement('div'); d.className='sys'; d.textContent=`${text} • ${ts||''}`.trim(); messagesEl.appendChild(d); messagesEl.scrollTop=messagesEl.scrollHeight; }
function addMessage({user,message,ts}){ const d=document.createElement('div'); d.className='msg'+(user===state.user?' me':''); const m=document.createElement('div'); m.className='meta'; m.textContent=`${user} • ${ts}`; const b=document.createElement('div'); b.textContent=message; d.appendChild(m); d.appendChild(b); messagesEl.appendChild(d); messagesEl.scrollTop=messagesEl.scrollHeight; }
async function fetchHistory(room){ const res=await fetch(`/history?room=${encodeURIComponent(room)}&limit=200`); const msgs=await res.json(); messagesEl.innerHTML=''; msgs.forEach(addMessage); }
function connect(){ socket=io(); socket.on('connect',()=>{ socket.emit('join',{user:state.user,room:state.room}); }); socket.on('new_message',data=>{ if(data.room!==state.room)return; addMessage(data); }); socket.on('system',data=>{ if(data.room!==state.room&&data.room!=='all')return; addSystem(data.text,data.ts); }); let typingTimeout=null; socket.on('typing',data=>{ if(data.room!==state.room)return; typingEl.textContent=`${data.user} is typing…`; clearTimeout(typingTimeout); typingTimeout=setTimeout(()=>typingEl.textContent='',1200); }); socket.on('stop_typing',data=>{ if(data.room!==state.room)return; typingEl.textContent=''; }); }
function send(){ const t=(messageEl.value||'').trim(); if(!t)return; socket.emit('send_message',{room:state.room,user:state.user,message:t}); messageEl.value=''; }
joinBtn.addEventListener('click',async()=>{ const u=(usernameEl.value||'').trim()||'Anonymous'; const r=(roomEl.value||'').trim()||'{{ default_room }}'; state.user=u; state.room=r; localStorage.setItem('chatUser',u); localStorage.setItem('chatRoom',r); await fetchHistory(r); showChat(); if(!socket) connect(); else socket.emit('join',{user:u,room:r}); });
randomBtn.addEventListener('click',()=>{ usernameEl.value='user'+Math.floor(Math.random()*10000); });
sendBtn.addEventListener('click',send);
const messageEl=$('#message'); messageEl.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); send(); } });
signoutBtn.addEventListener('click',()=>{ if(socket){ socket.emit('leave',{user:state.user,room:state.room}); socket.disconnect(); socket=null; } state.user=null; state.room=null; localStorage.removeItem('chatUser'); localStorage.removeItem('chatRoom'); messagesEl.innerHTML=''; typingEl.textContent=''; showForm(); setYouLabel(); });
window.addEventListener('load',async()=>{ const u=localStorage.getItem('chatUser'), r=localStorage.getItem('chatRoom'); if(u&&r){ state.user=u; state.room=r; usernameEl.value=u; roomEl.value=r; await fetchHistory(r); showChat(); connect(); } });
</script></body></html>
"""

HTML_ADMIN = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin Panel</title>
<style>
body { margin:0; font-family:system-ui; background:#0f172a; color:#e2e8f0; }
header { display:flex; justify-content:space-between; align-items:center; padding:12px 16px; background:#111827; }
h1,h2 { margin:8px 0; }
.card { background:#111827; border:1px solid #1f2937; border-radius:12px; padding:16px; margin:16px auto; max-width:1000px; }
table { width:100%; border-collapse:collapse; margin-top:8px; }
th, td { padding:8px 12px; border:1px solid #1f2937; text-align:left; }
th { background:#1f2937; }
input, select, button { padding:8px 10px; border-radius:8px; border:1px solid #374151; background:#0b1220; color:#e5e7eb; margin:4px 0; }
button { background:#2563eb; color:white; cursor:pointer; border:none; }
button.red { background:#ef4444; }
</style>
</head>
<body>
<header>
<h1>🛡 Admin Panel</h1>
<form action="/admin/logout" method="post" style="margin:0;"><button class="red">Logout</button></form>
</header>

<div class="card">
<h2>Broadcast Message</h2>
<form method="post" action="/admin/broadcast">
<input type="text" name="msg" placeholder="System Message">
<button>Send</button>
</form>
</div>

<div class="card">
<h2>Active Rooms & Users</h2>
{% for room, users in users_list.items() %}
<h3>{{ room }}</h3>
<table>
<tr><th>User</th><th>Actions</th></tr>
{% for user in users %}
<tr>
<td>{{ user }}</td>
<td>
<form method="post" style="display:flex; gap:4px;">
<input type="hidden" name="room" value="{{ room }}">
<input type="hidden" name="user" value="{{ user }}">
<select name="action">
<option value="kick">Kick</option>
<option value="mute">Mute</option>
<option value="unmute">Unmute</option>
<option value="ban">Ban</option>
<option value="unban">Unban</option>
<option value="lock">Lock Room</option>
<option value="unlock">Unlock Room</option>
</select>
<button>Execute</button>
</form>
</td>
</tr>
{% endfor %}
</table>
{% endfor %}
</div>
</body>
</html>
"""
HTML_MOD =  r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moderator Panel</title>
<style>
body { margin:0; font-family:system-ui; background:#0f172a; color:#e2e8f0; }
header { display:flex; justify-content:space-between; align-items:center; padding:12px 16px; background:#111827; }
h1,h2 { margin:8px 0; }
.card { background:#111827; border:1px solid #1f2937; border-radius:12px; padding:16px; margin:16px auto; max-width:1000px; }
table { width:100%; border-collapse:collapse; margin-top:8px; }
th, td { padding:8px 12px; border:1px solid #1f2937; text-align:left; }
th { background:#1f2937; }
input, select, button { padding:8px 10px; border-radius:8px; border:1px solid #374151; background:#0b1220; color:#e5e7eb; margin:4px 0; }
button { background:#2563eb; color:white; cursor:pointer; border:none; }
button.red { background:#ef4444; }
</style>
</head>
<body>
<header>
<h1>🛡 Moderator Panel</h1>
<form action="/mod/logout" method="post" style="margin:0;"><button class="red">Logout</button></form>
</header>

<div class="card">
<h2>Active Rooms & Users</h2>
{% for room, users in users_list.items() %}
<h3>{{ room }}</h3>
<table>
<tr><th>User</th><th>Actions</th></tr>
{% for user in users %}
<tr>
<td>{{ user }}</td>
<td>
<form method="post" style="display:flex; gap:4px;">
<input type="hidden" name="room" value="{{ room }}">
<input type="hidden" name="user" value="{{ user }}">
<select name="action">
<option value="kick">Kick</option>
<option value="mute">Mute</option>
<option value="unmute">Unmute</option>
</select>
<button>Execute</button>
</form>
</td>
</tr>
{% endfor %}
</table>
{% endfor %}
</div>
</body>
</html>
"""

# ---------- Run ----------
if __name__=="__main__":
    init_db()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)



