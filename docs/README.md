IgnividhaTech — Static site (docs/)

This `docs/` folder contains static HTML exports of the original Flask-based site so it can be served by GitHub Pages without a backend.

What is included
- `index.html` — Static homepage converted from `templates/dashboard.html`.
- `admin_login.html` — Static preview of the admin login page (login disabled).
- `admin_dashboard.html` — Static admin dashboard preview (controls disabled).
- `register.html` — Links to the public Google Form used for registrations.
- `contact.html` — Static contact page with an email link and a disabled contact form.

Notes & limitations
- All admin functionality (adding updates, managing registrations, exporting Excel, sending emails) requires the Flask server. These static pages are read-only previews.
- If you need dynamic admin capabilities, run the Flask app locally or deploy to a server that supports Python/Flask.

How to preview locally
1. Open `docs/index.html` in your browser directly, or start a simple HTTP server from the repo root:

   # PowerShell
   python -m http.server 8000

   Then open: http://localhost:8000/docs/index.html

Deploy to GitHub Pages
1. Commit and push the `docs/` folder to your `main` branch.
2. In your GitHub repository settings -> Pages, choose `Deploy from branch: main` and set the folder to `/docs`.
3. Save — your site should be published shortly.

Restoring full dynamic functionality
- To run the original Flask app (with admin, registration handling, Excel exports, and SMTP):
  1. Create a virtual environment and install dependencies from `requirements.txt`.
  2. Set environment variables (SECRET_KEY, SMTP_HOST, SMTP_FROM, ADMIN_TOKEN if desired) or use `.env` locally.
  3. Run `python app.py` and visit http://localhost:5000.

If you'd like, I can:
- Add `docs/404.html` and a small sitemap.
- Create a commit patch that adds/updates these files and a short PR message.
- Remove or archive the original `templates/` folder to avoid confusion.

Contact
- For deployment help, tell me whether you want me to prepare a commit (I can create a patch), or if you'd like CLI commands for pushing the changes yourself.