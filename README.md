# 🌟 Blockchain-Inspired Student Sponsorship Platform  
*A transparent donation lifecycle simulation built with **Python + Streamlit + SQLite***  

---

## 📌 Overview  
This project is a **mock blockchain-powered charity sponsorship platform** demonstrating how fintech, smart contracts, and transparency mechanisms can improve trust in charitable funding.

It simulates an end-to-end transparent workflow:

1. Donor funds a student  
2. Student becomes **fully funded**  
3. NGO uploads **proof of fund usage**  
4. App simulates **7-second smart-contract verification**  
5. Proof becomes **Verified**  
6. Funds automatically **Release** to the student  
7. Admin fee is charged **only once** at release  
8. A complete **Ledger** records every transaction  

This system is ideal for:
- 🎓 FinTech coursework & MBA demos  
- 🧪 Proof-of-concept blockchain charity apps  
- 🚀 Smart-contract simulation  
- 📱 Mobile-friendly donation prototypes  

---

## ✨ Key Features  
### 🔐 1. Transparent Smart-Contract Simulation  
- Auto fund release  
- Auto admin-fee calculation  
- Delayed verification (7 seconds)  
- Immutable-like ledger entries  

### 📤 2. Proof Upload with Restrictions  
- Accepts **PDF / PNG / JPG**  
- File preview inside expander  
- Upload disabled after first submission  

### 💸 3. Donation Engine  
- Prevents over-donation  
- Animated funding progress bar  
- Multi-student sponsorship  
- Donation records stored in ledger  

### 📊 4. Real-Time Dashboard  
Donut-style visualization of:
- Funded (Not Released)  
- Released  
- Remaining  
- Admin Fee  

Plus mobile-friendly student cards with badge statuses:
- ✔ Funded  
- 🔄 Reviewing  
- 📄 Verified  
- 🔓 Released  

### 🗃 5. SQLite Database Persistence  
Locally stores:
- Students  
- Donations  
- Proof documents  
- Ledger transactions  

### 📜 6. Full Ledger + CSV Export  
Tracks all event types:
- Donations  
- Proof submissions  
- Proof verification  
- Admin fee  
- Fund release  

### ➕ 7. Add Student Module  
Enter:
- Student name  
- Required amount  
- Short story  

---

## 🧱 Architecture
app.py
├── UI (Streamlit)
│ ├── Dashboard
│ ├── Donation
│ ├── Proof Upload
│ ├── Ledger
│ └── Add Student
│
├── Business Logic
│ ├── Donation handling
│ ├── Proof workflow (Review → Verify)
│ ├── Auto fund release
│ ├── Admin fee calculation
│ └── Ledger recording
│
├── Persistence Layer (SQLite)
│ ├── students.db
│ ├── students table
│ ├── ledger table
│ └── proofs table
│
└── File Storage
└── /uploads (PDF / Image files)
