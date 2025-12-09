# app.py
import streamlit as st
import hashlib
import time
import pandas as pd
import plotly.express as px
import sqlite3
from pathlib import Path

# ------------------------------
# Config
# ------------------------------
DB_PATH = "trustchain.db"
ADMIN_FEE_RATE = 0.05  # charged once at release (5%)

# ------------------------------
# Database helpers / schema
# ------------------------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    # students
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
    # ledger
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
    # proofs
    c.execute("""
        CREATE TABLE IF NOT EXISTS proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            student_name TEXT,
            filename TEXT,
            hash TEXT,
            time TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ------------------------------
# DB <-> app utilities
# ------------------------------
def now_ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def compute_hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def load_students_from_db():
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM students ORDER BY id ASC", conn)
    conn.close()
    return df.to_dict("records")

def load_ledger_from_db():
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM ledger ORDER BY id ASC", conn)
    conn.close()
    return df.to_dict("records")

def load_proofs_from_db():
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM proofs ORDER BY id ASC", conn)
    conn.close()
    return df.to_dict("records")

def db_add_student(name: str, need: float, story: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO students (name, need, received, story, doc_hash, released, admin_charged) VALUES (?, ?, 0, ?, NULL, 0, 0)",
        (name, need, story)
    )
    conn.commit()
    conn.close()

def db_update_student(student: dict):
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

def db_add_ledger(entry: dict):
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

def db_add_proof(p: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO proofs (student_id, student_name, filename, hash, time)
        VALUES (?, ?, ?, ?, ?)
    """, (p["student_id"], p["student_name"], p["filename"], p["hash"], p["time"]))
    conn.commit()
    conn.close()

# ------------------------------
# Session state initialization (load DB into session)
# ------------------------------
if "initialized" not in st.session_state:
    # load or seed
    students = load_students_from_db()
    ledger = load_ledger_from_db()
    proofs = load_proofs_from_db()

    if len(students) == 0:
        # seed initial students (only if DB empty)
        db_add_student("Alice Chan", 2000.0, "Alice dreams to continue secondary school.")
        db_add_student("Ben Wong", 1500.0, "Ben needs tuition for next semester.")
        db_add_student("Cindy Lee", 1000.0, "Cindy needs school supplies & uniform.")
        students = load_students_from_db()

    st.session_state.students = students
    st.session_state.ledger = ledger
    st.session_state.proofs = proofs
    st.session_state.notification = None
    st.session_state.show_balloons = False
    st.session_state.initialized = True

# ------------------------------
# UI helpers and safe rerun
# ------------------------------
def safe_rerun():
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

def add_ledger(entry: dict):
    entry["time"] = now_ts()
    st.session_state.ledger.append(entry)
    db_add_ledger(entry)

def animate_progress(old_val, new_val, total, placeholder):
    old_pct = old_val/total if total > 0 else 1.0
    new_pct = new_val/total if total > 0 else 1.0
    steps = 12
    for i in range(1, steps + 1):
        inter = old_pct + (new_pct - old_pct) * (i / steps)
        placeholder.progress(int(inter * 100))
        time.sleep(0.02)
    placeholder.progress(int(new_pct * 100))

# ------------------------------
# Auto-release logic (updates DB)
# ------------------------------
def try_auto_release(student):
    fully_funded = student["received"] >= student["need"]
    proof_uploaded = any(p["student_id"] == student["id"] for p in st.session_state.proofs)

    if fully_funded and proof_uploaded and not student.get("released", False):
        # mark release
        student["released"] = True

        # charge admin fee once
        if not student.get("admin_charged", False):
            admin_fee = round(student["need"] * ADMIN_FEE_RATE, 2)
            student_amount = round(student["need"] - admin_fee, 2)

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

        # add release ledger
        ledger_entry_release = {
            "type": "release",
            "student_id": student["id"],
            "student_name": student["name"],
            "amount_released": student["need"],
            "time": now_ts()
        }
        add_ledger(ledger_entry_release)

        # update student in DB and session
        db_update_student(student)

        # notify
        st.session_state.notification = {"message": f"🔓 Funds released to {student['name']}", "level": "info"}
        st.session_state.show_balloons = True

# ------------------------------
# UI: Styles (keeps previous simple style)
# ------------------------------
st.set_page_config(page_title="Charity Sponsor (SQLite)", layout="centered")
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
@media (max-width:600px){ .avatar { width:55px; height:55px; font-size:18px; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ------------------------------
# Notification (display queued)
# ------------------------------
if st.session_state.get("notification"):
    lev = st.session_state.notification.get("level", "success")
    cls = "success" if lev == "success" else "info"
    st.markdown(f"<div class='notice {cls}'>{st.session_state.notification['message']}</div>", unsafe_allow_html=True)
    if st.session_state.get("show_balloons"):
        try:
            st.balloons()
        except Exception:
            pass
        st.session_state.show_balloons = False
    st.session_state.notification = None

# ------------------------------
# Sidebar navigation
# ------------------------------
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Dashboard", "Make Donation", "Upload Proof", "Ledger", "Add Student"])

# ------------------------------
# Dashboard page
# ------------------------------
if page == "Dashboard":
    st.image("trustchain_logo1.png")
    # st.title("📊 Dashboard")

    # reload latest from session
    students = st.session_state.students
    ledger = st.session_state.ledger

    total_need = sum(s["need"] for s in students)
    total_received = sum(s["received"] for s in students)
    total_admin = sum(tx.get("admin_fee", 0) for tx in ledger if tx.get("type") == "admin_fee")
    total_released = sum(s["need"] if s.get("released") else 0 for s in students)

    c1, c2, c3 = st.columns(3)
    c1.metric("🎯 Total Need", f"${total_need:,.2f}")
    c2.metric("💰 Donated (credited)", f"${total_received:,.2f}")
    c3.metric("🏛 Admin Fee Collected", f"${total_admin:,.2f}")

    st.markdown("---")
    st.subheader("Funding Overview — Funded vs Remaining")
    # donut
    # rows = []
    # for s in students:
    #     funded = min(s["received"], s["need"])
    #     remain = max(s["need"] - s["received"], 0.0)
    #     rows.append({"student": s["name"], "label": "Funded", "value": funded})
    #     rows.append({"student": s["name"], "label": "Remaining", "value": remain})
    # df_pie = pd.DataFrame(rows)
    # if not df_pie.empty:
    #     pie_df = df_pie.copy()
    #     pie_df["id"] = pie_df["student"] + " — " + pie_df["label"]
    #     fig = px.pie(pie_df, names="id", values="value", hole=0.55, hover_data=["student", "label", "value"])
    #     fig.update_traces(textinfo='percent+label')
    #     st.plotly_chart(fig, use_container_width=True)
    # else:
    #     st.info("No data yet.")
    
    required_total = sum(s["need"] for s in st.session_state.students)
    received_total = sum(min(s["received"], s["need"]) for s in st.session_state.students)

    released_total = sum(
        s["need"] for s in st.session_state.students if s.get("released", False)
    )

    not_released_total = max(received_total - released_total, 0)
    remaining_total = max(required_total - received_total, 0)

    admin_fee_total = sum(
        float(tx.get("admin_fee", 0.0))
        for tx in st.session_state.ledger
        if tx.get("type") == "admin_fee"
    )

    labels = ["Funded - Not Released 🔒", "Funded - Released 🔓", "Remaining 🔄", "Admin Fee 🏛"]
    values = [not_released_total, released_total, remaining_total, admin_fee_total]

    # Custom color strategy
    colors = ["#FFC300", "#2ECC71", "#E74C3C", "#9B59B6"]  
    # Yellow, Green, Red, Purple

    fig = px.pie(
        values=values,
        names=labels,
        color=labels,
        color_discrete_map=dict(zip(labels, colors)),
        hole=0.55,
    )

    fig.update_traces(
        textinfo="percent+label",
        pull=[0.03, 0.02, 0, 0.05],
    )

    fig.update_layout(
        title="💸 Overall Funding Transparency Status",
        showlegend=True,
        margin=dict(t=40, l=0, r=0, b=0),
    )

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
        """,
        unsafe_allow_html=True
    )

    st.markdown("---")
    st.subheader("Students")
    for s in students:
        remaining = max(s["need"] - s["received"], 0.0)
        funded = s["received"] >= s["need"]
        proof_uploaded = any(p["student_id"] == s["id"] for p in st.session_state.proofs)
        released = s.get("released", False)
        initials = "".join([p[0] for p in s["name"].split()][:2]).upper()

        status_funded = (
            '<span class="badge badge-green">✔ Funded</span>'
            if funded else (
                '<span class="badge badge-yellow">⚠ Partial</span>'
                if s["received"] > 0 else
                '<span class="badge badge-red">❌ Not Funded</span>'
            )
        )
        status_proof = (
            '<span class="badge badge-green">📄 Proof</span>'
            if proof_uploaded else
            '<span class="badge badge-red">❌ No Proof</span>'
        )
        status_release = (
            '<span class="badge badge-green">🔓 Released</span>'
            if released else
            '<span class="badge badge-red">❌ Locked</span>'
        )

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="student-card">
                <div class="avatar">{initials}</div>
                <div style="flex:1">
                    <div style="font-weight:700">{s['name']}</div>
                    <div class="tiny">{s.get('story','')}</div>
                    <div class="row">
                        <div class="small"><b>Required:</b> ${s['need']:,.2f}</div>
                        <div class="small"><b>Received:</b> ${s['received']:,.2f}</div>
                        <div class="small"><b>Remaining:</b> ${remaining:,.2f}</div>
                    </div>
                    <div style="margin-top:8px">
                        {status_funded}
                        {status_proof}
                        {status_release}
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Make Donation page
# ------------------------------
elif page == "Make Donation":
    st.title("💝 Make Donation")
    st.write("Donate to a student. Admin fee is charged once when a student's funds are released (after proof upload).")

    students = st.session_state.students
    for idx, s in enumerate(students):
        remaining = max(s["need"] - s["received"], 0.0)
        funded = s["received"] >= s["need"]
        initials = "".join([x[0] for x in s["name"].split()][:2]).upper()

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="student-card">
                <div class="avatar">{initials}</div>
                <div style="flex:1">
                    <div style="font-weight:700">{s['name']}</div>
                    <div class="tiny">{s.get('story','')}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="row">
                <div class="small"><b>Required:</b> ${s['need']:,.2f}</div>
                <div class="small"><b>Remaining:</b> ${remaining:,.2f}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if funded:
            st.success("This student is fully funded — donation disabled.")
        else:
            # donation UI: slider + numeric sync
            max_slider = max(int(remaining), 50)
            slider_key = f"slider_{s['id']}"
            num_key = f"num_{s['id']}"
            if slider_key not in st.session_state:
                st.session_state[slider_key] = min(50, max_slider)
            col1, col2 = st.columns([3,1])
            slider_val = col1.slider("Amount", min_value=1, max_value=max_slider, value=st.session_state[slider_key], key=slider_key)
            col2.number_input("USD", min_value=1, value=slider_val, key=num_key)
            # keep in sync
            try:
                num_val = int(st.session_state[num_key])
                if num_val != slider_val:
                    st.session_state[slider_key] = num_val
                    slider_val = num_val
            except Exception:
                pass

            if st.button("Donate Now", key=f"donate_btn_{s['id']}"):
                amount = float(st.session_state[slider_key])
                old_received = s["received"]
                # ledger insert donation (no admin fee recorded now)
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
                # update student in session and DB
                s["received"] += amount
                # update DB
                db_update_student(s)

                # animate progress
                ph = st.empty()
                animate_progress(old_received, s["received"], s["need"], ph)

                st.session_state.notification = {"message": f"🎉 Donation ${amount:,.2f} recorded for {s['name']}", "level": "success"}
                st.session_state.show_balloons = True

                # try release if proof exists
                try_auto_release(s)
                safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Upload Proof page
# ------------------------------
elif page == "Upload Proof":
    st.title("📄 Upload Proof (NGO)")
    st.write("When a student reaches required amount, upload proof to trigger release.")

    if "proofs" not in st.session_state:
        st.session_state.proofs = load_proofs_from_db()

    for s in st.session_state.students:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader(s["name"])
        funded = s["received"] >= s["need"]
        proof_exists = any(p["student_id"] == s["id"] for p in st.session_state.proofs)

        if not funded:
            st.info("Student not fully funded yet; cannot upload proof.")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        if proof_exists:
            st.success("Proof already uploaded ✔")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        uploaded = st.file_uploader(f"Upload proof for {s['name']}", key=f"proof_{s['id']}")
        if uploaded and st.button(f"Submit Proof for {s['name']}", key=f"submit_proof_{s['id']}"):
            content = uploaded.read()
            file_hash = compute_hash(content)
            proof_record = {
                "student_id": s["id"],
                "student_name": s["name"],
                "filename": uploaded.name,
                "hash": file_hash,
                "time": now_ts()
            }
            # add to session and DB
            st.session_state.proofs.append(proof_record)
            db_add_proof(proof_record)

            # add ledger proof upload
            ledger_entry = {
                "type": "proof_upload",
                "student_id": s["id"],
                "student_name": s["name"],
                "filename": uploaded.name,
                "file_hash": file_hash,
                "time": now_ts()
            }
            add_ledger(ledger_entry)

            st.session_state.notification = {"message": f"📄 Proof uploaded for {s['name']}", "level": "success"}

            # try auto-release (this will write admin fee + release ledger and update student in DB)
            try_auto_release(s)
            # update student in DB for doc_hash and release flags
            s["doc_hash"] = file_hash
            db_update_student(s)

            safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# Ledger page (defensive rendering)
# ------------------------------
elif page == "Ledger":
    st.title("⛓ Ledger")
    # reload ledger from DB to ensure consistent ordering (optional)
    try:
        st.session_state.ledger = load_ledger_from_db()
    except Exception:
        pass

    if not st.session_state.ledger:
        st.info("No ledger records yet.")
    else:
        # show newest first
        for tx in reversed(st.session_state.ledger):
            t = tx.get("type", "").lower()

            # Donation entry
            if t == "donation":
                gross = float(tx.get("gross", 0.0))
                student_name = tx.get("student_name", "Unknown")
                time_str = tx.get("time", "")
                st.markdown(f"**💰 Donation** — {student_name} • ${gross:,.2f} • {time_str}")

            # Proof upload entry
            elif t == "proof_upload" or t == "proof":
                student_name = tx.get("student_name", "Unknown")
                filename = tx.get("filename", tx.get("file_name", ""))
                file_hash = tx.get("file_hash", tx.get("hash", ""))
                time_str = tx.get("time", "")
                st.markdown(f"**📄 Proof Uploaded** — {student_name} • {filename} • hash: `{file_hash}` • {time_str}")

            # Admin fee entry
            elif t == "admin_fee":
                student_name = tx.get("student_name", "Unknown")
                admin_fee = float(tx.get("admin_fee", 0.0))
                student_amount = float(tx.get("student_amount", 0.0))
                time_str = tx.get("time", "")
                st.markdown(f"**🏛 Admin Fee Charged** — {student_name} • fee: ${admin_fee:,.2f} • student receives: ${student_amount:,.2f} • {time_str}")

            # Release entry
            elif t == "release":
                student_name = tx.get("student_name", "Unknown")
                # fallback to gross or stored value if amount_released missing
                amount_released = float(tx.get("amount_released", tx.get("gross", 0.0)))
                time_str = tx.get("time", "")
                st.markdown(f"**🔓 Release** — {student_name} • ${amount_released:,.2f} • {time_str}")

            # Generic / unknown entry
            else:
                # Show the raw JSON for inspection
                st.json(tx)

        # Convert ledger list → DataFrame        
        df_ledger = pd.DataFrame(st.session_state.ledger)

        # Download CSV
        csv = df_ledger.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Ledger CSV",
            data=csv,
            file_name="trustchain_ledger.csv",
            mime="text/csv",
            type="secondary",
            use_container_width=True
        )


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
        # reload students into session state
        st.session_state.students = load_students_from_db()
        st.session_state.notification = {"message": f"👶 Student '{name}' added.", "level": "success"}
        safe_rerun()
