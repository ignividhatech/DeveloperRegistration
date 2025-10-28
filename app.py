import os
import json
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    flash,
    url_for,
    send_file,
    abort,
    jsonify,
    session,
)
from dotenv import load_dotenv
from typing import List, Iterable, Optional
import smtplib
import time
from email.message import EmailMessage
from io import BytesIO
from openpyxl import Workbook

# Load environment variables from .env file for local development
load_dotenv()

# Initialize app
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


# Canonical list of positions used across the app
POSITIONS: List[str] = [
    'Frontend Developer',
    'Backend Developer',
    'Full Stack Developer',
    'App Developer',
]


# --- DEPLOYMENT-READY STORAGE CONFIGURATION ---

# Use the persistent disk path from the hosting environment,
# or a local folder '.' if running locally
DATA_DIR = os.environ.get('RENDER_DISK_PATH', '.')

# Ensure data directory exists so file writes don't fail on fresh deploys
os.makedirs(DATA_DIR, exist_ok=True)

# 1. Define paths for your data files using the DATA_DIR
REGISTRATIONS_FILE = os.path.join(DATA_DIR, 'registrations.json')
QUERIES_FILE = os.path.join(DATA_DIR, 'queries.json')
UPDATES_FILE = os.path.join(DATA_DIR, 'updates.json')

# Note: file upload handling removed; project files are no longer stored on the server.
# --- END OF STORAGE CONFIGURATION ---

# Mail templates storage file (after DATA_DIR is defined)
MAIL_TEMPLATES_FILE = os.path.join(DATA_DIR, 'mail_templates.json')
# Admin credentials storage (so you can change them from the dashboard)
ADMIN_CREDENTIALS_FILE = os.path.join(DATA_DIR, 'admin_credentials.json')
# Site content storage (editable from Admin)
SITE_CONTENT_FILE = os.path.join(DATA_DIR, 'site_content.json')


# File upload helpers removed. Project files are no longer stored on disk.


def _load_list(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Backfill missing review_status as 'pending'
            if isinstance(data, list):
                for r in data:
                    if isinstance(r, dict) and 'review_status' not in r:
                        r['review_status'] = 'pending'
            return data
    except Exception:
        return []


def _save_list(path, data_list):
    # Make sure the containing directory exists before attempting to write
    parent = os.path.dirname(path) or '.'
    os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data_list, f, ensure_ascii=False, indent=2)


def _require_admin():
    """Guard for admin-only routes.
    Allows access if:
      - Flask session has 'admin_logged_in' set, OR
      - A valid ADMIN_TOKEN is provided via query/form/header.
    """
    if session.get('admin_logged_in'):
        return
    expected = os.environ.get('ADMIN_TOKEN')
    if expected:
        token = (
            request.args.get('token')
            or request.form.get('token')
            or request.headers.get('X-Admin-Token')
        )
        if token == expected:
            return
    abort(403)


def _load_updates_list():
    """Load updates from UPDATES_FILE or legacy notifications.json.
    Returns a list of strings, falling back to a default if empty.
    """
    data = []
    try:
        if os.path.exists(UPDATES_FILE):
            with open(UPDATES_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            msg = (
                                item.get('message')
                                or item.get('text')
                                or item.get('title')
                            )
                            if msg:
                                data.append(str(msg))
                        else:
                            data.append(str(item))
        else:
            legacy_path = os.path.join(DATA_DIR, 'notifications.json')
            if os.path.exists(legacy_path):
                with open(legacy_path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                    if isinstance(raw, list):
                        data = [str(x) for x in raw]
    except Exception:
        data = []

    if not data:
        data = ['Registration will open soon.']
    return data


@app.route('/')
def home():
    update_list = _load_updates_list()
    update = update_list[0] if update_list else 'Registration will open soon.'
    return render_template(
        'dashboard.html',
        update=update,
        updates=update_list,
        site_content=_load_site_content(),
    )


@app.route('/api/updates')
def api_updates():
    """Simple API to verify what updates the server sees.
    Add no-cache headers so clients always get fresh data.
    """
    resp = jsonify({'updates': _load_updates_list()})
    resp.headers['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # Basic info
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        contact = request.form.get('contact')
        status = request.form.get('status')
        positions = request.form.getlist('position[]')

        if not fullname or not email or not contact or not status:
            flash("Please fill all required fields.", "error")
            return redirect(request.url)

        # Date of birth and age validation
        dob_str = request.form.get('dob')
        age_from_form = request.form.get('age')
        age = None
        try:
            if age_from_form:
                age = int(age_from_form)
            elif dob_str:
                dob = datetime.strptime(dob_str, '%Y-%m-%d')
                today = datetime.now()
                # compute age accounting for whether birthday passed this year
                age = (
                    today.year
                    - dob.year
                    - ((today.month, today.day) < (dob.month, dob.day))
                )
        except Exception:
            age = None

        if age is None:
            flash("Please provide a valid Date of Birth.", "error")
            return redirect(request.url)

        # Enforce age limits: 18 to 27 inclusive
        if age < 18 or age > 27:
            flash(
                (
                    f"Age {age} is not eligible. Applicants must be "
                    f"between 18 and 27 years old."
                ),
                "error",
            )
            return redirect(request.url)

    # Enforce 1-2 positions selected
        if len(positions) < 1 or len(positions) > 2:
            flash("Please select at least 1 and at most 2 positions.", "error")
            return redirect(request.url)


        # Dynamic projects — collect links only. File upload support removed.
        project_links = []
        project_files = []
        if status == 'Student':
            link_list = request.form.getlist(
                'studentProjectsContainer_project_link[]'
            )
        else:
            link_list = request.form.getlist(
                'graduateProjectsContainer_project_link[]'
            )
        for link in link_list:
            if link:
                project_links.append(link)



        # Socials (mandatory: GitHub and LinkedIn)
        social_media = {
            'github': request.form.get('github', '').strip(),
            'linkedin': request.form.get('linkedin', '').strip(),
            'instagram': request.form.get('instagram', '').strip(),
            'portfolio': request.form.get('portfolio', '').strip(),
        }
        if (not social_media['github'] or not social_media['linkedin']):
            flash("GitHub and LinkedIn profile links are required.", "error")
            return redirect(request.url)

        # Load existing, assign id, save
        regs = _load_list(REGISTRATIONS_FILE)
        next_id = 1 + max([r.get('id', 0) for r in regs] or [0])
        registration = {
            'id': next_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'fullname': fullname,
            'email': email,
            'contact': contact,
            'age': age,
            'status': status,
            # Admin review status options:
            # 'pending' | 'selected' | 'rejected' | 'paused'
            'review_status': 'pending',
            'positions': positions,
            'project_links': project_links,
            'project_files': project_files,
            # (Profile photo, identity proof and other removed fields are not collected)
            'social_media': social_media,
        }
        regs.insert(0, registration)
        _save_list(REGISTRATIONS_FILE, regs)

        flash("Registration submitted successfully!", "success")
        return redirect(url_for('register'))

    return render_template('register.html', site_content=_load_site_content())


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        message = request.form.get('message')
        if not name or not email or not message:
            flash("Please fill all fields.", "error")
            return redirect(request.url)

        qs = _load_list(QUERIES_FILE)
        qs.append({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': name,
            'email': email,
            'message': message,
        })
        _save_list(QUERIES_FILE, qs)
        flash("Your query has been submitted successfully!", "success")
        return redirect(url_for('contact'))

    return render_template('contact.html', site_content=_load_site_content())


# --- SIMPLE SESSION-BASED ADMIN AUTH ---

def _default_admin_credentials():
    return {"username": "Umeshkiran", "password": "Umesh@kiran"}


def _load_admin_credentials():
    try:
        if os.path.exists(ADMIN_CREDENTIALS_FILE):
            with open(ADMIN_CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if (
                    isinstance(data, dict)
                    and data.get('username')
                    and data.get('password')
                ):
                    return data
    except Exception:
        pass
    # Initialize with defaults if missing or invalid
    creds = _default_admin_credentials()
    _save_admin_credentials(creds)
    return creds


def _save_admin_credentials(creds: dict):
    try:
        parent = os.path.dirname(ADMIN_CREDENTIALS_FILE) or '.'
        os.makedirs(parent, exist_ok=True)
        with open(ADMIN_CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'username': (
                        creds.get('username')
                        or _default_admin_credentials()['username']
                    ),
                    'password': (
                        creds.get('password')
                        or _default_admin_credentials()['password']
                    ),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        creds = _load_admin_credentials()
        if (
            username == creds.get('username')
            and password == creds.get('password')
        ):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            flash('Logged in successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    flash('Logged out.', 'success')
    return redirect(url_for('admin_login'))


@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    updates = _load_updates_list()
    return render_template(
        'admin_dashboard.html',
        updates=updates,
        admin_username=session.get('admin_username'),
        current_username=_load_admin_credentials().get('username'),
        site_content=_load_site_content(),
        positions=POSITIONS,
        mail_templates=_load_mail_templates(),
    )


def _save_updates_list(items: List[str]):
    """Persist updates as a list of strings to UPDATES_FILE."""
    try:
        with open(UPDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@app.route('/admin/updates/add', methods=['POST'])
def admin_add_update():
    _require_admin()
    msg = (request.form.get('message') or '').strip()
    if not msg:
        flash('Update message cannot be empty.', 'error')
        return redirect(url_for('admin_dashboard'))
    items = _load_updates_list()
    items.insert(0, msg)
    _save_updates_list(items)
    flash('Update added.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/updates/delete/<int:index>', methods=['POST'])
def admin_delete_update(index: int):
    _require_admin()
    items = _load_updates_list()
    if 0 <= index < len(items):
        items.pop(index)
        _save_updates_list(items)
        flash('Update deleted.', 'success')
    else:
        flash('Invalid update index.', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/settings/credentials', methods=['POST'])
def admin_update_credentials():
    _require_admin()
    new_user = (request.form.get('new_username') or '').strip()
    new_pass = (request.form.get('new_password') or '').strip()
    if not new_user or not new_pass:
        flash('Username and Password cannot be empty.', 'error')
        return redirect(url_for('admin_dashboard'))
    _save_admin_credentials({'username': new_user, 'password': new_pass})
    session['admin_username'] = new_user
    flash('Admin credentials updated.', 'success')
    return redirect(url_for('admin_dashboard'))


# --- SITE CONTENT (Admin-editable homepage content) ---
def _default_site_content():
    return {
        'hero': {
            'badge': 'PAID OPPORTUNITY',
            'title': 'IgnividhaTech',
            'subtitle': 'Developer Recruitment',
            'ctaLabel': 'Register Now',
            'ctaLink': '/register',
        },
        'about': {
            'leftText': (
                'Join our remote developer team on a project-by-project '
                'basis. Earn stipends for completed work, not a fixed salary.'
            ),
            'rightText': (
                'Gain real-world experience on exciting projects with '
                'flexible hours. Work from anywhere and meet project '
                'deadlines to build your portfolio.'
            ),
        },
        'positions': {
            'frontend': (
                'Build responsive user interfaces and web applications.'
            ),
            'backend': (
                'Design APIs, manage databases, and write server-side logic.'
            ),
            'fullstack': (
                'Handle both client-side and server-side infrastructure.'
            ),
            'app': 'Develop Android/iOS apps focused on performance.',
        },
        'qualifications': {
            'coreRequirement': (
                'Provide relevant certificates or proof of experience if available. '
                'Project samples or links are recommended to showcase your work.'
            ),
            'students': [
                'Include project or academic work samples to showcase skills.',
            ],
            'graduates': [
                'Include project or professional work samples to showcase experience.',
            ],
        },
        # Registration page configurable content
        'registerPage': {
            'title': 'Developer Recruitment Registration Form',
            'intro': (
                'Fill the form carefully. Eligibility: Age 18–27; '
                'select up to 2 positions.'
            ),
            'fee': {
                'total': '₹125',
                'refundable': '₹100',
                'verification': '₹25',
                'note': (
                    '₹100 refundable if not selected; ₹25 non-refundable '
                    'verification fee.'
                )
            }
        },
        # Contact page configurable content
        'contactPage': {
            'title': 'Contact Us',
            'subtitle': (
                'Get in touch with IgnividhaTech or send us your query'
            ),
            'companyName': 'IgnividhaTech',
            'email': 'tatvatech2225@gmail.com',
            'location': 'Hyderabad, India',
            'workMode': 'Remote / Work From Home',
        },
    }


def _load_site_content():
    try:
        if os.path.exists(SITE_CONTENT_FILE):
            with open(SITE_CONTENT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Merge with defaults to ensure keys exist
                base = _default_site_content()

                def merge(a, b):
                    if isinstance(a, dict) and isinstance(b, dict):
                        out = dict(a)
                        for k, v in b.items():
                            out[k] = merge(out.get(k), v)
                        return out
                    return b if b is not None else a
                return merge(base, data)
    except Exception:
        pass
    return _default_site_content()


def _save_site_content(data: dict):
    os.makedirs(os.path.dirname(SITE_CONTENT_FILE), exist_ok=True)
    with open(SITE_CONTENT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/admin/settings/site_content', methods=['POST'])
def admin_save_site_content():
    _require_admin()
    content = _load_site_content()
    # Hero
    content['hero']['badge'] = (
        (request.form.get('hero_badge') or '').strip()
        or content['hero']['badge']
    )
    content['hero']['title'] = (
        (request.form.get('hero_title') or '').strip()
        or content['hero']['title']
    )
    content['hero']['subtitle'] = (
        (request.form.get('hero_subtitle') or '').strip()
        or content['hero']['subtitle']
    )
    content['hero']['ctaLabel'] = (
        (request.form.get('cta_label') or '').strip()
        or content['hero']['ctaLabel']
    )
    link = (request.form.get('cta_link') or '').strip()
    if link:
        content['hero']['ctaLink'] = link
    # About
    a_left = (request.form.get('about_left') or '').strip()
    a_right = (request.form.get('about_right') or '').strip()
    if a_left:
        content['about']['leftText'] = a_left
    if a_right:
        content['about']['rightText'] = a_right
    # Positions
    pos = content.get('positions', {})
    pf = (request.form.get('pos_frontend') or '').strip()
    pb = (request.form.get('pos_backend') or '').strip()
    pfs = (request.form.get('pos_fullstack') or '').strip()
    pa = (request.form.get('pos_app') or '').strip()
    if pf:
        pos['frontend'] = pf
    if pb:
        pos['backend'] = pb
    if pfs:
        pos['fullstack'] = pfs
    if pa:
        pos['app'] = pa
    content['positions'] = pos
    # Qualifications
    q = content.get('qualifications', {})
    qc = (request.form.get('qual_core') or '').strip()
    if qc:
        q['coreRequirement'] = qc
    qs = (request.form.get('qual_students') or '').strip()
    qg = (request.form.get('qual_graduates') or '').strip()
    if qs:
        q['students'] = [
            line.strip()
            for line in qs.splitlines()
            if line.strip()
        ]
    if qg:
        q['graduates'] = [
            line.strip()
            for line in qg.splitlines()
            if line.strip()
        ]
    content['qualifications'] = q

    # Registration Page
    rp = content.get('registerPage', {})
    r_title = (request.form.get('reg_title') or '').strip()
    r_intro = (request.form.get('reg_intro') or '').strip()
    if r_title:
        rp['title'] = r_title
    if r_intro:
        rp['intro'] = r_intro
    fee = rp.get('fee', {})
    f_total = (request.form.get('fee_total') or '').strip()
    f_ref = (request.form.get('fee_refundable') or '').strip()
    f_ver = (request.form.get('fee_verification') or '').strip()
    f_note = (request.form.get('fee_note') or '').strip()
    if f_total:
        fee['total'] = f_total
    if f_ref:
        fee['refundable'] = f_ref
    if f_ver:
        fee['verification'] = f_ver
    if f_note:
        fee['note'] = f_note
    rp['fee'] = fee
    content['registerPage'] = rp

    # Contact Page
    cp = content.get('contactPage', {})
    c_title = (request.form.get('contact_title') or '').strip()
    c_sub = (request.form.get('contact_subtitle') or '').strip()
    c_company = (request.form.get('contact_company') or '').strip()
    c_email = (request.form.get('contact_email') or '').strip()
    c_loc = (request.form.get('contact_location') or '').strip()
    c_mode = (request.form.get('contact_workmode') or '').strip()
    if c_title:
        cp['title'] = c_title
    if c_sub:
        cp['subtitle'] = c_sub
    if c_company:
        cp['companyName'] = c_company
    if c_email:
        cp['email'] = c_email
    if c_loc:
        cp['location'] = c_loc
    if c_mode:
        cp['workMode'] = c_mode
    content['contactPage'] = cp

    _save_site_content(content)
    flash('Site content updated.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/registrations')
def view_registrations():
    _require_admin()
    # Show only pending entries on the main registrations page
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'pending'
    ]
    return render_template('registrations.html', registrations=regs)


def _set_review_status(reg_id: int, new_status: str):
    regs = _load_list(REGISTRATIONS_FILE)
    updated = False
    for r in regs:
        if r.get('id') == reg_id:
            r['review_status'] = new_status
            updated = True
            break
    if updated:
        _save_list(REGISTRATIONS_FILE, regs)
    return updated


def _set_selected_with_position(reg_id: int, position: str):
    """Set a registration to selected with a chosen position.
    Returns True if updated, else False.
    """
    regs = _load_list(REGISTRATIONS_FILE)
    for r in regs:
        if r.get('id') == reg_id:
            positions = r.get('positions') or []
            if position not in positions:
                return False
            r['review_status'] = 'selected'
            r['selected_position'] = position
            _save_list(REGISTRATIONS_FILE, regs)
            return True
    return False


@app.route('/admin/registrations/select/<int:reg_id>', methods=['POST'])
def select_registration(reg_id):
    _require_admin()
    sel_pos = request.form.get('selected_position', '').strip()
    if not sel_pos:
        flash('Please choose a position to select for.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_registrations', token=token))

    ok = _set_selected_with_position(reg_id, sel_pos)
    if not ok:
        flash('Invalid position for this candidate.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_registrations', token=token))

    flash('Marked as Selected.', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_selected', token=token,
                            position=sel_pos))


@app.route('/admin/registrations/reject/<int:reg_id>', methods=['POST'])
def reject_registration(reg_id):
    _require_admin()
    _set_review_status(reg_id, 'rejected')
    flash('Marked as Rejected.', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_rejected', token=token))


@app.route('/admin/registrations/pause/<int:reg_id>', methods=['POST'])
def pause_registration(reg_id):
    _require_admin()
    _set_review_status(reg_id, 'paused')
    flash('Moved to Paused.', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_paused', token=token))


@app.route('/admin/selected')
def view_selected():
    _require_admin()
    all_regs = _load_list(REGISTRATIONS_FILE)
    regs = [r for r in all_regs if r.get('review_status') == 'selected']
    pos = request.args.get('position')
    if pos:
        regs = [r for r in regs if r.get('selected_position') == pos]
    # Use the canonical positions list for consistent filters
    positions = POSITIONS
    return render_template('selected.html',
                           registrations=regs,
                           positions=positions,
                           active_position=pos)


@app.route('/admin/positions')
def view_positions():
    """View selected candidates grouped (or filtered) by selected_position."""
    _require_admin()
    all_regs = _load_list(REGISTRATIONS_FILE)
    selected_regs = [
        r for r in all_regs if r.get('review_status') == 'selected'
    ]
    # Build groups by selected_position, ensuring all known positions exist
    groups = {p: [] for p in POSITIONS}
    for r in selected_regs:
        pos = r.get('selected_position')
        if pos in groups:
            groups[pos].append(r)

    # Optional filter via query string ?position=
    active = request.args.get('position')
    templates = _load_mail_templates()
    if active:
        filtered = {active: groups.get(active, [])}
        return render_template(
            'positions.html',
            groups=filtered,
            positions=POSITIONS,
            active_position=active,
            mail_templates=templates,
        )

    # Load mail templates for UI defaults
    # (already loaded above as 'templates')

    return render_template(
        'positions.html',
        groups=groups,
        positions=POSITIONS,
        active_position=None,
        mail_templates=templates,
    )


# --- EMAIL UTILITIES ---
def _smtp_configured():
    return bool(
        os.environ.get('SMTP_HOST') and os.environ.get('SMTP_FROM')
    )


def _send_bulk_email(
    subject: str,
    body: str,
    recipients: Iterable[str],
    batch_size: Optional[int] = None,
    delay_seconds: float = 0,
):
    """Send a single email to multiple recipients via BCC.
    Uses environment variables:
      SMTP_HOST (required), SMTP_PORT (optional, default 587),
      SMTP_USER (optional), SMTP_PASSWORD (optional),
      SMTP_USE_TLS (optional, 'true'/'false', default 'true'),
      SMTP_FROM (required)
    Returns (sent_count, error_message_or_None)
    """
    host = os.environ.get('SMTP_HOST')
    from_addr = os.environ.get('SMTP_FROM')
    if not host or not from_addr:
        return 0, 'SMTP is not configured (missing SMTP_HOST/SMTP_FROM).'

    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASSWORD')
    use_tls = str(os.environ.get('SMTP_USE_TLS', 'true')).lower() in (
        '1', 'true', 'yes', 'on'
    )

    to_list = list({e.strip().lower() for e in recipients if e})
    if not to_list:
        return 0, None
    # batching
    try:
        bsz = int(batch_size) if batch_size else int(
            os.environ.get('SMTP_BATCH_SIZE', '50')
        )
    except Exception:
        bsz = 50
    try:
        delay = float(delay_seconds) if delay_seconds else float(
            os.environ.get('SMTP_BATCH_DELAY', '0')
        )
    except Exception:
        delay = 0.0

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if use_tls:
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    # continue even if TLS not supported
                    pass
            if user and password:
                server.login(user, password)
            sent = 0
            errors: List[str] = []
            # Iterate in chunks
            for i in range(0, len(to_list), max(1, bsz)):
                chunk = to_list[i:i + max(1, bsz)]
                msg = EmailMessage()
                msg['Subject'] = subject or '(no subject)'
                # some servers require a To: address even if BCC is used
                msg['To'] = from_addr
                msg['From'] = from_addr
                msg.set_content(body or '')
                try:
                    server.send_message(
                        msg, from_addr=from_addr, to_addrs=chunk
                    )
                    sent += len(chunk)
                except Exception as be:
                    errors.append(str(be))
                if delay > 0 and i + bsz < len(to_list):
                    time.sleep(delay)
        if errors:
            return sent, '; '.join(errors)
        return sent, None
    except Exception as e:
        return 0, str(e)


@app.route('/admin/positions/send_mail', methods=['POST'])
def send_mail_to_position():
    _require_admin()
    position = request.form.get('position', '').strip()
    subject = request.form.get('subject', '').strip()
    body = request.form.get('body', '').strip()

    if not position:
        flash('Position is required.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_positions', token=token))

    # Get selected candidates for this position
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'selected'
        and r.get('selected_position') == position
    ]
    recipients = [r.get('email') for r in regs if r.get('email')]

    # If subject/body not provided, use saved template for this position
    tmpl = _load_mail_templates().get(position, {})
    if not subject:
        subject = tmpl.get('subject', '')
    if not body:
        body = tmpl.get('body', '')

    if not _smtp_configured():
        flash(
            (
                'Email not sent: SMTP is not configured. '
                'Set SMTP_HOST, SMTP_FROM (and optionally SMTP_PORT, '
                'SMTP_USER, SMTP_PASSWORD, SMTP_USE_TLS).'
            ),
            'error',
        )
        token = request.args.get('token') or request.form.get('token')
        # Stay on the same filtered view if present
        return redirect(url_for(
            'view_positions', token=token, position=position
        ))

    sent_count, err = _send_bulk_email(subject, body, recipients)
    if err:
        flash(f'Failed to send email: {err}', 'error')
    else:
        flash(
            (
                f'Sent email to {sent_count} recipient(s) '
                f'for {position}.'
            ),
            'success',
        )

    token = request.args.get('token') or request.form.get('token')
    return redirect(url_for('view_positions', token=token, position=position))


# --- MAIL TEMPLATES LOAD/SAVE ---
def _default_mail_templates():
    base = {}
    for p in POSITIONS:
        base[p] = {
            'subject': f"Update for {p}",
            'body': (
                "Hello,\n\n"
                "This is an update regarding your selection for "
                f"{p}.\n"
                "We will share next steps shortly.\n\n"
                "Regards,\nTeam"
            ),
        }
    return base


def _load_mail_templates():
    # Ensure file exists with defaults
    try:
        if not os.path.exists(MAIL_TEMPLATES_FILE):
            data = _default_mail_templates()
            _save_mail_templates(data)
            return data
        with open(MAIL_TEMPLATES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}

    # Fill missing positions with defaults
    changed = False
    for p in POSITIONS:
        if p not in data or not isinstance(data.get(p), dict):
            data[p] = _default_mail_templates()[p]
            changed = True
        else:
            if 'subject' not in data[p]:
                data[p]['subject'] = _default_mail_templates()[p]['subject']
                changed = True
            if 'body' not in data[p]:
                data[p]['body'] = _default_mail_templates()[p]['body']
                changed = True
    if changed:
        _save_mail_templates(data)
    return data


def _save_mail_templates(data: dict):
    os.makedirs(os.path.dirname(MAIL_TEMPLATES_FILE), exist_ok=True)
    with open(MAIL_TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/admin/positions/save_template', methods=['POST'])
def save_mail_template():
    _require_admin()
    position = request.form.get('position', '').strip()
    subject = request.form.get('subject', '').strip()
    body = request.form.get('body', '').strip()
    if not position:
        flash('Position is required.', 'error')
    else:
        data = _load_mail_templates()
        data[position] = {
            'subject': subject or data.get(position, {}).get('subject', ''),
            'body': body or data.get(position, {}).get('body', ''),
        }
        _save_mail_templates(data)
        flash(f'Template saved for {position}.', 'success')
    token = request.args.get('token') or request.form.get('token')
    return redirect(url_for('view_positions', token=token, position=position))


@app.route('/admin/positions/save_templates_bulk', methods=['POST'])
def save_mail_templates_bulk():
    """Save multiple position email templates in one submission.
    Input field names expected:
      subject_<slug>, body_<slug> for each position, where slug is
      lowercased position with spaces replaced by underscores.
    """
    _require_admin()
    data = _load_mail_templates()

    def _slug(name: str) -> str:
        return (name or '').lower().replace(' ', '_')

    changed = False
    for pos in POSITIONS:
        s_key = f"subject_{_slug(pos)}"
        b_key = f"body_{_slug(pos)}"
        subject = (request.form.get(s_key) or '').strip()
        body = (request.form.get(b_key) or '').strip()
        if subject:
            data.setdefault(pos, {})['subject'] = subject
            changed = True
        if body:
            data.setdefault(pos, {})['body'] = body
            changed = True

    if changed:
        _save_mail_templates(data)
        flash('Email templates saved.', 'success')
    else:
        flash('No changes provided for templates.', 'error')

    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('admin_dashboard', token=token))


@app.route('/admin/rejected')
def view_rejected():
    _require_admin()
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'rejected'
    ]
    return render_template('rejected.html', registrations=regs)


@app.route('/admin/rejected/delete_all', methods=['POST'])
def delete_all_rejected():
    """Delete all registrations marked as rejected."""
    _require_admin()
    regs = _load_list(REGISTRATIONS_FILE)
    kept = [r for r in regs if r.get('review_status') != 'rejected']
    _save_list(REGISTRATIONS_FILE, kept)
    flash('All rejected registrations deleted.', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_rejected', token=token))


@app.route('/admin/rejected/delete_selected', methods=['POST'])
def delete_selected_rejected():
    """Delete selected rejected registrations (IDs provided in form)."""
    _require_admin()
    try:
        raw_ids = request.form.getlist('selected_ids')
        ids = {int(x) for x in raw_ids if x}
    except Exception:
        ids = set()
    if not ids:
        flash('No candidates selected to delete.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_rejected', token=token))

    regs = _load_list(REGISTRATIONS_FILE)
    kept = []
    removed = 0
    for r in regs:
        if r.get('review_status') == 'rejected' and r.get('id') in ids:
            removed += 1
            continue
        kept.append(r)
    _save_list(REGISTRATIONS_FILE, kept)
    flash(f'Deleted {removed} rejected registration(s).', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_rejected', token=token))


@app.route('/admin/registrations/send_mail_single', methods=['POST'])
def send_mail_single():
    """Send an email to a single registration by ID.
    Falls back to a default message if subject/body are not provided.
    """
    _require_admin()
    try:
        reg_id = int(request.form.get('reg_id', '0'))
    except Exception:
        reg_id = 0
    subject = (request.form.get('subject') or '').strip()
    body = (request.form.get('body') or '').strip()

    # Find the registration
    regs = _load_list(REGISTRATIONS_FILE)
    reg = next((r for r in regs if r.get('id') == reg_id), None)
    if not reg:
        flash('Invalid candidate.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_rejected', token=token))

    email = (reg.get('email') or '').strip()
    if not email:
        flash('Candidate has no email on record.', 'error')
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_rejected', token=token))

    # Default message if not provided
    if not subject:
        subject = 'Regarding your application to IgnividhaTech'
    if not body:
        fullname = reg.get('fullname') or 'Candidate'
        body = (
            f"Hello {fullname},\n\n"
            "Thank you for your interest in IgnividhaTech. "
            "After careful review, we won’t be moving forward with your "
            "application at this time.\n\n"
            "We appreciate the time you invested and encourage you to "
            "apply again in the future.\n\n"
            "Regards,\nIgnividhaTech Team"
        )

    if not _smtp_configured():
        flash(
            'Email not sent: SMTP is not configured on the server.',
            'error',
        )
        token = request.args.get('token') or request.form.get('token')
        return redirect(url_for('view_rejected', token=token))

    sent_count, err = _send_bulk_email(subject, body, [email])
    if err:
        flash(f'Failed to send email: {err}', 'error')
    else:
        flash(f'Email sent to {email}.', 'success')

    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_rejected', token=token))


@app.route('/admin/paused')
def view_paused():
    _require_admin()
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'paused'
    ]
    return render_template('paused.html', registrations=regs)


@app.route('/admin/registrations/delete/<int:reg_id>', methods=['POST'])
def delete_registration(reg_id):
    _require_admin()
    regs = _load_list(REGISTRATIONS_FILE)
    new_regs = [r for r in regs if r.get('id') != reg_id]
    _save_list(REGISTRATIONS_FILE, new_regs)
    flash('Registration deleted successfully!', 'success')
    token = request.args.get('token') or request.form.get('token')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('view_registrations', token=token))


@app.route('/admin/queries')
def view_queries():
    _require_admin()
    qs = _load_list(QUERIES_FILE)
    return render_template('queries.html', queries=qs)


# Uploads endpoint removed — project files are no longer stored/served from the server.


# --- EXCEL EXPORTS ---
def _selected_rows_for_excel(position: Optional[str] = None):
    """Return rows for Excel: [Name, Position, Email, Contact]."""
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'selected'
    ]
    if position:
        regs = [
            r for r in regs if r.get('selected_position') == position
        ]
    rows = []
    for r in regs:
        rows.append([
            r.get('fullname') or '',
            r.get('selected_position') or '',
            r.get('email') or '',
            r.get('contact') or '',
        ])
    return rows


def _excel_response(rows: List[List[str]], filename: str):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Selected'
    ws.append(['Student Name', 'Position', 'Email', 'Contact Number'])
    for row in rows:
        ws.append(row)
    # Optional: widen columns a bit
    for col, width in zip(['A', 'B', 'C', 'D'], [22, 18, 28, 18]):
        ws.column_dimensions[col].width = width
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )


@app.route('/admin/selected/download')
def download_selected_excel():
    _require_admin()
    pos = request.args.get('position')
    rows = _selected_rows_for_excel(pos)
    if pos:
        safe = pos.replace(' ', '_')
        fname = f'selected_{safe}.xlsx'
    else:
        fname = 'selected_all_positions.xlsx'
    return _excel_response(rows, fname)


@app.route('/admin/positions/download')
def download_position_excel():
    _require_admin()
    pos = (request.args.get('position') or '').strip()
    if not pos:
        flash('Position is required to download.', 'error')
        token = request.args.get('token')
        return redirect(url_for('view_positions', token=token))
    rows = _selected_rows_for_excel(pos)
    safe = pos.replace(' ', '_')
    fname = f'selected_{safe}.xlsx'
    return _excel_response(rows, fname)


@app.route('/admin/rejected/download')
def download_rejected_excel():
    """Download rejected candidates with only Name and Email in Excel."""
    _require_admin()
    regs = [
        r for r in _load_list(REGISTRATIONS_FILE)
        if r.get('review_status') == 'rejected'
    ]
    # Build two-column rows: Name, Email
    wb = Workbook()
    ws = wb.active
    ws.title = 'Rejected'
    ws.append(['Student Name', 'Email'])
    for r in regs:
        ws.append([
            r.get('fullname') or '',
            r.get('email') or '',
        ])
    # Optional: widths
    ws.column_dimensions['A'].width = 24
    ws.column_dimensions['B'].width = 30
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name='rejected_candidates.xlsx',
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )
# --- HEALTH CHECK ENDPOINT ---


@app.route('/healthz')
def healthz():
    """Render key templates to verify Jinja/context health.
    Returns JSON summary and 200 if all pass, else 500.
    """
    results: dict[str, dict] = {}

    def check(name: str, func):
        try:
            html = func()
            # Rendered HTML length; jsonify needs serializable values
            results[name] = {
                'ok': True,
                'len': len(html) if hasattr(html, '__len__') else 0,
            }
        except Exception as e:
            results[name] = {'ok': False, 'error': str(e)}

    # Check dashboard, register, contact templates using the same context
    check('dashboard', lambda: render_template(
        'dashboard.html',
        update=(_load_updates_list()[0]
                if _load_updates_list() else 'Registration will open soon.'),
        updates=_load_updates_list(),
        site_content=_load_site_content(),
    ))
    check('register', lambda: render_template(
        'register.html', site_content=_load_site_content()
    ))
    check('contact', lambda: render_template(
        'contact.html', site_content=_load_site_content()
    ))

    all_ok = all(v.get('ok') for v in results.values())
    resp = jsonify({
        'status': 'ok' if all_ok else 'degraded',
        'results': results,
        'time': datetime.now().isoformat(timespec='seconds'),
    })
    # Avoid caching
    resp.headers['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    resp.status_code = 200 if all_ok else 500
    return resp


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=True,
    )
