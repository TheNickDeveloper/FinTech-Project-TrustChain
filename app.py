import streamlit as st
import hashlib
import time
import pandas as pd
import plotly.express as px
import sqlite3
from pathlib import Path
import base64
import os
from typing import Dict, Any, List

# -------------------------
# Configuration
# -------------------------
DB_PATH = "trustchain.db"
UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_FEE_RATE = 0.02  # admin fee applied once on release (2%)

# -------------------------
# Database helpers
# -------------------------
def get_conn():
    """
    Return a sqlite3 connection. check_same_thread=False to allow
    Streamlit's single-threaded model to reuse the connection.
    """
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    """
    Initialize DB schema. idempotent — safe to call on every app start.
    """
    conn = get_conn()
    c = conn.cursor()

    # Students table
    c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            need REAL,
            received REAL,
            story TEXT,
            doc_hash TEXT,
            released INTEGER,
            admin_charged INTEGER
        )
    """)

    # Ledger table
    c.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            type TEXT,
            student_id INTEGER,
            student_name TEXT,
            gross REAL,
            student_net REAL,
            admin_fee REAL,
            student_amount REAL,
            filename TEXT,
            file_hash TEXT
        )
    """)

    # Proofs table -- persist status and file_path
    c.execute("""
        CREATE TABLE IF NOT EXISTS proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            student_name TEXT,
            filename TEXT,
            hash TEXT,
            time TEXT,
            status TEXT DEFAULT 'Submitted',
            file_path TEXT
        )
    """)

    conn.commit()
    conn.close()

    # run a quick migration helper to ensure columns exist if DB is older
    ensure_proofs_columns()

def ensure_proofs_columns():
    """
    Defensive migration: ensure 'status' and 'file_path' exist on proofs table.
    SQLite ALTER TABLE supports ADD COLUMN so we can add if missing.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        pragma = c.execute("PRAGMA table_info(proofs)").fetchall()
        existing_cols = [row[1] for row in pragma]
        if "status" not in existing_cols:
            c.execute("ALTER TABLE proofs ADD COLUMN status TEXT DEFAULT 'Submitted'")
        if "file_path" not in existing_cols:
            c.execute("ALTER TABLE proofs ADD COLUMN file_path TEXT")
        conn.commit()
    except Exception:
        # If anything odd happens, ignore — table may already be in desired shape
        pass
    finally:
        conn.close()

# initialize DB on import
init_db()

# -------------------------
# Utility: Timestamps & Hashing
# -------------------------
def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def compute_hash(b: bytes) -> str:
    """
    Return SHA256 hex digest of given bytes.
    """
    return hashlib.sha256(b).hexdigest()

# -------------------------
# DB Read helpers
# -------------------------
def load_students_from_db() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM students ORDER BY id ASC", conn)
    finally:
        conn.close()
    return df.to_dict("records")

def load_ledger_from_db() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM ledger ORDER BY id ASC", conn)
    finally:
        conn.close()
    return df.to_dict("records")

def load_proofs_from_db() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM proofs ORDER BY id ASC", conn)
    finally:
        conn.close()
    return df.to_dict("records")

# -------------------------
# DB Write helpers
# -------------------------
def db_add_student(name: str, need: float, story: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO students (name, need, received, story, doc_hash, released, admin_charged) VALUES (?, ?, 0, ?, NULL, 0, 0)",
        (name, need, story)
    )
    conn.commit()
    conn.close()

def db_update_student(student: Dict[str, Any]):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE students
        SET name=?, need=?, received=?, story=?, doc_hash=?, released=?, admin_charged=?
        WHERE id=?
    """, (
        student["name"],
        student["need"],
        student["received"],
        student.get("story", ""),
        student.get("doc_hash"),
        int(bool(student.get("released"))),
        int(bool(student.get("admin_charged"))),
        student["id"]
    ))
    conn.commit()
    conn.close()

def db_add_ledger(entry: Dict[str, Any]):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO ledger (time, type, student_id, student_name, gross, student_net, admin_fee, student_amount, filename, file_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry.get("time"),
        entry.get("type"),
        entry.get("student_id"),
        entry.get("student_name"),
        entry.get("gross"),
        entry.get("student_net"),
        entry.get("admin_fee"),
        entry.get("student_amount"),
        entry.get("filename"),
        entry.get("file_hash")
    ))
    conn.commit()
    conn.close()

def db_add_proof(p: Dict[str, Any]):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO proofs (student_id, student_name, filename, hash, time, status, file_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        p["student_id"],
        p["student_name"],
        p["filename"],
        p["hash"],
        p["time"],
        p.get("status", "Submitted"),
        p.get("file_path")
    ))
    conn.commit()
    conn.close()

def db_update_proof(file_hash: str, updates: Dict[str, Any]):
    """
    Update proof row identified by hash. 'updates' should be a dict of column->value.
    """
    if not updates:
        return
    conn = get_conn()
    c = conn.cursor()
    fields = ", ".join([f"{k}=?" for k in updates.keys()])
    vals = list(updates.values()) + [file_hash]
    c.execute(f"UPDATE proofs SET {fields} WHERE hash=?", vals)
    conn.commit()
    conn.close()

# -------------------------
# Session initialization (load DB into session)
# -------------------------
if "initialized" not in st.session_state:
    # Load from DB
    students = load_students_from_db()
    ledger = load_ledger_from_db()
    proofs_db = load_proofs_from_db()

    # Seed sample students if DB empty (safe idempotent)
    if len(students) == 0:
        db_add_student("Alice Chan", 2000.0, "Alice dreams to continue secondary school.")
        db_add_student("Ben Wong", 1500.0, "Ben needs tuition for next semester.")
        db_add_student("Cindy Lee", 1000.0, "Cindy needs school supplies & uniform.")
        students = load_students_from_db()

    # Convert DB proofs to session format (keep status & path)
    session_proofs = []
    for p in proofs_db:
        session_proofs.append({
            "student_id": p.get("student_id"),
            "student_name": p.get("student_name"),
            "filename": p.get("filename"),
            "hash": p.get("hash"),
            "time": p.get("time"),
            "status": p.get("status", "Submitted"),
            "file_path": p.get("file_path")
        })

    st.session_state.students = students
    st.session_state.ledger = ledger
    st.session_state.proofs = session_proofs
    st.session_state.notification = None
    st.session_state.show_balloons = False
    st.session_state.initialized = True

# -------------------------
# Safe rerun helper
# -------------------------
def safe_rerun():
    """Rerun Streamlit script safely across versions."""
    if hasattr(st, "rerun"):
        try:
            st.rerun()
        except Exception:
            pass
    elif hasattr(st, "experimental_rerun"):
        try:
            st.experimental_rerun()
        except Exception:
            pass

# -------------------------
# Ledger helper that writes both session and DB
# -------------------------
def add_ledger(entry: Dict[str, Any]):
    """
    Add a ledger entry to session and DB. Adds timestamp if missing.
    """
    entry = dict(entry)  # shallow copy
    if "time" not in entry or not entry.get("time"):
        entry["time"] = now_ts()
    st.session_state.ledger.append(entry)
    db_add_ledger(entry)

# -------------------------
# Small animation helper for progress visuals
# -------------------------
def animate_progress(old_val: float, new_val: float, total: float, placeholder):
    """
    Animate a small progress bar from old -> new. placeholder is a streamlit.empty() slot.
    """
    try:
        old_pct = float(old_val) / float(total) if total > 0 else 1.0
        new_pct = float(new_val) / float(total) if total > 0 else 1.0
    except Exception:
        old_pct, new_pct = 0.0, 0.0

    steps = 12
    for i in range(1, steps + 1):
        inter = old_pct + (new_pct - old_pct) * (i / steps)
        try:
            placeholder.progress(int(inter * 100))
        except Exception:
            pass
        time.sleep(0.02)
    try:
        placeholder.progress(int(new_pct * 100))
    except Exception:
        pass

# -------------------------
# Auto-release logic (fires when student fully funded + proof Verified)
# -------------------------
def try_auto_release(student: Dict[str, Any]):
    """
    If student meets conditions (received >= need AND a Verified proof exists),
    then mark released, charge admin fee once, write ledger entries, and persist to DB.
    """
    if not student:
        return

    fully_funded = float(student.get("received", 0.0)) >= float(student.get("need", 0.0))
    proof_verified = any(
        p.get("student_id") == student["id"] and p.get("status") == "Verified"
        for p in st.session_state.proofs
    )

    if fully_funded and proof_verified and not student.get("released", False):
        # mark release in session
        student["released"] = True

        # admin fee (charged once)
        if not student.get("admin_charged", False):
            admin_fee = round(float(student["need"]) * ADMIN_FEE_RATE, 2)
            student_amount = round(float(student["need"]) - admin_fee, 2)

            ledger_entry_fee = {
                "type": "admin_fee",
                "student_id": student["id"],
                "student_name": student["name"],
                "admin_fee": admin_fee,
                "student_amount": student_amount,
                "time": now_ts()
            }
            add_ledger(ledger_entry_fee)
            student["admin_charged"] = True

        # release ledger
        ledger_entry_release = {
            "type": "release",
            "student_id": student["id"],
            "student_name": student["name"],
            "amount_released": float(student["need"]),
            "time": now_ts()
        }
        add_ledger(ledger_entry_release)

        # persist student changes
        db_update_student(student)

        # UI notification
        st.session_state.notification = {"message": f"🔓 Funds released to {student['name']}", "level": "info"}
        st.session_state.show_balloons = True

# -------------------------
# CSS / Small UI tweaks
# -------------------------
st.set_page_config(page_title="TrustChain Demo", layout="centered")
CSS = """
<style>
body { font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; }
.card { background:#fff; padding:14px; border-radius:12px; box-shadow: 0 4px 10px rgba(0,0,0,0.06); margin-bottom:12px; }
.student-card { display:flex; gap:12px; align-items:flex-start; }
.avatar { width:64px; height:64px; border-radius:12px; background: linear-gradient(135deg,#6EE7B7,#3B82F6); display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:700; color:white; }
.row { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-top:10px; }
.small { font-size:13px; color:#666; }
.badge { padding:5px 10px; border-radius:999px; font-size:12px; color:white; margin-right:6px; }
.badge-green { background:#16a34a; }
.badge-yellow { background:#f59e0b; }
.badge-red { background:#ef4444; }
.notice { padding:12px; border-radius:10px; color:white; margin:10px 0; font-weight:600; }
.success { background:linear-gradient(90deg,#34d399,#059669); }
.info { background:linear-gradient(90deg,#60a5fa,#2563eb); }
.tiny { font-size:12px; color:#777; }
.kpi-box { padding:12px; background:#fafafa; border:1px solid #eee; border-radius:10px; text-align:center; }
a { color: #0366d6; }
@media (max-width:600px){ .avatar { width:55px; height:55px; font-size:18px; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# -------------------------
# Show queued notifications (if any)
# -------------------------
if st.session_state.get("notification"):
    n = st.session_state.pop("notification", None)
    if n:
        lev = n.get("level", "success")
        cls = "success" if lev == "success" else "info"
        st.markdown(f"<div class='notice {cls}'>{n.get('message')}</div>", unsafe_allow_html=True)
        if st.session_state.get("show_balloons"):
            try:
                st.balloons()
            except Exception:
                pass
            st.session_state.show_balloons = False

# -------------------------
# Sidebar navigation (pages handled in Part 3)
# -------------------------
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Dashboard", "Add Student", "Make Donation", "Upload Proof", "Ledger"])

# ------------------------------
# Dashboard page
# ------------------------------
if page == "Dashboard":
    st.image("trustchain_logo1.png")

    students = st.session_state.students
    ledger = st.session_state.ledger

    total_need = sum(float(s.get("need", 0.0)) for s in students)
    received_total = sum(min(float(s.get("received", 0.0)), float(s.get("need", 0.0))) for s in students)
    total_admin = sum(float(tx.get("admin_fee", 0)) for tx in ledger if tx.get("type") == "admin_fee")
    released_total = sum(float(s.get("need", 0.0)) if s.get("released") else 0 for s in students)

    c1, c2, c3 = st.columns(3)
    c1.metric("🎯 Total Required", f"${total_need:,.2f}")
    c2.metric("💰 Funded (capped)", f"${received_total:,.2f}")
    c3.metric("🏛 Admin Fee Collected", f"${total_admin:,.2f}")

    st.markdown("---")
    st.subheader("Funding Overview — Status")

    required_total = total_need
    received_total = received_total
    released_total = released_total
    not_released_total = max(received_total - released_total, 0)
    remaining_total = max(required_total - received_total, 0)
    admin_fee_total = total_admin

    labels = ["Funded - Not Released 🔒", "Funded - Released 🔓", "Remaining 🔄", "Admin Fee 🏛"]
    values = [not_released_total, released_total, remaining_total, admin_fee_total]
    colors = ["#FFC300", "#2ECC71", "#E74C3C", "#9B59B6"]

    fig = px.pie(values=values, names=labels, color=labels, color_discrete_map=dict(zip(labels, colors)), hole=0.55)
    fig.update_traces(textinfo="percent+label", pull=[0.03, 0.02, 0, 0.05])
    fig.update_layout(title="💸 Overall Funding Transparency Status", showlegend=True, margin=dict(t=40, l=0, r=0, b=0))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        f"""
        <div style='text-align:center; font-size:15px; margin-top:8px;'>
            <b>Total Required:</b> ${required_total:,.2f} &nbsp;•&nbsp;
            <b>Funded:</b> ${received_total:,.2f} &nbsp;•&nbsp;
            <b>Remaining:</b> ${remaining_total:,.2f}
            <br>
            <b>Released:</b> ${released_total:,.2f} &nbsp;•&nbsp;
            <b>Not Released:</b> ${not_released_total:,.2f} &nbsp;•&nbsp;
            <b>Admin Fee:</b> ${admin_fee_total:,.2f}
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Students")
    for s in students:
        remaining = max(float(s.get("need", 0.0)) - float(s.get("received", 0.0)), 0.0)
        funded = float(s.get("received", 0.0)) >= float(s.get("need", 0.0))
        proof_record = next((p for p in st.session_state.proofs if p["student_id"] == s["id"]), None)
        proof_status = proof_record.get("status") if proof_record else None
        released = s.get("released", False)
        initials = "".join([p[0] for p in s["name"].split()][:2]).upper()

        status_funded = ('<span class="badge badge-green">✔ Funded</span>' if funded else ('<span class="badge badge-yellow">⚠ Partial</span>' if float(s.get("received",0))>0 else '<span class="badge badge-red">❌ Not Funded</span>'))
        status_proof = (f'<span class="badge badge-green">📄 {proof_status}</span>' if proof_record else '<span class="badge badge-red">❌ No Proof</span>')
        status_release = ('<span class="badge badge-green">🔓 Released</span>' if proof_record else '<span class="badge badge-red">❌ Locked</span>')

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"""
            <div class="student-card">
                <div class="avatar">{initials}</div>
                <div style="flex:1">
                    <div style="font-weight:700">{s['name']}</div>
                    <div class="tiny">{s.get('story','')}</div>
                    <div class="row">
                        <div class="small"><b>Required:</b> ${float(s['need']):,.2f}</div>
                        <div class="small"><b>Received:</b> ${float(s['received']):,.2f}</div>
                        <div class="small"><b>Remaining:</b> ${remaining:,.2f}</div>
                    </div>
                    <div style="margin-top:8px">
                        {status_funded}
                        {status_proof}
                        {status_release}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Make Donation page
# ------------------------------
elif page == "Make Donation":
    st.title("💝 Make Donation")
    st.write("Donate to a student. Admin fee is charged once when a student's funds are released (after proof verification).")

    students = st.session_state.students
    for s in students:
        remaining = max(float(s.get("need", 0.0)) - float(s.get("received", 0.0)), 0.0)
        funded = float(s.get("received", 0.0)) >= float(s.get("need", 0.0))
        initials = "".join([x[0] for x in s["name"].split()][:2]).upper()

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"""
            <div class="student-card">
                <div class="avatar">{initials}</div>
                <div style="flex:1">
                    <div style="font-weight:700">{s['name']}</div>
                    <div class="tiny">{s.get('story','')}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="row">
                <div class="small"><b>Required:</b> ${float(s['need']):,.2f}</div>
                <div class="small"><b>Remaining:</b> ${remaining:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)

        if funded:
            st.success("This student is fully funded — donation disabled.")
        else:
            max_slider = max(int(remaining) if remaining >=1 else 50, 50)
            slider_key = f"slider_{s['id']}"
            num_key = f"num_{s['id']}"
            if slider_key not in st.session_state:
                st.session_state[slider_key] = min(50, max_slider)
            col1, col2 = st.columns([3,1])
            slider_val = col1.slider("Amount", min_value=1, max_value=max_slider, value=st.session_state[slider_key], key=slider_key)
            col2.number_input("USD", min_value=1, value=slider_val, key=num_key)
            try:
                num_val = int(st.session_state[num_key])
                if num_val != slider_val:
                    st.session_state[slider_key] = num_val
                    slider_val = num_val
            except Exception:
                pass

            if st.button("Donate Now", key=f"donate_btn_{s['id']}"):
                amount = float(st.session_state[slider_key])
                old_received = float(s["received"])
                donation_entry = {
                    "type": "donation",
                    "student_id": s["id"],
                    "student_name": s["name"],
                    "gross": float(amount),
                    "student_net": float(amount),
                    "released": False,
                    "time": now_ts()
                }
                add_ledger(donation_entry)
                s["received"] = float(s.get("received", 0.0)) + amount
                db_update_student(s)
                ph = st.empty()
                animate_progress(old_received, s["received"], float(s["need"]), ph)
                st.session_state.notification = {"message": f"🎉 Donation ${amount:,.2f} recorded for {s['name']}", "level": "success"}
                st.session_state.show_balloons = True
                # try release if possible (likely not until proof verified)
                try_auto_release(s)
                safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Upload Proof page (streamlined Reviewing -> 7s -> Verified -> Release)
# ------------------------------
elif page == "Upload Proof":
    st.title("📄 Upload Proof")
    st.write("When a student reaches required amount, upload proof (PDF or image).")

    # refresh proofs from DB into session to keep fresh state
    proofs_db = load_proofs_from_db()
    st.session_state.proofs = []
    for p in proofs_db:
        st.session_state.proofs.append({
            "student_id": p.get("student_id"),
            "student_name": p.get("student_name"),
            "filename": p.get("filename"),
            "hash": p.get("hash"),
            "time": p.get("time"),
            "status": p.get("status", "Submitted"),
            "file_path": p.get("file_path")
        })

    for s in st.session_state.students:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader(s["name"])
        funded = float(s.get("received", 0.0)) >= float(s.get("need", 0.0))

        existing_proof = next((p for p in st.session_state.proofs if p["student_id"] == s["id"]), None)

        if not funded:
            st.info("Student not fully funded yet; cannot upload proof.")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        # If a proof exists, display current status, show link and preview, and disable upload
        if existing_proof:
            status = existing_proof.get("status", "Submitted")
            st.markdown(f"**Proof status:** {status}")
            if existing_proof.get("file_path"):
                fp = existing_proof["file_path"]
                fname = existing_proof["filename"]
                if os.path.exists(fp):
                    st.markdown(f"Saved file: [{fname}]({fp})")
                else:
                    st.markdown(f"Saved file: {fname} (file missing)")
            # preview via expander (safe)
            if existing_proof.get("file_path") and os.path.exists(existing_proof["file_path"]):
                with st.expander("Preview file"):
                    path = existing_proof["file_path"]
                    if path.lower().endswith(".pdf"):
                        try:
                            with open(path, "rb") as f:
                                b64 = base64.b64encode(f.read()).decode()
                            iframe = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="600px"></iframe>'
                            st.markdown(iframe, unsafe_allow_html=True)
                        except Exception:
                            st.write("Preview not available.")
                    else:
                        try:
                            st.image(path, use_column_width=True)
                        except Exception:
                            st.write("Preview not available.")
            st.info("Upload disabled — only one proof allowed per student.")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        # allow upload (only if no existing proof)
        uploaded = st.file_uploader(f"Upload proof for {s['name']} (PDF / JPG / PNG)", type=['pdf','png','jpg','jpeg'], key=f"proof_{s['id']}")
        if uploaded:
            fname = uploaded.name
            fname_lower = fname.lower()
            if not (fname_lower.endswith(".pdf") or fname_lower.endswith(".png") or fname_lower.endswith(".jpg") or fname_lower.endswith(".jpeg")):
                st.error("Only PDF or image files (png/jpg/jpeg) are allowed.")
            else:
                content = uploaded.read()
                ts = int(time.time())
                safe_name = f"{s['id']}_{ts}_{fname}"
                save_path = UPLOAD_DIR / safe_name
                with open(save_path, "wb") as f:
                    f.write(content)

                st.markdown(f"Saved file: [{fname}]({save_path.as_posix()})")

                # preview via expander
                b64 = base64.b64encode(content).decode()
                with st.expander("Preview file"):
                    if fname_lower.endswith(".pdf"):
                        iframe = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="600px"></iframe>'
                        st.markdown(iframe, unsafe_allow_html=True)
                    else:
                        st.image(content, use_column_width=True)

                # Submit: set Reviewing, show progress, then mark Verified and release
                if st.button(f"Submit Proof for {s['name']}", key=f"submit_proof_{s['id']}"):
                    file_hash = compute_hash(content)

                    # 1) persist Reviewing proof (DB + session)
                    proof_record = {
                        "student_id": s["id"],
                        "student_name": s["name"],
                        "filename": fname,
                        "hash": file_hash,
                        "time": now_ts(),
                        "status": "Reviewing",
                        "file_path": str(save_path)
                    }
                    db_add_proof(proof_record)
                    st.session_state.proofs.append(proof_record)

                    # 2) ledger: proof_upload
                    add_ledger({
                        "type": "proof_upload",
                        "student_id": s["id"],
                        "student_name": s["name"],
                        "filename": fname,
                        "file_hash": file_hash,
                        "time": now_ts()
                    })

                    # 3) notify + progress UI
                    st.session_state.notification = {"message": f"📄 Proof submitted for {s['name']} — Reviewing (7s)...", "level": "info"}
                    st.session_state.show_balloons = False
                    # no safe_rerun() yet — we must finish the simulation before rerunning

                    # display a progress bar for the review
                    ph_text = st.empty()
                    ph_progress = st.empty()
                    ph_text.info("Review in progress...")
                    for i in range(1, 71):  # 7 seconds with 0.1s steps -> 70 iterations
                        ph_progress.progress(int((i/70) * 100))
                        time.sleep(0.1)

                    ph_text.success("Review completed. Marking Verified...")

                    # 4) update to Verified (DB + session)
                    db_update_proof(file_hash, {"status": "Verified", "file_path": str(save_path)})
                    for p in st.session_state.proofs:
                        if p["hash"] == file_hash:
                            p["status"] = "Verified"
                            p["file_path"] = str(save_path)
                            break

                    # 5) ledger: proof_verified
                    add_ledger({
                        "type": "proof_verified",
                        "student_id": s["id"],
                        "student_name": s["name"],
                        "filename": fname,
                        "file_hash": file_hash,
                        "time": now_ts()
                    })

                    # 6) update student's doc_hash and attempt immediate release
                    s["doc_hash"] = file_hash
                    db_update_student(s)

                    st.session_state.notification = {"message": f"✅ Proof verified for {s['name']}. Releasing funds if eligible...", "level": "success"}
                    st.session_state.show_balloons = True

                    try_auto_release(s)

                    # final rerun to refresh UI
                    safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Ledger page
# ------------------------------
elif page == "Ledger":
    st.title("⛓ Ledger")
    # refresh ledger from DB
    st.session_state.ledger = load_ledger_from_db()
    if st.session_state.ledger:
        df_ledger = pd.DataFrame(st.session_state.ledger)
        csv = df_ledger.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Ledger CSV", data=csv, file_name="trustchain_ledger.csv", mime="text/csv", use_container_width=True)
    else:
        st.info("No ledger records yet.")

    if st.session_state.ledger:
        for tx in reversed(st.session_state.ledger):
            t = tx.get("type", "").lower()
            if t == "donation":
                gross = float(tx.get("gross", 0.0))
                student_name = tx.get("student_name", "Unknown")
                time_str = tx.get("time", "")
                st.markdown(f"**💰 Donation** — {student_name} • ${gross:,.2f} • {time_str}")
            elif t == "proof_upload":
                student_name = tx.get("student_name", "Unknown")
                filename = tx.get("filename", "")
                file_hash = tx.get("file_hash", "")
                time_str = tx.get("time", "")
                st.markdown(f"**📄 Proof Submitted** — {student_name} • {filename} • hash: `{file_hash}` • {time_str}")
            elif t == "proof_verified":
                student_name = tx.get("student_name", "Unknown")
                filename = tx.get("filename", "")
                file_hash = tx.get("file_hash", "")
                time_str = tx.get("time", "")
                st.markdown(f"**✅ Proof Verified** — {student_name} • {filename} • hash: `{file_hash}` • {time_str}")
            elif t == "admin_fee":
                student_name = tx.get("student_name", "Unknown")
                admin_fee = float(tx.get("admin_fee", 0.0))
                student_amount = float(tx.get("student_amount", 0.0))
                time_str = tx.get("time", "")
                st.markdown(f"**🏛 Admin Fee Charged** — {student_name} • fee: ${admin_fee:,.2f} • student receives: ${student_amount:,.2f} • {time_str}")
            elif t == "release":
                student_name = tx.get("student_name", "Unknown")
                amount_released = float(tx.get("amount_released", tx.get("gross", 0.0)))
                time_str = tx.get("time", "")
                st.markdown(f"**🔓 Release** — {student_name} • ${amount_released:,.2f} • {time_str}")
            else:
                st.json(tx)

# ------------------------------
# Add Student page
# ------------------------------
elif page == "Add Student":
    st.title("➕ Add Student")
    name = st.text_input("Student Name")
    need = st.number_input("Required Amount (USD)", min_value=1, value=1000, step=50)
    story = st.text_area("Short story (one line)", max_chars=140)
    if st.button("Add Student"):
        db_add_student(name, float(need), story)
        st.session_state.students = load_students_from_db()
        st.session_state.notification = {"message": f"👶 Student '{name}' added.", "level": "success"}
        safe_rerun()
