from datetime import datetime, timedelta
import os
import re
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (
    LoginManager, UserMixin, current_user,
    login_user, logout_user, login_required
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import Markup, escape
from flask_socketio import SocketIO, join_room

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = "rozdum_secret_key_change_me"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "forum.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(basedir, "static", "uploads")

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ADMIN_PASSWORD = "141022"
REACTIONS = ["🔥", "❤️", "😂", "😎", "👍", "👏"]
ONLINE_WINDOW = timedelta(minutes=5)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
MENTION_RE = re.compile(r'@([a-zA-Z0-9_]+)')

topic_tags = db.Table(
    "topic_tags",
    db.Column("topic_id", db.Integer, db.ForeignKey("topic.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(80), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    avatar = db.Column(db.String(255), default="")
    bio = db.Column(db.Text, default="")
    status = db.Column(db.String(120), default="")
    role = db.Column(db.String(20), default="user")  # user / moderator / admin
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class Topic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(220), nullable=False)
    content = db.Column(db.Text, nullable=False)
    pinned = db.Column(db.Boolean, default=False)
    image = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)

    author = db.relationship("User", backref=db.backref("topics", lazy=True))
    category = db.relationship("Category", backref=db.backref("topics", lazy=True))
    tags = db.relationship("Tag", secondary=topic_tags, backref=db.backref("topics", lazy=True))
    images = db.relationship(
        "TopicImage",
        backref="topic_rel",
        cascade="all, delete-orphan",
        order_by="TopicImage.position.asc()",
        lazy=True,
    )


class TopicImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, default=0)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=True)

    author = db.relationship("User", backref=db.backref("comments", lazy=True))
    topic = db.relationship("Topic", backref=db.backref("all_comments", lazy=True))
    parent = db.relationship("Comment", remote_side=[id], backref=db.backref("replies", lazy=True))


class Reaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reaction = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)

    user = db.relationship("User", backref=db.backref("reactions", lazy=True))
    topic = db.relationship("Topic", backref=db.backref("reactions", lazy=True))


class CommentReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reaction = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=False)

    user = db.relationship("User", backref=db.backref("comment_reactions", lazy=True))
    comment = db.relationship("Comment", backref=db.backref("reactions", lazy=True))


class SavedPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)

    user = db.relationship("User", backref=db.backref("saved_posts", lazy=True))
    topic = db.relationship("Topic", backref=db.backref("saved_by_entries", lazy=True))


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    following_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    follower = db.relationship("User", foreign_keys=[follower_id], backref=db.backref("following", lazy=True))
    following = db.relationship("User", foreign_keys=[following_id], backref=db.backref("followers_rel", lazy=True))


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", backref=db.backref("notifications", lazy=True))


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_type = db.Column(db.String(30), nullable=False)   # topic/comment/user
    reason = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="open")        # open/resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    reporter = db.relationship("User", foreign_keys=[reporter_id])
    topic = db.relationship("Topic")
    comment = db.relationship("Comment")
    target_user = db.relationship("User", foreign_keys=[target_user_id])


class ChatThread(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user1_id", "user2_id", name="uq_chat_pair"),
    )

    def other_user(self, current_user_id):
        other_id = self.user2_id if self.user1_id == current_user_id else self.user1_id
        return User.query.get(other_id)


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("chat_thread.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    sender = db.relationship("User", backref=db.backref("chat_messages", lazy=True))
    thread = db.relationship("ChatThread", backref=db.backref("messages", lazy=True, cascade="all, delete-orphan"))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def seed_categories():
    defaults = ["Общение", "Игры", "Новости", "Помощь", "Оффтоп"]
    for name in defaults:
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name))
    db.session.commit()


def is_online(user):
    if not user or not user.last_seen:
        return False
    return datetime.utcnow() - user.last_seen <= ONLINE_WINDOW


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    if not allowed_image(file_storage.filename):
        return None

    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4()}_{filename}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file_storage.save(path)
    return unique_name


def delete_uploaded_image(filename):
    if not filename:
        return
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(path):
        os.remove(path)


def render_mentions(text):
    safe_text = str(escape(text))

    def repl(match):
        username = match.group(1).lower()
        return f'<a class="mention" href="{url_for("profile", username=username)}">@{match.group(1)}</a>'

    return Markup(MENTION_RE.sub(repl, safe_text))


def notify_mentions(text, sender, link, source_text):
    notified_ids = set()
    usernames = {u.lower() for u in MENTION_RE.findall(text or "")}
    for username in usernames:
        user = User.query.filter_by(username=username).first()
        if user and user.id != sender.id:
            db.session.add(Notification(
                user_id=user.id,
                text=f"{sender.nickname} упомянул(а) вас {source_text}.",
                link=link
            ))
            notified_ids.add(user.id)
    return notified_ids


def push_notification_counts(user_ids):
    unique_ids = {uid for uid in user_ids if uid}
    for uid in unique_ids:
        count = Notification.query.filter_by(user_id=uid, is_read=False).count()
        socketio.emit("new_notification", {"count": count}, to=f"user_{uid}")


def get_or_create_thread(user_a_id, user_b_id):
    low_id, high_id = sorted([user_a_id, user_b_id])
    thread = ChatThread.query.filter_by(user1_id=low_id, user2_id=high_id).first()
    if not thread:
        thread = ChatThread(user1_id=low_id, user2_id=high_id)
        db.session.add(thread)
        db.session.commit()
    return thread


def parse_tags(tags_text):
    tags = []
    for raw in (tags_text or "").split(","):
        name = raw.strip().lstrip("#").lower()
        if name and name not in tags:
            tags.append(name)
    return tags[:10]


def sync_topic_tags(topic, tags_text):
    names = parse_tags(tags_text)
    topic.tags.clear()

    for name in names:
        tag = Tag.query.filter_by(name=name).first()
        if not tag:
            tag = Tag(name=name)
            db.session.add(tag)
            db.session.flush()
        topic.tags.append(tag)


def store_topic_images(topic, files):
    images = [f for f in files if f and f.filename]
    existing_count = len(topic.images)

    if existing_count + len(images) > 5:
        return False, "Можно максимум 5 фото на один пост."

    for offset, file in enumerate(images):
        fname = save_uploaded_image(file)
        if fname is None:
            return False, "Фото должны быть изображениями."
        db.session.add(TopicImage(topic_rel=topic, filename=fname, position=existing_count + offset))

    return True, ""


def decorate_topic(topic):
    topic.reaction_counts = {
        emoji: Reaction.query.filter_by(topic_id=topic.id, reaction=emoji).count()
        for emoji in REACTIONS
    }
    topic.comment_count = Comment.query.filter_by(topic_id=topic.id, parent_id=None).count()

    if current_user.is_authenticated:
        my_reaction = Reaction.query.filter_by(topic_id=topic.id, user_id=current_user.id).first()
        topic.my_reaction = my_reaction.reaction if my_reaction else ""

        topic.is_saved = SavedPost.query.filter_by(user_id=current_user.id, topic_id=topic.id).first() is not None
    else:
        topic.my_reaction = ""
        topic.is_saved = False

    topic.content_html = render_mentions(topic.content)
    return topic


def decorate_comment(comment):
    comment.reaction_counts = {
        emoji: CommentReaction.query.filter_by(comment_id=comment.id, reaction=emoji).count()
        for emoji in REACTIONS
    }

    if current_user.is_authenticated:
        my_reaction = CommentReaction.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()
        comment.my_reaction = my_reaction.reaction if my_reaction else ""
    else:
        comment.my_reaction = ""

    comment.content_html = render_mentions(comment.content)

    for reply in comment.replies:
        decorate_comment(reply)

    return comment


def can_manage_topic(topic):
    return current_user.is_authenticated and (
        current_user.role in ("admin", "moderator") or topic.user_id == current_user.id
    )


def can_manage_comment(comment):
    return current_user.is_authenticated and (
        current_user.role in ("admin", "moderator") or comment.user_id == current_user.id
    )


def delete_comment_tree(comment):
    for reply in list(comment.replies):
        delete_comment_tree(reply)

    CommentReaction.query.filter_by(comment_id=comment.id).delete(synchronize_session=False)
    Report.query.filter_by(comment_id=comment.id).delete(synchronize_session=False)
    db.session.delete(comment)


@app.context_processor
def inject_globals():
    unread = 0
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return {
        "unread_notifications_count": unread,
        "reactions_list": REACTIONS,
        "is_online": is_online,
    }


@app.before_request
def update_last_seen():
    if request.endpoint == "static":
        return
    if current_user.is_authenticated:
        if current_user.is_banned:
            logout_user()
            flash("Ваш аккаунт заблокирован.")
            return redirect(url_for("login"))
        current_user.last_seen = datetime.utcnow()
        db.session.commit()


@socketio.on("connect")
def socket_connect():
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")


@socketio.on("join_notifications")
def join_notifications():
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")


@socketio.on("join_thread")
def join_thread(data):
    if not current_user.is_authenticated:
        return
    thread_id = data.get("thread_id")
    thread = ChatThread.query.get(thread_id)
    if thread and current_user.id in (thread.user1_id, thread.user2_id):
        join_room(f"thread_{thread.id}")


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("forum"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        nickname = request.form.get("nickname", "").strip()
        password_text = request.form.get("password", "")

        if not username or not nickname or not password_text:
            flash("Заполни все поля.")
            return render_template("register.html")

        if not re.fullmatch(r"[a-zA-Z0-9_]+", username):
            flash("Логин должен быть только на английском: буквы, цифры и _")
            return render_template("register.html")

        if User.query.filter_by(username=username).first():
            flash("Такой логин уже занят.")
            return render_template("register.html")

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("register.html")

        user = User(
            username=username,
            nickname=nickname,
            password=generate_password_hash(password_text),
            avatar=avatar_name,
            role="user",
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Аккаунт создан.")
        return redirect(url_for("forum"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password_text = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password, password_text):
            flash("Неверный логин или пароль.")
            return render_template("login.html")

        if user.is_banned:
            flash("Этот аккаунт заблокирован.")
            return render_template("login.html")

        login_user(user)
        flash("Добро пожаловать в ROZDUM.")
        return redirect(url_for("forum"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.")
    return redirect(url_for("login"))


@app.route("/admin/login", methods=["POST"])
@login_required
def admin_login():
    password_text = request.form.get("password", "")
    if password_text == ADMIN_PASSWORD:
        current_user.role = "admin"
        current_user.is_admin = True
        db.session.commit()
        flash("Админ-режим включён.")
    else:
        flash("Неверный код.")
    return redirect(request.referrer or url_for("forum"))


@app.route("/forum")
@login_required
def forum():
    search = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    searched_user = None

    query = Topic.query.join(User, Topic.user_id == User.id).outerjoin(Topic.tags)

    if category_id:
        query = query.filter(Topic.category_id == category_id)

    if search:
        if search.startswith("@"):
            username = search[1:].strip().lower()
            searched_user = User.query.filter_by(username=username).first()
            if searched_user:
                query = query.filter(Topic.user_id == searched_user.id)
            else:
                like = f"%{search}%"
                query = query.filter(
                    or_(
                        Topic.title.ilike(like),
                        Topic.content.ilike(like),
                        User.nickname.ilike(like),
                        User.username.ilike(like),
                        Tag.name.ilike(like),
                    )
                )
        else:
            like = f"%{search}%"
            query = query.filter(
                or_(
                    Topic.title.ilike(like),
                    Topic.content.ilike(like),
                    User.nickname.ilike(like),
                    User.username.ilike(like),
                    Tag.name.ilike(like),
                )
            )

    topics = query.distinct().order_by(Topic.pinned.desc(), Topic.id.desc()).all()
    for topic in topics:
        decorate_topic(topic)

    categories = Category.query.order_by(Category.name.asc()).all()

    return render_template(
        "forum.html",
        topics=topics,
        search=search,
        searched_user=searched_user,
        category_id=category_id,
        categories=categories,
        total_topics=Topic.query.count(),
        total_users=User.query.count(),
        total_comments=Comment.query.count(),
    )


@app.route("/topic/new", methods=["GET", "POST"])
@login_required
def create_topic():
    categories = Category.query.order_by(Category.name.asc()).all()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id", type=int)
        tags_text = request.form.get("tags", "").strip()
        files = request.files.getlist("images")

        if not title or not content:
            flash("Заполни заголовок и текст.")
            return render_template("create.html", categories=categories)

        if not category_id:
            first_category = Category.query.first()
            category_id = first_category.id if first_category else None

        if not category_id:
            flash("Сначала создай категории.")
            return render_template("create.html", categories=categories)

        if len([f for f in files if f and f.filename]) > 5:
            flash("Можно максимум 5 фото на один пост.")
            return render_template("create.html", categories=categories)

        for f in files:
            if f and f.filename and not allowed_image(f.filename):
                flash("Все фото должны быть изображениями.")
                return render_template("create.html", categories=categories)

        topic = Topic(
            title=title,
            content=content,
            user_id=current_user.id,
            category_id=category_id,
        )
        db.session.add(topic)
        db.session.flush()

        sync_topic_tags(topic, tags_text)
        ok, msg = store_topic_images(topic, files)
        if not ok:
            db.session.rollback()
            flash(msg)
            return render_template("create.html", categories=categories)

        db.session.commit()

        mentioned_ids = notify_mentions(
            content,
            current_user,
            url_for("topic_view", topic_id=topic.id),
            "в посте",
        )
        db.session.commit()
        push_notification_counts(mentioned_ids)

        socketio.emit("new_topic", {"topic_id": topic.id}, to="user_%s" % current_user.id)
        flash("Пост опубликован.")
        return redirect(url_for("forum"))

    return render_template("create.html", categories=categories)


@app.route("/topic/<int:topic_id>")
@login_required
def topic_view(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    decorate_topic(topic)

    comments = (
        Comment.query
        .filter_by(topic_id=topic.id, parent_id=None)
        .order_by(Comment.id.asc())
        .all()
    )
    for comment in comments:
        decorate_comment(comment)

    return render_template("topic.html", topic=topic, comments=comments)


@app.route("/topic/<int:topic_id>/react", methods=["POST"])
@login_required
def react_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    reaction = request.form.get("reaction", "").strip()

    if reaction not in REACTIONS:
        abort(400)

    existing = Reaction.query.filter_by(topic_id=topic.id, user_id=current_user.id).first()
    if existing:
        if existing.reaction == reaction:
            db.session.delete(existing)
            flash("Реакция убрана.")
        else:
            existing.reaction = reaction
            flash("Реакция изменена.")
    else:
        db.session.add(Reaction(topic_id=topic.id, user_id=current_user.id, reaction=reaction))
        flash("Реакция добавлена.")

    affected_ids = []
    if topic.author.id != current_user.id:
        db.session.add(Notification(
            user_id=topic.author.id,
            text=f"{current_user.nickname} поставил(а) реакцию на ваш пост.",
            link=url_for("topic_view", topic_id=topic.id),
        ))
        affected_ids.append(topic.author.id)

    db.session.commit()
    push_notification_counts(affected_ids)
    return redirect(request.referrer or url_for("topic_view", topic_id=topic.id))


@app.route("/topic/<int:topic_id>/save", methods=["POST"])
@login_required
def toggle_save_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    existing = SavedPost.query.filter_by(user_id=current_user.id, topic_id=topic.id).first()

    if existing:
        db.session.delete(existing)
        flash("Пост убран из сохранённых.")
    else:
        db.session.add(SavedPost(user_id=current_user.id, topic_id=topic.id))
        flash("Пост сохранён.")

    db.session.commit()
    return redirect(request.referrer or url_for("topic_view", topic_id=topic.id))


@app.route("/topic/<int:topic_id>/pin", methods=["POST"])
@login_required
def pin_topic(topic_id):
    if not current_user.is_admin:
        abort(403)
    topic = Topic.query.get_or_404(topic_id)
    topic.pinned = not topic.pinned
    db.session.commit()
    flash("Статус закрепления изменён.")
    return redirect(request.referrer or url_for("topic_view", topic_id=topic.id))


@app.route("/topic/<int:topic_id>/comment", methods=["POST"])
@login_required
def create_comment(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    content = request.form.get("content", "").strip()

    if not content:
        flash("Комментарий не может быть пустым.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    comment = Comment(content=content, topic_id=topic.id, user_id=current_user.id)
    db.session.add(comment)

    affected_ids = []
    if topic.author.id != current_user.id:
        db.session.add(Notification(
            user_id=topic.author.id,
            text=f"{current_user.nickname} оставил(а) комментарий под вашим постом.",
            link=url_for("topic_view", topic_id=topic.id) + "#comments",
        ))
        affected_ids.append(topic.author.id)

    affected_ids += list(notify_mentions(
        content,
        current_user,
        url_for("topic_view", topic_id=topic.id) + "#comments",
        "в комментарии",
    ))
    db.session.commit()
    push_notification_counts(affected_ids)

    flash("Комментарий добавлен.")
    return redirect(url_for("topic_view", topic_id=topic.id) + "#comments")


@app.route("/comment/<int:comment_id>/reply", methods=["POST"])
@login_required
def reply_comment(comment_id):
    parent = Comment.query.get_or_404(comment_id)
    content = request.form.get("content", "").strip()

    if not content:
        flash("Ответ не может быть пустым.")
        return redirect(url_for("topic_view", topic_id=parent.topic_id) + "#comments")

    reply = Comment(
        content=content,
        topic_id=parent.topic_id,
        user_id=current_user.id,
        parent_id=parent.id,
    )
    db.session.add(reply)

    affected_ids = []
    if parent.author.id != current_user.id:
        db.session.add(Notification(
            user_id=parent.author.id,
            text=f"{current_user.nickname} ответил(а) на ваш комментарий.",
            link=url_for("topic_view", topic_id=parent.topic_id) + "#comments",
        ))
        affected_ids.append(parent.author.id)

    affected_ids += list(notify_mentions(
        content,
        current_user,
        url_for("topic_view", topic_id=parent.topic_id) + "#comments",
        "в ответе",
    ))
    db.session.commit()
    push_notification_counts(affected_ids)

    flash("Ответ добавлен.")
    return redirect(url_for("topic_view", topic_id=parent.topic_id) + "#comments")


@app.route("/comment/<int:comment_id>/react", methods=["POST"])
@login_required
def react_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    reaction = request.form.get("reaction", "").strip()

    if reaction not in REACTIONS:
        abort(400)

    existing = CommentReaction.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()

    if existing:
        if existing.reaction == reaction:
            db.session.delete(existing)
            flash("Реакция убрана.")
        else:
            existing.reaction = reaction
            flash("Реакция изменена.")
    else:
        db.session.add(CommentReaction(comment_id=comment.id, user_id=current_user.id, reaction=reaction))
        flash("Реакция добавлена.")

    affected_ids = []
    if comment.author.id != current_user.id:
        db.session.add(Notification(
            user_id=comment.author.id,
            text=f"{current_user.nickname} отреагировал(а) на ваш комментарий.",
            link=url_for("topic_view", topic_id=comment.topic_id) + "#comments",
        ))
        affected_ids.append(comment.author.id)

    db.session.commit()
    push_notification_counts(affected_ids)
    return redirect(request.referrer or url_for("topic_view", topic_id=comment.topic_id) + "#comments")


@app.route("/topic/<int:topic_id>/edit", methods=["GET", "POST"])
@login_required
def edit_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    categories = Category.query.order_by(Category.name.asc()).all()

    if not can_manage_topic(topic):
        flash("У вас нет доступа к этому посту.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id", type=int)
        tags_text = request.form.get("tags", "").strip()
        files = request.files.getlist("images")

        if not title or not content:
            flash("Заполни заголовок и текст.")
            return render_template("edit_topic.html", topic=topic, categories=categories)

        if category_id:
            topic.category_id = category_id

        for f in files:
            if f and f.filename and not allowed_image(f.filename):
                flash("Все фото должны быть изображениями.")
                return render_template("edit_topic.html", topic=topic, categories=categories)

        if len([f for f in files if f and f.filename]) + len(topic.images) > 5:
            flash("Можно максимум 5 фото на один пост.")
            return render_template("edit_topic.html", topic=topic, categories=categories)

        topic.title = title
        topic.content = content

        sync_topic_tags(topic, tags_text)
        ok, msg = store_topic_images(topic, files)
        if not ok:
            db.session.rollback()
            flash(msg)
            return render_template("edit_topic.html", topic=topic, categories=categories)

        db.session.commit()
        flash("Пост обновлён.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    return render_template("edit_topic.html", topic=topic, categories=categories)


@app.route("/topic/<int:topic_id>/delete", methods=["POST"])
@login_required
def delete_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)

    if not can_manage_topic(topic):
        flash("У вас нет доступа к этому посту.")
        return redirect(url_for("forum"))

    for root_comment in Comment.query.filter_by(topic_id=topic.id, parent_id=None).all():
        delete_comment_tree(root_comment)

    Reaction.query.filter_by(topic_id=topic.id).delete(synchronize_session=False)
    SavedPost.query.filter_by(topic_id=topic.id).delete(synchronize_session=False)
    Report.query.filter_by(topic_id=topic.id).delete(synchronize_session=False)

    for img in topic.images:
        delete_uploaded_image(img.filename)

    db.session.delete(topic)
    db.session.commit()
    flash("Пост удалён.")
    return redirect(url_for("forum"))


@app.route("/comment/<int:comment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if not can_manage_comment(comment):
        flash("У вас нет доступа к этому комментарию.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id))

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("Комментарий не может быть пустым.")
            return render_template("edit_comment.html", comment=comment)

        comment.content = content
        db.session.commit()
        flash("Комментарий обновлён.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id) + "#comments")

    return render_template("edit_comment.html", comment=comment)


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if not can_manage_comment(comment):
        flash("У вас нет доступа к этому комментарию.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id))

    delete_comment_tree(comment)
    db.session.commit()
    flash("Комментарий удалён.")
    return redirect(url_for("topic_view", topic_id=comment.topic_id) + "#comments")


@app.route("/profile/<username>")
@login_required
def profile(username):
    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    topics = Topic.query.filter_by(user_id=user.id).order_by(Topic.pinned.desc(), Topic.id.desc()).all()
    for topic in topics:
        decorate_topic(topic)

    followers_count = Follow.query.filter_by(following_id=user.id).count()
    following_count = Follow.query.filter_by(follower_id=user.id).count()
    posts_count = Topic.query.filter_by(user_id=user.id).count()
    is_following = False

    if current_user.id != user.id:
        is_following = Follow.query.filter_by(follower_id=current_user.id, following_id=user.id).first() is not None

    return render_template(
        "profile.html",
        user=user,
        topics=topics,
        followers_count=followers_count,
        following_count=following_count,
        posts_count=posts_count,
        is_following=is_following,
    )


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        bio = request.form.get("bio", "").strip()

        if not nickname:
            flash("Ник не может быть пустым.")
            return render_template("edit_profile.html")

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("edit_profile.html")

        if avatar_name:
            delete_uploaded_image(current_user.avatar)
            current_user.avatar = avatar_name

        current_user.nickname = nickname
        current_user.bio = bio
        db.session.commit()
        flash("Профиль обновлён.")
        return redirect(url_for("profile", username=current_user.username))

    return render_template("edit_profile.html")


@app.route("/admin/profile/<username>", methods=["GET", "POST"])
@login_required
def admin_edit_profile(username):
    if not current_user.is_admin:
        flash("Только админ может редактировать чужие профили.")
        return redirect(url_for("forum"))

    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        bio = request.form.get("bio", "").strip()
        status = request.form.get("status", "").strip()
        role = request.form.get("role", "user").strip()

        if role not in ("user", "moderator", "admin"):
            role = "user"

        if not nickname:
            flash("Ник не может быть пустым.")
            return render_template("admin_edit_profile.html", user=user)

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("admin_edit_profile.html", user=user)

        if avatar_name:
            delete_uploaded_image(user.avatar)
            user.avatar = avatar_name

        user.nickname = nickname
        user.bio = bio
        user.status = status
        user.role = role
        user.is_admin = (role == "admin")

        db.session.commit()
        flash("Профиль пользователя обновлён.")
        return redirect(url_for("profile", username=user.username))

    return render_template("admin_edit_profile.html", user=user)


@app.route("/follow/<username>", methods=["POST"])
@login_required
def follow_user(username):
    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if user.id == current_user.id:
        flash("Нельзя подписаться на себя.")
        return redirect(url_for("profile", username=user.username))

    relation = Follow.query.filter_by(follower_id=current_user.id, following_id=user.id).first()

    affected_ids = []
    if relation:
        db.session.delete(relation)
        flash("Вы отписались.")
    else:
        db.session.add(Follow(follower_id=current_user.id, following_id=user.id))
        db.session.add(Notification(
            user_id=user.id,
            text=f"{current_user.nickname} подписался(ась) на вас.",
            link=url_for("profile", username=current_user.username),
        ))
        affected_ids.append(user.id)
        flash("Вы подписались.")

    db.session.commit()
    push_notification_counts(affected_ids)
    return redirect(url_for("profile", username=user.username))


@app.route("/save/<int:topic_id>", methods=["POST"])
@login_required
def save_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    existing = SavedPost.query.filter_by(user_id=current_user.id, topic_id=topic.id).first()

    if existing:
        db.session.delete(existing)
        flash("Убрано из сохранённых.")
    else:
        db.session.add(SavedPost(user_id=current_user.id, topic_id=topic.id))
        flash("Добавлено в сохранённые.")

    db.session.commit()
    return redirect(request.referrer or url_for("topic_view", topic_id=topic.id))


@app.route("/saved")
@login_required
def saved_posts():
    saved = SavedPost.query.filter_by(user_id=current_user.id).order_by(SavedPost.id.desc()).all()
    topics = []
    for item in saved:
        if item.topic:
            decorate_topic(item.topic)
            topics.append(item.topic)
    return render_template("saved.html", topics=topics)


@app.route("/report/<target_type>/<int:target_id>", methods=["GET", "POST"])
@login_required
def report(target_type, target_id):
    target_type = target_type.lower()
    target_label = ""
    target_url = url_for("forum")
    topic = None
    comment = None
    user = None

    if target_type == "topic":
        topic = Topic.query.get_or_404(target_id)
        target_label = f"пост: {topic.title}"
        target_url = url_for("topic_view", topic_id=topic.id)
    elif target_type == "comment":
        comment = Comment.query.get_or_404(target_id)
        target_label = "комментарий"
        target_url = url_for("topic_view", topic_id=comment.topic_id) + "#comments"
    elif target_type == "user":
        user = User.query.get_or_404(target_id)
        target_label = f"пользователь: {user.nickname}"
        target_url = url_for("profile", username=user.username)
    else:
        abort(404)

    reasons = ["Спам", "Оскорбления", "Фейк", "18+", "Мошенничество", "Другое"]

    if request.method == "POST":
        reason = request.form.get("reason", "Другое")
        description = request.form.get("description", "").strip()

        report = Report(
            report_type=target_type,
            reason=reason,
            description=description,
            reporter_id=current_user.id,
            topic_id=topic.id if topic else None,
            comment_id=comment.id if comment else None,
            target_user_id=user.id if user else None,
        )
        db.session.add(report)
        db.session.commit()

        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            db.session.add(Notification(
                user_id=admin.id,
                text=f"Новая жалоба на {target_label}.",
                link=url_for("admin_reports"),
            ))
        db.session.commit()
        push_notification_counts([a.id for a in admins])

        flash("Жалоба отправлена.")
        return redirect(target_url)

    return render_template(
        "report.html",
        target_type=target_type,
        target_label=target_label,
        target_url=target_url,
        reasons=reasons,
        topic=topic,
        comment=comment,
        user=user,
    )


@app.route("/admin/reports")
@login_required
def admin_reports():
    if not current_user.is_admin:
        abort(403)
    reports = Report.query.order_by(Report.id.desc()).all()
    return render_template("admin_reports.html", reports=reports)


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@login_required
def resolve_report(report_id):
    if not current_user.is_admin:
        abort(403)
    report = Report.query.get_or_404(report_id)
    report.status = "resolved"
    db.session.commit()
    flash("Жалоба отмечена как решённая.")
    return redirect(url_for("admin_reports"))


@app.route("/admin/user/<username>/role", methods=["POST"])
@login_required
def admin_set_role(username):
    if not current_user.is_admin:
        abort(403)

    user = User.query.filter_by(username=username.strip().lower()).first_or_404()
    role = request.form.get("role", "user").strip()
    if role not in ("user", "moderator", "admin"):
        role = "user"

    user.role = role
    user.is_admin = (role == "admin")
    db.session.commit()
    flash("Роль пользователя обновлена.")
    return redirect(url_for("profile", username=user.username))


@app.route("/ban/<username>", methods=["POST"])
@login_required
def ban_user(username):
    if not current_user.is_admin:
        flash("Только админ может банить пользователей.")
        return redirect(request.referrer or url_for("forum"))

    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if user.id == current_user.id:
        flash("Нельзя забанить себя.")
        return redirect(request.referrer or url_for("forum"))

    user.is_banned = True
    db.session.commit()
    flash(f"Пользователь {user.username} заблокирован.")
    return redirect(request.referrer or url_for("forum"))


@app.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).all()
    return render_template("notifications.html", notifications=items)


@app.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    note = Notification.query.get_or_404(notification_id)
    if note.user_id != current_user.id:
        abort(403)

    note.is_read = True
    db.session.commit()
    return redirect(note.link or url_for("notifications"))


@app.route("/chats")
@login_required
def chats():
    search = request.args.get("q", "").strip()
    users = []

    if search:
        like = f"%{search}%"
        users = (
            User.query
            .filter(User.id != current_user.id)
            .filter(or_(User.username.ilike(like), User.nickname.ilike(like)))
            .order_by(User.nickname.asc())
            .all()
        )

    threads = (
        ChatThread.query
        .filter(or_(ChatThread.user1_id == current_user.id, ChatThread.user2_id == current_user.id))
        .order_by(ChatThread.last_message_at.desc())
        .all()
    )

    prepared_threads = []
    for thread in threads:
        partner = thread.other_user(current_user.id)
        last_message = (
            ChatMessage.query
            .filter_by(thread_id=thread.id)
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        unread_count = (
            ChatMessage.query
            .filter_by(thread_id=thread.id, is_read=False)
            .filter(ChatMessage.sender_id != current_user.id)
            .count()
        )

        prepared_threads.append({
            "thread": thread,
            "partner": partner,
            "last_message": last_message,
            "unread_count": unread_count,
        })

    return render_template(
        "chats.html",
        search=search,
        users=users,
        threads=prepared_threads,
    )


@app.route("/chat/<username>", methods=["GET", "POST"])
@login_required
def chat_with_user(username):
    other = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if other.id == current_user.id:
        flash("Нельзя открыть чат с самим собой.")
        return redirect(url_for("chats"))

    thread = get_or_create_thread(current_user.id, other.id)

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            flash("Сообщение не может быть пустым.")
            return redirect(url_for("chat_with_user", username=other.username))

        msg = ChatMessage(
            thread_id=thread.id,
            sender_id=current_user.id,
            content=content,
        )
        thread.last_message_at = datetime.utcnow()
        db.session.add(msg)

        db.session.add(Notification(
            user_id=other.id,
            text=f"{current_user.nickname} написал(а) вам сообщение.",
            link=url_for("chat_with_user", username=current_user.username),
        ))

        mentioned_ids = notify_mentions(
            content,
            current_user,
            url_for("chat_with_user", username=other.username),
            "в сообщении",
        )

        db.session.commit()
        push_notification_counts([other.id] + list(mentioned_ids))
        socketio.emit("new_message", {"thread_id": thread.id}, to=f"thread_{thread.id}")
        flash("Сообщение отправлено.")
        return redirect(url_for("chat_with_user", username=other.username))

    messages = (
        ChatMessage.query
        .filter_by(thread_id=thread.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    for msg in messages:
        msg.content_html = render_mentions(msg.content)
        msg.is_me = (msg.sender_id == current_user.id)
        if not msg.is_me and not msg.is_read:
            msg.is_read = True

    db.session.commit()

    return render_template("chat.html", other=other, thread=thread, messages=messages)


@app.route("/api/ping")
def api_ping():
    return {"ok": True}


with app.app_context():
    db.create_all()
    seed_categories()

if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)