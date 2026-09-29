# Lab Job Manager

A Django app for role-based lab job management: Lab Heads create jobs and manage
instruments/technicians; Lab Technicians see only the jobs assigned to them.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install django
python manage.py migrate
python manage.py runserver
```

Visit `http://127.0.0.1:8000/signup/` to create your first account — sign up as
**Lab Head** to get full access (job creation, instruments, technicians).
Technicians are added by a Lab Head from the "Technicians" page (they don't
self-register with that role).

## How it fits the spec

- **Login/signup** (`/login/`, `/signup/`) — role chosen at signup (Lab Head or
  Lab Technician). Only Lab Heads can create jobs or manage instruments/technicians.
- **Job number** — auto-generated as `YYMM` + a 3-digit sequence that resets each
  month (e.g. `2609001`), shown on the job form and fixed on save
  (`Job.generate_job_number` in `labmanager/models.py`).
- **Certificates** — entering a job's `quantity` generates that many certificate
  records in sequence, formatted `AF-116444`, `AF-116445`, ... (`Certificate.generate_number`).
  These are the fixed pool for the job.
- **Instrument table** — dynamic rows (add/remove via JS, no page reload) with
  Instrument and Job Assigned To as dropdowns sourced from the Instruments and
  Technicians pages. Each row's `quantity` pulls that many certificates from the
  job's pool, in order (`JobLineItem.assign_certificates`), and the assigned range
  is shown on the job detail page.
- **Instruments / Technicians pages** (`/instruments/`, `/technicians/`) — simple
  add + activate/deactivate management; only active ones populate the job form
  dropdowns.
- **Documents** — a repeatable name + file upload section on the job form, stored
  per job under `media/job_documents/<job_number>/`.
- **Certificate detail** (`/certificates/<id>/`, linked from the job page) — each
  generated certificate has its own page for the full calibration-certificate
  record: device under test (manufacturer, serial, resolution, accuracy — model
  and range come from the instrument line item), working standard, calibration
  conditions (lab temp/humidity/ambient pressure/reference procedure/temp
  variation + notes), a dynamic multi-row results table (point, applied value,
  standard reading, UUT reading, error, uncertainty), and sign-off (calibration
  date, issue date, calibrated by, approved signatory). The job page shows a
  Complete/Pending status per certificate. A Lab Head can open any certificate;
  a technician can only open certificates from line items assigned to them.

## Notes / next steps

- Uses SQLite for simplicity; swap the `DATABASES` setting in
  `labsystem/settings.py` for Postgres/MySQL in production.
- Password reset and editing an already-created job (currently create-only) aren't
  built yet.
- For production: set `DEBUG = False`, a real `SECRET_KEY`, `ALLOWED_HOSTS`, and
  serve static/media files properly (not via Django's dev server).
