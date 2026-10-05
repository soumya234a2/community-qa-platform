#  Community Q&A Platform

A Stack Overflow style Q&A site with Reddit style communities, built with **Flask** and **MySQL**.

## Features

- Sign up, log in and log out with hashed passwords (Werkzeug) and session cookies
- Communities (`c/python`, `c/java`, ...): browse, create and post inside them
- Ask questions with a title, details and up to 5 tags
- Answer questions; the asker can accept one answer (shown first with a green marker)
- Upvote and downvote questions and answers (click again to undo, no voting on your own post);
  votes update instantly with JavaScript and still work as normal forms if JavaScript is off
- Comments on questions and answers (2-500 characters, delete your own), with a live character counter
- Keyboard shortcut: Ctrl+Enter posts the comment or answer you are typing
- Sort by Newest, Top or Unanswered; search by keyword; filter by tag
- User profiles with a reputation score (question votes x5, answer votes x10, accepted answer +15)
- Code blocks (triple backticks) and inline code in posts, with all other HTML escaped

## Security

- Parameterized SQL everywhere (no string-built queries from user input)
- CSRF token checked on every POST
- Output escaped by Jinja; only a safe subset of formatting is re-enabled
- Open-redirect protection on the login `next` parameter

## Tech stack

Python, Flask, Jinja2, MySQL, PyMySQL, HTML, CSS, JavaScript (vanilla, no framework)

## Database

`users`, `communities`, `questions`, `answers`, `tags`, `question_tags` (many-to-many), `votes`
(one row per user per post, enforced by a unique key), `comments` (on questions or answers). See `schema.sql`.

## Run it locally

```bash
git clone https://github.com/soumya234a2/<repo-name>.git
cd <repo-name>
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt

copy .env.example .env         # Mac/Linux: cp .env.example .env
# open .env and put your MySQL password and a random SECRET_KEY

flask --app app init-db        # creates the database, tables and starter communities
                               # (safe to run again later to add new tables)
flask --app app run --debug
```

Open http://127.0.0.1:5000 and create an account.

## Project structure

```
app.py            routes, database helpers, auth, voting
schema.sql        table definitions
templates/        Jinja2 pages
static/style.css  styling (responsive)
static/app.js     AJAX voting, character counter, keyboard shortcut
```

## Ideas for next steps

Edit history, pagination, email verification, image uploads, notifications, a REST API.
