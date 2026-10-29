# CareerMatch AI — Local Development Guide

This guide provides step-by-step instructions for setting up, developing, testing, and debugging CareerMatch AI using Windows PowerShell.

---

## 1. Environment Setup

### 1.1 Python Virtual Environment
Open Windows PowerShell in the project root:
```powershell
# Create virtual environment
python -m venv .venv

# Set execution policy if script execution is restricted
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 1.2 Local Configuration File
Create your local `.env` from the provided template:
```powershell
Copy-Item .env.example .env
```
Edit `.env` to configure your database (`DATABASE_URL`) and authentication keys.

---

## 2. Running the Application

### 2.1 Start Local Development Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Once started, the following URLs are available:
- **Main Web Application**: `http://127.0.0.1:8000`
- **Interactive Swagger Docs**: `http://127.0.0.1:8000/docs`
- **ReDoc API Documentation**: `http://127.0.0.1:8000/redoc`
- **Developer API Playground**: `http://127.0.0.1:8000/dev-ui`

---

## 3. Database Management

### 3.1 SQLite vs. Neon PostgreSQL
- For zero-dependency local development, set:
  ```env
  DATABASE_URL=sqlite:///./app_dev.db
  ```
- For production-grade PostgreSQL with vector similarity, set:
  ```env
  DATABASE_URL=postgresql://<user>:<password>@<neon-endpoint>.neon.tech/neondb?sslmode=require
  ```
The `DatabaseManager` runs schema initialization and table creation idempotently on startup.

---

## 4. Quality Assurance & Testing

### 4.1 Run the Full Test Suite
```powershell
python -m pytest -v
```

### 4.2 Run Specific Test Suites
```powershell
# Authentication & Email Verification
python -m pytest tests/integration/test_firebase_verification_delivery_fix.py tests/integration/test_firebase_email_verification.py -v

# Matching & Dashboard Performance
python -m pytest tests/integration/test_dashboard_ranked_matches_performance.py tests/integration/test_real_matching_and_no_hardcoded_matches.py -v

# Career Advisory Agent & RAG
python -m pytest tests/integration/test_conversational_agent.py tests/integration/test_performance_optimization_and_career_agent.py -v
```

### 4.3 Code Quality & Formatting
```powershell
# Syntax & compile check
python -m py_compile app/main.py

# Lint with ruff or flake8 (if installed)
python -m flake8 app/ --max-line-length=120
```

