# CareerMatch AI — Troubleshooting Guide

This guide helps resolve common development and runtime issues in CareerMatch AI.

---

## 1. Google Sign-In Issues

### Symptom: Popup closes immediately or shows "auth/unauthorized-domain"
- **Cause**: The domain serving the frontend is not on the Firebase authorized domains list.
- **Solution**:
  1. Open the [Firebase Console](https://console.firebase.google.com/project/aihiring-3d070/authentication/settings).
  2. Navigate to **Authentication** $\rightarrow$ **Settings** $\rightarrow$ **Authorized domains**.
  3. Ensure `localhost` and `127.0.0.1` are in the list.

### Symptom: Google sign-in button stuck on "Connecting to Google..."
- **Cause**: Third-party cookies or popups are blocked by browser privacy extensions.
- **Solution**: Check browser shield settings, allow popups for `localhost:8000`, or use an incognito window.

---

## 2. Email Verification Issues

### Symptom: User creates account but never receives verification email
- **Cause 1 (Rate Limiting)**: Repeated registration requests trigger Google's `TOO_MANY_ATTEMPTS_TRY_LATER` / `auth/too-many-requests`. Wait 5 minutes or use the cooldown timer.
- **Cause 2 (Spam Filtering)**: Default emails sent from `noreply@aihiring-3d070.firebaseapp.com` are frequently flagged by spam/promotions filters in Gmail and Outlook. Check the **Spam** and **Promotions** tabs.
- **Cause 3 (Development Bypass)**: On the verification card, click **"Email not in inbox or spam? Open instant verification link"** to open the official Firebase action link generated via the backend service account.

---

## 3. PostgreSQL / Neon Connectivity Issues

### Symptom: `DatabaseError: gaierror - nodename nor servname provided`
- **Cause**: No internet connection, or DNS resolution failure connecting to the remote Neon PostgreSQL host.
- **Solution**:
  1. Verify active internet access.
  2. For offline local development, switch `DATABASE_URL` in `.env` to SQLite:
     ```env
     DATABASE_URL=sqlite:///./app_dev.db
     ```

---

## 4. Resume Processing & Profile Resolution

### Symptom: Candidate uploads CV but sees "403 Forbidden: EMAIL_NOT_VERIFIED"
- **Cause**: The candidate registered via email/password but has not verified their email address.
- **Solution**: Verify the email using the confirmation link sent to the inbox, or log in via Google to bypass verification.

### Symptom: "Unsupported document format" on upload
- **Cause**: The file signature is not recognized as a valid PDF.
- **Solution**: Ensure the uploaded file starts with the standard `%PDF-` magic header and is under 5MB.

---

## 5. Ranked Jobs & Dashboard Performance

### Symptom: Dashboard stuck on "Retrieving multi-factor match records..."
- **Cause**: The matching pipeline is evaluating 100+ jobs synchronously without cache records.
- **Solution**: In CareerMatch AI, `GET /api/v1/matches` queries persisted PostgreSQL match records first. If empty, the async background worker computes rankings without blocking the initial dashboard response.

---

## 6. Career Advisory Agent Issues

### Symptom: Agent says "I currently do not have access to your profile data"
- **Cause**: The candidate has not uploaded a resume yet, or the active resume version was not associated with the user ID.
- **Solution**: Upload a PDF resume in the **Resume Management** section. The Career Agent will immediately detect and ground its answers in the active profile.

