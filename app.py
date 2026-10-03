"""Campfire - a community Q&A platform (Stack Overflow style answers + Reddit style communities).

Stack: Flask, MySQL (PyMySQL), Jinja2.
"""
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

import pymysql
from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from markupsafe import Markup, escape
from pymysql.cursors import DictCursor
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB

DB_SETTINGS = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    user=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", ""),
    charset="utf8mb4",
)
DB_NAME = os.environ.get("DB_NAME", "campfire")

DEFAULT_COMMUNITIES = [
    ("python", "Python questions, scripts and libraries"),
    ("java", "Core Java, Spring and everything JVM"),
    ("web-dev", "HTML, CSS, JavaScript and frontend tooling"),
    ("databases", "SQL, MySQL, schema design and query tuning"),
    ("careers", "Resumes, interviews and first jobs in tech"),
]


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = pymysql.connect(database=DB_NAME, cursorclass=DictCursor,
                               autocommit=True, **DB_SETTINGS)
    return g.db


def query(sql, args=(), one=False):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@app.cli.command("init-db")
def init_db():
    """Create the database, tables and starter communities."""
    conn = pymysql.connect(autocommit=True, **DB_SETTINGS)
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute(f"USE `{DB_NAME}`")
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, encoding="utf-8") as f:
            for statement in f.read().split(";"):
                if statement.strip():
                    cur.execute(statement)
        for name, description in DEFAULT_COMMUNITIES:
            cur.execute(
                "INSERT IGNORE INTO communities (name, description, created_at) "
                "VALUES (%s, %s, %s)", (name, description, utcnow()))
    conn.close()
    print(f"Database '{DB_NAME}' is ready.")


# ---------------------------------------------------------------- helpers

@app.template_filter("render_body")
def render_body(text):
    """Escape user text, then allow only ```code blocks``` and `inline code`."""
    safe = str(escape(text))
    safe = re.sub(
        r"```(?:\w+)?\n?(.*?)```",
        lambda m: "<pre><code>" + m.group(1).strip("\n") + "</code></pre>",
        safe, flags=re.S)
    parts = re.split(r"(<pre>.*?</pre>)", safe, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("<pre>"):
            out.append(part)
        else:
            part = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", part)
            out.append(part.replace("\r\n", "\n").replace("\n", "<br>"))
    return Markup("".join(out))


@app.template_filter("ago")
def ago(dt):
    seconds = int((utcnow() - dt).total_seconds())
    for size, label in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"),
                        (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{label} ago"
    return "just now"


def parse_tags(raw):
    tags = []
    for token in re.split(r"[,\s]+", raw.lower()):
        token = token.lstrip("#")
        if re.fullmatch(r"[a-z0-9+#.\-]{1,24}", token) and token not in tags:
            tags.append(token)
    return tags[:5]


def save_tags(question_id, tags):
    for name in tags:
        execute("INSERT IGNORE INTO tags (name) VALUES (%s)", (name,))
        tag = query("SELECT id FROM tags WHERE name = %s", (name,), one=True)
        execute("INSERT IGNORE INTO question_tags (question_id, tag_id) "
                "VALUES (%s, %s)", (question_id, tag["id"]))


def attach_tags(questions):
    if not questions:
        return questions
    ids = [q["id"] for q in questions]
    marks = ",".join(["%s"] * len(ids))
    rows = query(
        "SELECT qt.question_id, t.name FROM question_tags qt "
        f"JOIN tags t ON t.id = qt.tag_id WHERE qt.question_id IN ({marks}) "
        "ORDER BY t.name", ids)
    by_question = {}
    for row in rows:
        by_question.setdefault(row["question_id"], []).append(row["name"])
    for q in questions:
        q["tags"] = by_question.get(q["id"], [])
    return questions


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Log in to continue.", "info")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user_and_check_csrf():
    if request.endpoint == "static":
        return
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = query("SELECT id, username FROM users WHERE id = %s",
                       (user_id,), one=True)
        if g.user is None:
            session.clear()
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Your session expired. Reload the page and try again.")


@app.context_processor
def inject_globals():
    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return session["csrf_token"]
    return {"csrf_token": csrf_token, "current_user": g.get("user")}


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def show_error(error):
    return render_template("error.html", error=error), error.code


# ---------------------------------------------------------------- questions feed

QUESTION_SELECT = """
SELECT q.id, q.title, q.body, q.created_at, q.user_id, q.accepted_answer_id,
       u.username, c.name AS community,
       COALESCE((SELECT SUM(v.value) FROM votes v
                 WHERE v.target_type = 'question' AND v.target_id = q.id), 0) AS score,
       (SELECT COUNT(*) FROM answers a WHERE a.question_id = q.id) AS answer_count
FROM questions q
JOIN users u ON u.id = q.user_id
JOIN communities c ON c.id = q.community_id
"""

SORTS = {
    "new": "q.created_at DESC",
    "top": "score DESC, q.created_at DESC",
    "unanswered": "q.created_at DESC",
}


def like_pattern(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def render_feed(community=None):
    sort = request.args.get("sort", "new")
    if sort not in SORTS:
        sort = "new"
    search = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip().lower()

    where, params = [], []
    if community:
        where.append("q.community_id = %s")
        params.append(community["id"])
    if search:
        where.append("(q.title LIKE %s OR q.body LIKE %s)")
        params += [like_pattern(search)] * 2
    if tag:
        where.append("q.id IN (SELECT qt.question_id FROM question_tags qt "
                     "JOIN tags t ON t.id = qt.tag_id WHERE t.name = %s)")
        params.append(tag)
    if sort == "unanswered":
        where.append("NOT EXISTS (SELECT 1 FROM answers a WHERE a.question_id = q.id)")

    sql = QUESTION_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORTS[sort]} LIMIT 40"
    questions = attach_tags(query(sql, params))

    popular_tags = query(
        "SELECT t.name, COUNT(*) AS n FROM tags t "
        "JOIN question_tags qt ON qt.tag_id = t.id "
        "GROUP BY t.id, t.name ORDER BY n DESC, t.name LIMIT 12")
    communities = query(
        "SELECT c.name, (SELECT COUNT(*) FROM questions q WHERE q.community_id = c.id) AS posts "
        "FROM communities c ORDER BY posts DESC, c.name LIMIT 8")
    def sort_link(key):
        args = {k: v for k, v in (("sort", key), ("q", search), ("tag", tag)) if v}
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    sort_links = {key: sort_link(key) for key in SORTS}
    return render_template("index.html", questions=questions, sort=sort,
                           search=search, tag=tag, community=community,
                           popular_tags=popular_tags, communities=communities,
                           sort_links=sort_links)


@app.get("/")
def index():
    return render_feed()


@app.get("/c/<name>")
def community_page(name):
    community = query("SELECT * FROM communities WHERE name = %s", (name.lower(),), one=True)
    if community is None:
        abort(404)
    return render_feed(community)


@app.get("/communities")
def communities():
    rows = query(
        "SELECT c.name, c.description, "
        "(SELECT COUNT(*) FROM questions q WHERE q.community_id = c.id) AS posts "
        "FROM communities c ORDER BY posts DESC, c.name")
    return render_template("communities.html", communities=rows)


@app.route("/communities/new", methods=["GET", "POST"])
@login_required
def new_community():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip().lower()
        description = request.form.get("description", "").strip()[:255]
        if not re.fullmatch(r"[a-z0-9_-]{3,30}", name):
            error = "Use 3-30 characters: lowercase letters, numbers, - or _."
        else:
            try:
                execute("INSERT INTO communities (name, description, created_by, created_at) "
                        "VALUES (%s, %s, %s, %s)", (name, description, g.user["id"], utcnow()))
                return redirect(url_for("community_page", name=name))
            except pymysql.err.IntegrityError:
                error = "A community with that name already exists."
    return render_template("community_new.html", error=error)


# ---------------------------------------------------------------- ask / view / answer

@app.route("/ask", methods=["GET", "POST"])
@login_required
def ask():
    all_communities = query("SELECT id, name FROM communities ORDER BY name")
    form = {"title": "", "body": "", "tags": "",
            "community": request.args.get("community", "")}
    error = None
    if request.method == "POST":
        form = {key: request.form.get(key, "").strip()
                for key in ("title", "body", "tags", "community")}
        community = query("SELECT id FROM communities WHERE name = %s",
                          (form["community"],), one=True)
        if community is None:
            error = "Pick a community for your question."
        elif not 10 <= len(form["title"]) <= 200:
            error = "The title needs 10 to 200 characters."
        elif len(form["body"]) < 20:
            error = "Add more detail: the body needs at least 20 characters."
        else:
            question_id = execute(
                "INSERT INTO questions (user_id, community_id, title, body, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (g.user["id"], community["id"], form["title"], form["body"], utcnow()))
            save_tags(question_id, parse_tags(form["tags"]))
            return redirect(url_for("question", question_id=question_id))
    return render_template("ask.html", communities=all_communities, form=form, error=error)


@app.get("/q/<int:question_id>")
def question(question_id):
    q = query(QUESTION_SELECT + " WHERE q.id = %s", (question_id,), one=True)
    if q is None:
        abort(404)
    attach_tags([q])
    answers = query(
        """SELECT a.id, a.body, a.created_at, a.user_id, u.username,
                  COALESCE((SELECT SUM(v.value) FROM votes v
                            WHERE v.target_type = 'answer' AND v.target_id = a.id), 0) AS score
           FROM answers a JOIN users u ON u.id = a.user_id
           WHERE a.question_id = %s
           ORDER BY (a.id = %s) DESC, score DESC, a.created_at ASC""",
        (question_id, q["accepted_answer_id"]))

    my_votes = {}
    if g.user:
        for row in query("SELECT value FROM votes WHERE user_id = %s "
                         "AND target_type = 'question' AND target_id = %s",
                         (g.user["id"], question_id)):
            my_votes[f"question-{question_id}"] = row["value"]
        if answers:
            ids = [a["id"] for a in answers]
            marks = ",".join(["%s"] * len(ids))
            for row in query("SELECT target_id, value FROM votes WHERE user_id = %s "
                             f"AND target_type = 'answer' AND target_id IN ({marks})",
                             [g.user["id"], *ids]):
                my_votes[f"answer-{row['target_id']}"] = row["value"]
    comments = {}
    answer_ids = [a["id"] for a in answers]
    clauses, params = ["(c.target_type = 'question' AND c.target_id = %s)"], [question_id]
    if answer_ids:
        marks = ",".join(["%s"] * len(answer_ids))
        clauses.append(f"(c.target_type = 'answer' AND c.target_id IN ({marks}))")
        params += answer_ids
    for row in query(
            "SELECT c.id, c.target_type, c.target_id, c.body, c.created_at, c.user_id, u.username "
            "FROM comments c JOIN users u ON u.id = c.user_id "
            f"WHERE {' OR '.join(clauses)} ORDER BY c.created_at ASC", params):
        comments.setdefault(f"{row['target_type']}-{row['target_id']}", []).append(row)
    return render_template("question.html", q=q, answers=answers, my_votes=my_votes,
                           comments=comments)


@app.post("/q/<int:question_id>/answer")
@login_required
def post_answer(question_id):
    if query("SELECT id FROM questions WHERE id = %s", (question_id,), one=True) is None:
        abort(404)
    body = request.form.get("body", "").strip()
    if len(body) < 10:
        flash("An answer needs at least 10 characters.", "error")
    else:
        execute("INSERT INTO answers (question_id, user_id, body, created_at) "
                "VALUES (%s, %s, %s, %s)", (question_id, g.user["id"], body, utcnow()))
        flash("Answer posted.", "success")
    return redirect(url_for("question", question_id=question_id) + "#answers")


@app.post("/q/<int:question_id>/accept/<int:answer_id>")
@login_required
def accept_answer(question_id, answer_id):
    q = query("SELECT user_id, accepted_answer_id FROM questions WHERE id = %s",
              (question_id,), one=True)
    if q is None:
        abort(404)
    if q["user_id"] != g.user["id"]:
        abort(403)
    answer = query("SELECT id FROM answers WHERE id = %s AND question_id = %s",
                   (answer_id, question_id), one=True)
    if answer is None:
        abort(404)
    new_value = None if q["accepted_answer_id"] == answer_id else answer_id
    execute("UPDATE questions SET accepted_answer_id = %s WHERE id = %s",
            (new_value, question_id))
    return redirect(url_for("question", question_id=question_id) + f"#answer-{answer_id}")


@app.post("/q/<int:question_id>/delete")
@login_required
def delete_question(question_id):
    q = query("SELECT user_id FROM questions WHERE id = %s", (question_id,), one=True)
    if q is None:
        abort(404)
    if q["user_id"] != g.user["id"]:
        abort(403)
    # votes use a polymorphic target, so they are cleaned up manually
    execute("DELETE FROM votes WHERE target_type = 'answer' AND target_id IN "
            "(SELECT id FROM answers WHERE question_id = %s)", (question_id,))
    execute("DELETE FROM votes WHERE target_type = 'question' AND target_id = %s",
            (question_id,))
    execute("DELETE FROM comments WHERE target_type = 'answer' AND target_id IN "
            "(SELECT id FROM answers WHERE question_id = %s)", (question_id,))
    execute("DELETE FROM comments WHERE target_type = 'question' AND target_id = %s",
            (question_id,))
    execute("DELETE FROM questions WHERE id = %s", (question_id,))
    flash("Question deleted.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- voting

def wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


@app.post("/vote")
@login_required
def vote():
    target_type = request.form.get("type")
    try:
        target_id = int(request.form.get("id", ""))
        value = int(request.form.get("value", ""))
    except ValueError:
        abort(400)
    if target_type not in ("question", "answer") or value not in (1, -1):
        abort(400)

    if target_type == "question":
        row = query("SELECT user_id, id AS question_id FROM questions WHERE id = %s",
                    (target_id,), one=True)
    else:
        row = query("SELECT user_id, question_id FROM answers WHERE id = %s",
                    (target_id,), one=True)
    if row is None:
        abort(404)

    back = url_for("question", question_id=row["question_id"])
    if row["user_id"] == g.user["id"]:
        if wants_json():
            return jsonify(error="You can't vote on your own post."), 403
        flash("You can't vote on your own post.", "error")
        return redirect(back)

    existing = query("SELECT id, value FROM votes WHERE user_id = %s "
                     "AND target_type = %s AND target_id = %s",
                     (g.user["id"], target_type, target_id), one=True)
    mine = value
    if existing is None:
        execute("INSERT INTO votes (user_id, target_type, target_id, value) "
                "VALUES (%s, %s, %s, %s)", (g.user["id"], target_type, target_id, value))
    elif existing["value"] == value:
        execute("DELETE FROM votes WHERE id = %s", (existing["id"],))  # click again to undo
        mine = 0
    else:
        execute("UPDATE votes SET value = %s WHERE id = %s", (value, existing["id"]))

    if wants_json():
        total = query("SELECT COALESCE(SUM(value), 0) AS score FROM votes "
                      "WHERE target_type = %s AND target_id = %s",
                      (target_type, target_id), one=True)["score"]
        return jsonify(score=int(total), mine=mine)
    anchor = "" if target_type == "question" else f"#answer-{target_id}"
    return redirect(back + anchor)


# ---------------------------------------------------------------- comments

@app.post("/comment")
@login_required
def add_comment():
    target_type = request.form.get("type")
    try:
        target_id = int(request.form.get("id", ""))
    except ValueError:
        abort(400)
    if target_type not in ("question", "answer"):
        abort(400)
    if target_type == "question":
        row = query("SELECT id AS question_id FROM questions WHERE id = %s", (target_id,), one=True)
    else:
        row = query("SELECT question_id FROM answers WHERE id = %s", (target_id,), one=True)
    if row is None:
        abort(404)

    back = url_for("question", question_id=row["question_id"])
    body = request.form.get("body", "").strip()
    if not 2 <= len(body) <= 500:
        flash("A comment needs 2 to 500 characters.", "error")
        return redirect(back)
    comment_id = execute(
        "INSERT INTO comments (user_id, target_type, target_id, body, created_at) "
        "VALUES (%s, %s, %s, %s, %s)", (g.user["id"], target_type, target_id, body, utcnow()))
    return redirect(back + f"#comment-{comment_id}")


@app.post("/comment/<int:comment_id>/delete")
@login_required
def delete_comment(comment_id):
    comment = query("SELECT user_id, target_type, target_id FROM comments WHERE id = %s",
                    (comment_id,), one=True)
    if comment is None:
        abort(404)
    if comment["user_id"] != g.user["id"]:
        abort(403)
    if comment["target_type"] == "question":
        question_id = comment["target_id"]
    else:
        question_id = query("SELECT question_id FROM answers WHERE id = %s",
                            (comment["target_id"],), one=True)["question_id"]
    execute("DELETE FROM comments WHERE id = %s", (comment_id,))
    return redirect(url_for("question", question_id=question_id))


# ---------------------------------------------------------------- profiles

@app.get("/u/<username>")
def profile(username):
    user = query("SELECT id, username, created_at FROM users WHERE username = %s",
                 (username,), one=True)
    if user is None:
        abort(404)
    uid = user["id"]
    reputation = query(
        """SELECT
             COALESCE((SELECT SUM(v.value) * 5 FROM votes v
                       JOIN questions q ON v.target_type = 'question' AND v.target_id = q.id
                       WHERE q.user_id = %s), 0)
           + COALESCE((SELECT SUM(v.value) * 10 FROM votes v
                       JOIN answers a ON v.target_type = 'answer' AND v.target_id = a.id
                       WHERE a.user_id = %s), 0)
           + COALESCE((SELECT COUNT(*) * 15 FROM questions q
                       JOIN answers a ON a.id = q.accepted_answer_id
                       WHERE a.user_id = %s), 0) AS rep""",
        (uid, uid, uid), one=True)["rep"]
    questions = attach_tags(query(
        QUESTION_SELECT + " WHERE q.user_id = %s ORDER BY q.created_at DESC LIMIT 20", (uid,)))
    answers = query(
        """SELECT a.id, a.created_at, q.id AS question_id, q.title,
                  (q.accepted_answer_id = a.id) AS accepted
           FROM answers a JOIN questions q ON q.id = a.question_id
           WHERE a.user_id = %s ORDER BY a.created_at DESC LIMIT 20""", (uid,))
    return render_template("profile.html", profile_user=user, reputation=int(reputation),
                           questions=questions, answers=answers)


# ---------------------------------------------------------------- auth

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))
    error = None
    form = {"username": "", "email": ""}
    if request.method == "POST":
        form["username"] = request.form.get("username", "").strip()
        form["email"] = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,30}", form["username"]):
            error = "Username: 3-30 letters, numbers or underscores."
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", form["email"]):
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            try:
                user_id = execute(
                    "INSERT INTO users (username, email, password_hash, created_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (form["username"], form["email"], generate_password_hash(password), utcnow()))
                session.clear()
                session["user_id"] = user_id
                return redirect(url_for("index"))
            except pymysql.err.IntegrityError:
                error = "That username or email is already registered."
    return render_template("register.html", error=error, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    error = None
    identifier = ""
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = query("SELECT id, password_hash FROM users WHERE username = %s OR email = %s",
                     (identifier, identifier.lower()), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(safe_next(request.args.get("next")))
        error = "Username/email or password is incorrect."
    return render_template("login.html", error=error, identifier=identifier)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
