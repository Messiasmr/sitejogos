from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
import pymongo
import uuid
from datetime import datetime
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "chave_segura")

# MongoDB Atlas config
MONGO_USER = os.getenv("Mongo_User")
MONGO_PASSWORD = os.getenv("Mongo_Password")
MONGO_HOST = os.getenv("Mongo_Host")
MONGO_URI = f"mongodb+srv://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}/?retryWrites=true&w=majority"

client = pymongo.MongoClient(MONGO_URI)
db = client["gamestore_db"]
usuarios = db["usuarios"]
comments = db["comments"]
messages = db["messages"]

# Upload folders
UPLOAD_FOLDER = "static/uploads"
UPLOAD_FOLDER_BG = "static/uploads/backgrounds"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_BG, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["UPLOAD_FOLDER_BG"] = UPLOAD_FOLDER_BG

socketio = SocketIO(app)

# Home
@app.route("/")
def home():
    user_email = session.get("user")
    user = usuarios.find_one({"email": user_email}) if user_email else None
    logged_user = user

    page = int(request.args.get("page", 1))

    return render_template(
        "index.html",
        logged_user=logged_user,
        user=user,
        page=page
    )

# Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        username = request.form.get("username")
        profile_picture = request.files.get("profile_picture")

        if usuarios.find_one({"email": email}):
            flash("Usuário já cadastrado!")
            return redirect("/register")

        profile_picture_path = None
        if profile_picture and profile_picture.filename:
            filename = secure_filename(profile_picture.filename)
            profile_picture_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            profile_picture.save(profile_picture_path)

        user_id = str(uuid.uuid4())
        hashed_password = generate_password_hash(password)

        usuarios.insert_one({
            "user_id": user_id,
            "name": name,
            "username": username,
            "email": email,
            "password": hashed_password,
            "profile_picture": profile_picture_path,
            "friends": [],
            "friend_requests": [],
            "bio": "",
            "badges": [],
            "games": [],
            "level": 1
        })
        return redirect("/login")
    return render_template("register.html")

# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = usuarios.find_one({"email": email})
        if user and check_password_hash(user["password"], password):
            session["user"] = email
            return redirect("/")
        else:
            flash("Usuário ou senha inválidos")
    return render_template("login.html")

# Logout
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")

# Perfil
@app.route("/profile")
def profile():
    user_id = request.args.get("id")
    user_email = request.args.get("user") or session.get("user")

    if user_id:
        user = usuarios.find_one({"user_id": user_id})
    elif user_email:
        user = usuarios.find_one({"email": user_email})
    else:
        user = None

    if not user:
        return "Perfil não encontrado", 404

    logged_user_email = session.get("user")
    logged_user = usuarios.find_one({"email": logged_user_email}) if logged_user_email else None

    comments_list = list(comments.find({"profile_owner": user.get("email")}))
    friend_ids = user.get("friends", [])
    friends = list(usuarios.find({"user_id": {"$in": friend_ids}})) if friend_ids else []

    return render_template(
        "profile.html",
        user=user,
        comments=comments_list,
        friends=friends,  # <-- Passa a lista de amigos reais
        logged_user=logged_user,
        usuarios=usuarios
    )

# Atualizar perfil
@app.route("/update-profile", methods=["POST"])
def update_profile():
    user_email = session.get("user")
    if not user_email:
        return redirect("/login")

    user = usuarios.find_one({"email": user_email})
    if not user:
        return redirect("/login")

    update_data = {}

    profile_picture = request.files.get("profile_picture")
    if profile_picture and profile_picture.filename:
        filename = secure_filename(profile_picture.filename)
        profile_picture_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        profile_picture.save(profile_picture_path)
        update_data["profile_picture"] = profile_picture_path

    unique_art = request.files.get("unique_art")
    if unique_art and unique_art.filename:
        art_filename = secure_filename(unique_art.filename)
        unique_art_path = os.path.join(app.config["UPLOAD_FOLDER"], art_filename)
        unique_art.save(unique_art_path)
        update_data["unique_art"] = unique_art_path

    bio = request.form.get("bio")
    if bio is not None:
        update_data["bio"] = bio

    bg_file = request.files.get("bg_image")
    if bg_file and bg_file.filename:
        filename = secure_filename(bg_file.filename)
        bg_path = os.path.join(app.config["UPLOAD_FOLDER_BG"], filename)
        bg_file.save(bg_path)
        update_data["background_image"] = f"{UPLOAD_FOLDER_BG}/{filename}"

    if update_data:
        usuarios.update_one({"email": user_email}, {"$set": update_data})

    return redirect("/profile")

# Adicionar comentário
@app.route("/comment", methods=["POST"])
def comment():
    if "user" not in session:
        return redirect("/login")
    author_email = session["user"]
    author = usuarios.find_one({"email": author_email})
    author_name = author["name"] if author else "Anônimo"

    profile_owner = author_email  # Comentários no próprio perfil
    text = request.form.get("comment")

    if not text:
        return redirect("/profile")

    comment = {
        "profile_owner": profile_owner,
        "author_email": author_email,
        "author_name": author_name,
        "text": text
    }
    comments.insert_one(comment)
    return redirect("/profile")

# Enviar solicitação de amizade
@app.route("/add_friend", methods=["POST"])
def add_friend():
    if "user" not in session:
        return redirect("/login")
    from_user = usuarios.find_one({"email": session["user"]})
    friend_username = request.form.get("friend_username")
    to_user = usuarios.find_one({"username": friend_username})
    if not from_user or not to_user or from_user["user_id"] == to_user["user_id"]:
        return redirect("/profile")
    usuarios.update_one(
        {"user_id": to_user["user_id"]},
        {"$addToSet": {"friend_requests": from_user["user_id"]}}
    )
    return redirect(f"/profile?id={to_user['user_id']}")

# Responder solicitação de amizade
@app.route("/respond-friend-request", methods=["POST"])
def respond_friend_request():
    if "user" not in session:
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    from_user_id = request.form.get("from_user_id")
    action = request.form.get("action")
    if not user or not from_user_id or action not in ["accept", "reject"]:
        return redirect("/profile")
    usuarios.update_one(
        {"user_id": user["user_id"]},
        {"$pull": {"friend_requests": from_user_id}}
    )
    if action == "accept":
        usuarios.update_one(
            {"user_id": user["user_id"]},
            {"$addToSet": {"friends": from_user_id}}
        )
        usuarios.update_one(
            {"user_id": from_user_id},
            {"$addToSet": {"friends": user["user_id"]}}
        )
    return redirect("/profile")

# Notificações (mensagens privadas)
@app.route("/notifications")
def notifications():
    if "user" not in session:
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    msgs = messages.find({"recipient_id": user["user_id"]})
    return render_template("notification.html", messages=msgs)

# API - Jogos
@app.route("/api/games")
def api_games():
    search = request.args.get("search", "")
    page = request.args.get("page", 1)
    api_key = os.getenv("RAWG_API_KEY")
    url = f"https://api.rawg.io/api/games?key={api_key}&search={search}&page={page}"
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": [], "count": 0, "error": str(e)}), 500

# Favoritar jogo
@app.route("/favorite", methods=["POST"])
def favorite():
    if "user" not in session:
        return jsonify({"success": False, "error": "Login necessário"}), 401
    user = usuarios.find_one({"email": session["user"]})
    game_id = request.json.get("game_id")
    game_name = request.json.get("game_name")
    game_image = request.json.get("game_image")
    if not game_id or not game_name:
        return jsonify({"success": False, "error": "Dados incompletos"}), 400
    # Evita duplicidade
    if any(fav["game_id"] == game_id for fav in user.get("favorites", [])):
        return jsonify({"success": False, "error": "Já está nos favoritos"}), 409
    usuarios.update_one(
        {"_id": user["_id"]},
        {"$push": {"favorites": {
            "game_id": game_id,
            "game_name": game_name,
            "game_image": game_image
        }}}
    )
    return jsonify({"success": True})

# Busca de amigos
@app.route("/search_friend")
def search_friend():
    if "user" not in session:
        return redirect(url_for("login"))
    q = request.args.get("q", "").strip()
    results = []
    if q:
        results = list(usuarios.find({
            "$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"username": {"$regex": q, "$options": "i"}}
            ]
        }))
    return render_template("search_friend.html", results=results, q=q)

# Favoritos
@app.route("/favoritos")
def favoritos():
    if "user" not in session:
        return redirect(url_for("login"))
    user_id = request.args.get("user_id")
    page = int(request.args.get("page", 1))
    per_page = 8

    # Se não passar user_id, mostra os favoritos do usuário logado
    if user_id:
        user = usuarios.find_one({"user_id": user_id})
    else:
        user = usuarios.find_one({"email": session["user"]})

    if not user:
        return "Usuário não encontrado", 404

    favoritos = user.get("favorites", [])
    total = len(favoritos)
    start = (page - 1) * per_page
    end = start + per_page
    favoritos_pagina = favoritos[start:end]

    # Para mostrar amigos na barra lateral ou topo (opcional)
    amigos = []
    if user.get("friends"):
        amigos = list(usuarios.find({"user_id": {"$in": user["friends"]}}))

    return render_template(
        "favoritos.html",
        user=user,
        favoritos=favoritos_pagina,
        total=total,
        page=page,
        per_page=per_page,
        amigos=amigos
    )

# Garante que todos os usuários tenham user_id
for user in usuarios.find({"user_id": {"$exists": False}}):
    usuarios.update_one(
        {"_id": user["_id"]},
        {"$set": {"user_id": str(uuid.uuid4())}}
    )

# Evento de mensagem
@socketio.on('send_message')
def handle_send_message(data):
    room = data.get('room', 'global')
    emit('receive_message', data, room=room)

@socketio.on('join')
def on_join(data):
    room = data.get('room', 'global')
    join_room(room)

@socketio.on('leave')
def on_leave(data):
    room = data.get('room', 'global')
    leave_room(room)

if __name__ == "__main__":
    socketio.run(app, debug=True)
