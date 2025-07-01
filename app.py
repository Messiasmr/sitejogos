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
import random
from bson import ObjectId
import gridfs

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
fs = gridfs.GridFS(db)

# Upload folders
UPLOAD_FOLDER = "static/uploads"
UPLOAD_FOLDER_BG = "static/uploads/backgrounds"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_BG, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["UPLOAD_FOLDER_BG"] = UPLOAD_FOLDER_BG

socketio = SocketIO(app)

# Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        profile_picture = request.files.get("profile_picture")

        # Validação dos campos obrigatórios
        if not name or not username or not email or not password:
            flash("Preencha todos os campos obrigatórios!")
            return redirect("/register")

        # Checa duplicidade de email
        if usuarios.find_one({"email": email}):
            flash("Este e-mail já está em uso.", "error")
            return redirect("/register")
        if usuarios.find_one({"username": username}):
            flash("Nome de usuário indisponível. Escolha outro.", "error")
            return redirect("/register")

        user_id = str(uuid.uuid4())  # Gere antes de salvar a foto

        profile_picture_id = None
        if profile_picture and profile_picture.filename:
            # Salva a imagem no GridFS
            profile_picture_id = fs.put(profile_picture.read(), filename=profile_picture.filename, content_type=profile_picture.mimetype)
        hashed_password = generate_password_hash(password)

        usuarios.insert_one({
            "user_id": user_id,
            "name": name,
            "username": username,
            "email": email,
            "password": hashed_password,
            "profile_picture": str(profile_picture_id) if profile_picture_id else None,
            "friends": [],
            "friend_requests": [],
            "bio": "",
            "badges": [],
            "games": [],
            "level": 1
        })
        flash("Cadastro realizado com sucesso! Faça login para continuar.")
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

# Perfil do usuário logado
@app.route("/profile")
def profile():
    if "user" not in session:
        return redirect("/login")
    logged_user = usuarios.find_one({"email": session["user"]})
    if not logged_user:
        return redirect("/login")
    return redirect(f"/profile/{logged_user['user_id']}")

# Perfil de qualquer usuário
@app.route("/profile/<user_id>")
def profile_by_id(user_id):
    user = usuarios.find_one({"user_id": user_id})
    if not user:
        return "Usuário não encontrado", 404  # Ou: return redirect("/profile")
    logged_user = None
    already_requested = False
    is_friend = False
    if "user" in session:
        logged_user = usuarios.find_one({"email": session["user"]})
        if logged_user and "friends" in logged_user:
            logged_user["friends"] = [str(fid) for fid in logged_user["friends"]]
            is_friend = str(user["user_id"]) in logged_user["friends"]
        if user and "friend_requests" in user and logged_user:
            already_requested = str(logged_user["user_id"]) in [str(fid) for fid in user["friend_requests"]]
    # Exemplo de montagem dos comentários
    comments_collection = db["comments"]  # ou como você acessa sua coleção

    comment_list = []
    for c in user.get("comments", []):
        author_id = str(c.get("author_id"))
        author = usuarios.find_one({"user_id": author_id})
        comment_list.append({
            "author_id": author_id,
            "author_name": author["name"] if author else "Usuário",
            "author_username": author["username"] if author and "username" in author else "",
            "author_profile_picture": author["profile_picture"] if author and author.get("profile_picture") else "static/uploads/unnamed.png",
            "text": c.get("text", ""),
            "timestamp": c.get("timestamp"),
        })

    return render_template(
        "profile.html",
        user=user,
        logged_user=logged_user,
        already_requested=already_requested,
        is_friend=is_friend,
        usuarios=usuarios,
        comments=comment_list  # Passe a lista correta!
    )

# Atualizar perfil
@app.route("/update-profile", methods=["GET", "POST"])
def update_profile():
    if "user" not in session:
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    if request.method == "POST":
        name = request.form.get("name")
        bio = request.form.get("bio")
        profile_picture = request.files.get("profile_picture")
        unique_art = request.files.get("unique_art")
        bg_image = request.files.get("bg_image")
        update_data = {"name": name, "bio": bio}

        # Foto de perfil
        if profile_picture and profile_picture.filename:
            # Salva a imagem no GridFS
            profile_picture_id = fs.put(profile_picture.read(), filename=profile_picture.filename, content_type=profile_picture.mimetype)
            update_data["profile_picture"] = str(profile_picture_id)
        # Arte única
        if unique_art and unique_art.filename:
            unique_art_id = fs.put(unique_art.read(), filename=unique_art.filename, content_type=unique_art.mimetype)
            update_data["unique_art"] = str(unique_art_id)
        # Imagem de fundo
        if bg_image and bg_image.filename:
            bg_image_id = fs.put(bg_image.read(), filename=bg_image.filename, content_type=bg_image.mimetype)
            update_data["background_image"] = str(bg_image_id)

        usuarios.update_one(
            {"email": session["user"]},
            {"$set": update_data}
        )
        flash("Fotos atualizadas com sucesso!", "success")
        return redirect(f"/profile/{user['user_id']}")
    return render_template("update_profile.html", user=user)

# Adicionar comentário
@app.route('/comment', methods=['POST'])
def comment():
    print(request.form)  # Adicione esta linha para depurar
    profile_id = request.form.get('profile_id')
    comment_text = request.form.get('comment')
    if not comment_text:
        return "Campo 'comment' não enviado!", 400
    # Pegue o usuário logado corretamente
    if "user" not in session:
        flash('Você precisa estar logado para comentar.', 'error')
        return redirect('/login')

    logged_user = usuarios.find_one({"email": session["user"]})
    if not logged_user:
        flash('Usuário não encontrado.', 'error')
        return redirect('/login')

    user_id = logged_user["user_id"]

    if not comment_text.strip():
        flash('Comentário vazio!', 'error')
        return redirect(request.referrer or '/')

    usuarios.update_one(
        {'user_id': profile_id},
        {'$push': {'comments': {
            'author_id': user_id,     # <-- AGORA ESTÁ CORRETO!
            'text': comment_text,
            'timestamp': datetime.utcnow()
        }}}
    )
    return redirect(f'/profile/{profile_id}')

# Enviar solicitação de amizade
@app.route("/add_friend", methods=["POST"])
def add_friend():
    if "user" not in session:
        flash("Faça login para adicionar amigos.", "warning")
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    friend_id = request.form.get("friend_id")
    if not user or not friend_id:
        flash("Erro ao adicionar amigo.", "danger")
        return redirect(request.referrer or "/profile")
    usuarios.update_one(
        {"user_id": friend_id},
        {"$addToSet": {"friend_requests": user["user_id"]}}
    )
    flash("Solicitação de amizade enviada!", "success")
    return redirect(f"/profile/{friend_id}")

# Remover amigo
@app.route("/remove_friend", methods=["POST"])
def remove_friend():
    if "user" not in session:
        flash("Faça login para remover amigos.", "warning")
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    friend_id = request.form.get("friend_id")
    if not user or not friend_id:
        flash("Erro ao remover amigo.", "danger")
        return redirect(request.referrer or "/profile")
    usuarios.update_one(
        {"user_id": user["user_id"]},
        {"$pull": {"friends": friend_id}}
    )
    usuarios.update_one(
        {"user_id": friend_id},
        {"$pull": {"friends": user["user_id"]}}
    )
    flash("Amigo removido!", "info")
    return redirect(request.referrer or "/profile")

# Cancelar solicitação de amizade
@app.route("/cancel_friend_request", methods=["POST"])
def cancel_friend_request():
    if "user" not in session:
        flash("Faça login para cancelar solicitações.", "warning")
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    friend_id = request.form.get("friend_id")
    if not user or not friend_id:
        flash("Erro ao cancelar solicitação.", "danger")
        return redirect(request.referrer or "/profile")
    usuarios.update_one(
        {"user_id": friend_id},
        {"$pull": {"friend_requests": user["user_id"]}}
    )
    flash("Solicitação de amizade cancelada.", "info")
    return redirect(f"/profile/{friend_id}")

# Responder solicitação de amizade
@app.route("/respond-friend-request", methods=["POST"])
def respond_friend_request():
    if "user" not in session:
        flash("Faça login para responder solicitações.", "warning")
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    from_user_id = request.form.get("from_user_id")
    action = request.form.get("action")
    if not user or not from_user_id or action not in ["accept", "reject"]:
        flash("Erro ao responder solicitação.", "danger")
        return redirect(request.referrer or "/profile")
    # Remove solicitação
    usuarios.update_one(
        {"user_id": user["user_id"]},
        {"$pull": {"friend_requests": from_user_id}}
    )
    if action == "accept":
        # Adiciona ambos como amigos
        usuarios.update_one(
            {"user_id": user["user_id"]},
            {"$addToSet": {"friends": from_user_id}}
        )
        usuarios.update_one(
            {"user_id": from_user_id},
            {"$addToSet": {"friends": user["user_id"]}}
        )
        flash("Solicitação aceita! Agora vocês são amigos.", "success")
    else:
        flash("Solicitação recusada.", "info")
    return redirect(request.referrer or "/profile")

# Notificações (mensagens privadas)
@app.route("/notifications")
def notifications():
    if "user" not in session:
        return redirect("/login")
    user = usuarios.find_one({"email": session["user"]})
    msgs = list(messages.find({"recipient_id": user["user_id"]}))
    # Adiciona solicitações de amizade como notificações
    friend_requests = []
    if user.get("friend_requests"):
        friend_requests = list(usuarios.find({"user_id": {"$in": user["friend_requests"]}}))
    return render_template("notification.html", messages=msgs, friend_requests=friend_requests)

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
    if not game_id:
        return jsonify({"success": False, "error": "Dados incompletos"}), 400

    favorites = user.get("favorites", [])
    idx = next(
        (i for i, fav in enumerate(favorites)
         if (isinstance(fav, dict) and fav.get("game_id") == game_id) or (isinstance(fav, str) and fav == game_id)),
        None
    )
    if idx is not None:
        favorites.pop(idx)
        usuarios.update_one({"_id": user["_id"]}, {"$set": {"favorites": favorites}})
        return jsonify({"success": True, "action": "removed"})
    else:
        usuarios.update_one(
            {"_id": user["_id"]},
            {"$push": {"favorites": {
                "game_id": game_id,
                "game_name": game_name or "",
                "game_image": game_image or ""
            }}}
        )
        return jsonify({"success": True, "action": "added"})

# Busca de amigos
@app.route("/search_friend")
def search_friend():
    logged_user = None
    if "user" in session:
        logged_user = usuarios.find_one({"email": session["user"]})
    q = request.args.get("q", "").strip()
    results = []
    buscou = False
    if q:
        buscou = True
        results = list(usuarios.find({"$or": [
            {"username": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}}
        ]}))
        # Remover o próprio usuário dos resultados
        if logged_user:
            results = [u for u in results if u.get("user_id") != logged_user.get("user_id")]
    return render_template("search_friend.html", results=results, buscou=buscou)

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

    # Adiciona o usuário logado ao contexto
    logged_user = None
    if "user" in session:
        logged_user = usuarios.find_one({"email": session["user"]})

    return render_template(
        "favoritos.html",
        user=user,
        favoritos=favoritos_pagina,
        total=total,
        page=page,
        per_page=per_page,
        amigos=amigos,
        logged_user=logged_user
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

@app.route("/api/storybot", methods=["POST"])
def storybot():
    data = request.get_json()
    user_msg = data.get("message", "")
    # Aqui você pode integrar com uma IA real, mas vamos simular:
    if "zelda" in user_msg.lower():
        reply = "Em The Legend of Zelda, Link embarca em uma jornada épica para salvar a princesa Zelda e derrotar Ganon!"
    elif "mario" in user_msg.lower():
        reply = "Mario é um encanador bigodudo que sempre salva a Princesa Peach do terrível Bowser!"
    else:
        reply = "Me peça uma história de algum jogo famoso, como Zelda ou Mario!"

    # Acrescenta respostas e histórias extras
    historias_extras = [
        
        "Em Minecraft, Steve encontra um portal misterioso que o leva para o mundo de Terraria. Lá, ele precisa aprender novas habilidades e unir forças com heróis pixelados para derrotar um chefe colossal que ameaça ambos os universos.",
        "Em God of War, Kratos descobre um artefato que permite viajar entre diferentes mitologias dos games. Ele enfrenta desafios ao lado de personagens como Lara Croft e Master Chief, mostrando que até deuses precisam de aliados.",
        "Durante um campeonato mundial de eSports, os finalistas são sugados para dentro do próprio jogo. Agora, cada um deve usar suas habilidades únicas de FPS, RPG e MOBA para sobreviver a chefes inspirados em lendas dos videogames e encontrar o caminho de volta ao mundo real.",
        "Em Pokémon, Ash encontra um Pokémon lendário nunca visto antes: o Digitamon. Para capturá-lo, ele precisa resolver enigmas e batalhar em arenas virtuais, onde as regras mudam a cada rodada.",
        "Em Sonic, o ouriço azul precisa correr contra o tempo para impedir que Robotnik use um artefato que mistura todos os mundos dos jogos em um só. A aventura é cheia de loopings, portais e participações especiais de outros mascotes dos games.",
        "Em Resident Evil, Claire Redfield descobre que a Umbrella Corporation está desenvolvendo um vírus capaz de transformar jogos pacíficos em survival horror. Ela precisa impedir que o vírus se espalhe para outros universos digitais.",
        "Em League of Legends, um novo campeão surge: o Programador. Suas habilidades permitem alterar o código do jogo em tempo real, criando bugs e vantagens inesperadas para sua equipe.",
        "No espaço sideral, uma nave tripulada por personagens de jogos indie viaja de planeta em planeta, enfrentando desafios inspirados em Tetris, Pac-Man e Hollow Knight. A cada vitória, um novo minigame é desbloqueado, e a tripulação descobre segredos sobre a origem dos jogos eletrônicos.",
        "Olá! Peça uma história de algum jogo ou pergunte sobre universos gamers que eu conto algo legal!",
        "Que tal experimentar um jogo indie diferente hoje? Jogos como Hollow Knight, Celeste ou Stardew Valley oferecem experiências incríveis!",
        "Em Chrono Trigger, Crono e seus amigos descobrem uma linha do tempo alternativa onde Lavos se alia aos heróis para impedir uma ameaça ainda maior: um vírus digital que apaga eras inteiras da história.",
        "Em Donkey Kong Country, Donkey e Diddy precisam atravessar uma ilha infestada de robôs controlados por King K. Rool, usando barris tecnológicos e aliados improváveis como Sonic e Tails.",
        "Em Street Fighter, Ryu e Chun-Li são convidados para um torneio interdimensional onde enfrentam lutadores de Mortal Kombat, Tekken e até personagens de jogos de luta obscuros dos anos 90.",
        "Em Sonic 3 & Knuckles, Sonic e Knuckles unem forças para impedir que Dr. Robotnik use as Esmeraldas do Caos para abrir portais para outros mundos dos games, trazendo desafios de plataformas nunca vistos.",
        "Em EarthBound, Ness e seus amigos viajam para o mundo dos sonhos dos videogames, onde enfrentam versões distorcidas de Mario, Link e Samus, aprendendo lições sobre amizade e coragem.",
        "Em Castlevania, Simon Belmont descobre que Drácula está tentando dominar não só a Transilvânia, mas também o universo digital dos jogos. Para vencê-lo, Simon precisa da ajuda de Alucard, Trevor e até de personagens de outros clássicos do terror.",
        "Em Sonic CD, Amy Rose é sequestrada por um novo vilão vindo do futuro dos videogames. Sonic precisa correr pelo tempo, visitando fases inspiradas em jogos de PC como Doom e Quake.",
        "Em Super Metroid, Samus Aran explora um planeta onde cada área é inspirada em um gênero diferente: plataforma, puzzle, RPG e até simulador de fazenda. Cada chefe derrotado libera uma habilidade única de outro universo gamer.",
        "Em The Secret of Monkey Island, Guybrush Threepwood encontra um portal para o mundo real e precisa usar seu humor e inteligência para resolver enigmas baseados em memes e cultura pop dos anos 2000.",
        "Em Doom, o Doom Slayer é transportado para o universo de Minecraft, onde precisa enfrentar Creepers e Endermen usando armas clássicas e blocos explosivos.",
        "Em Age of Empires, um bug faz com que civilizações de diferentes épocas se enfrentem em batalhas épicas: vikings contra samurais, egípcios contra astronautas, tudo é possível nesse crossover histórico.",
        "Em Sonic & All-Stars Racing, os personagens da SEGA desafiam Mario e seus amigos para uma corrida maluca por pistas inspiradas em clássicos do PC, como The Sims, SimCity e RollerCoaster Tycoon.",
        "Em Final Fantasy VI, Terra lidera uma rebelião não só contra o Império, mas também contra vilões de outros RPGs, como Kefka, Sephiroth e até Bowser, em uma aventura cheia de magia e reviravoltas.",
        "Em Mega Man X, X e Zero enfrentam Mavericks que escaparam para outros universos, como o de Metroid e Castlevania, usando upgrades inspirados em armas lendárias de outros jogos.",
        "Em Prince of Persia, o Príncipe descobre uma ampulheta que permite viajar para jogos de plataforma famosos, como Aladdin, Lion King e até Super Mario World, resolvendo puzzles e enfrentando chefes clássicos.",
        "Em Bomberman, nosso herói precisa atravessar labirintos explosivos em uma cidade futurista onde cada bomba cria portais para outros mundos. No final, Bomberman descobre que o maior desafio é unir todos os jogadores para derrotar um chefão que manipula o tempo.",
        "Em Top Gear, pilotos de todo o planeta competem em pistas que mudam de cenário a cada volta: desertos viram cidades, cidades viram circuitos na lua! O vencedor ganha o direito de criar sua própria pista secreta.",
        "No universo de Spider-Man, Peter Parker enfrenta vilões clássicos e novos, mas desta vez precisa unir forças com versões alternativas de si mesmo vindas de outros jogos e quadrinhos para salvar o multiverso dos games.",
        "Em Crazy Taxi, motoristas malucos disputam corridas pelas ruas de uma cidade onde passageiros podem ser personagens de outros jogos, como Sonic, Lara Croft e até o próprio Mario. Quem fizer mais manobras radicais ganha poderes especiais!",
        "Em Roblox, um grupo de amigos descobre um mapa secreto que leva a mundos criados por jogadores lendários. Eles precisam resolver enigmas e vencer minigames para desbloquear o lendário 'Bloco Dourado', capaz de criar qualquer universo imaginável.",
        "Em Valorant, agentes recebem uma missão especial: proteger um servidor que conecta todos os jogos online do mundo. Para isso, precisam enfrentar hackers, bugs e até personagens de outros FPS clássicos como Counter-Strike e Overwatch.",
        "Em The Sims, uma família é surpreendida ao receber visitas de personagens de outros jogos, como Donkey Kong e Samus Aran. Cada visita traz desafios e minigames únicos para manter a casa em ordem.",
        "Em GTA, um novo protagonista precisa conquistar respeito em uma cidade onde as gangues são formadas por personagens de jogos antigos, como Pac-Man, Donkey Kong e Mega Man. Missões malucas e perseguições épicas garantem a diversão.",
        "Em Harvest Moon, a fazenda é invadida por criaturas de Pokémon e Digimon. O fazendeiro precisa aprender a cuidar dos monstrinhos e usá-los para plantar, colher e proteger a vila de ameaças inusitadas.",
        "Em Counter-Strike, os times descobrem um novo modo onde cada rodada se passa em mapas inspirados em outros jogos: castelos de Zelda, pistas de Mario Kart e até arenas de Mortal Kombat.",
        "Em Sonic Adventure, Sonic e seus amigos viajam para o mundo de Minecraft, onde precisam construir pistas de corrida e enfrentar desafios de sobrevivência em blocos.",
        "Em Mortal Kombat, um torneio especial reúne lutadores de Street Fighter, Tekken, King of Fighters e até personagens de jogos de aventura, criando batalhas nunca vistas antes.",
        "Em FIFA, um bug faz com que jogadores de futebol ganhem superpoderes inspirados em heróis dos quadrinhos e dos games. Cada partida vira um verdadeiro espetáculo de habilidades especiais.",
        "Em Terraria, o mundo é invadido por portais que levam a fases de Castlevania, Metroid e Mega Man. O herói precisa coletar itens lendários para fechar os portais e restaurar o equilíbrio.",
        "Em Pac-Man, o labirinto se transforma em uma cidade moderna, e os fantasmas agora têm poderes especiais. Pac-Man precisa usar gadgets e veículos para escapar e coletar todas as frutas tecnológicas.",
        "Em Zelda: A Link to the Past, Link descobre um espelho mágico que o transporta para o mundo de Super Mario World. Lá, ele precisa unir forças com Mario para derrotar Bowser e Ganon juntos.",
        "Em Donkey Kong, Donkey e Diddy participam de uma competição de parkour em cidades inspiradas em Mirror's Edge e Assassin's Creed, enfrentando obstáculos e chefes em cenários urbanos.",
        "Em Portal, Chell encontra portais que a levam para puzzles inspirados em Tetris, Portal Knights e até puzzles de jogos mobile famosos.",
        "Em Castlevania: Symphony of the Night, Alucard precisa explorar um castelo que muda de forma a cada noite, trazendo desafios inspirados em Hollow Knight, Dark Souls e Bloodborne.",
        "Em Super Mario RPG, Mario, Bowser e Peach formam uma equipe para enfrentar um novo vilão que ameaça transformar todos os jogos em RPGs de turno, mudando as regras do universo dos games."
        "Sou a IA do Mobilete Chat, pronta para criar histórias e conversar sobre o universo dos jogos!"
    ]


    # Interação extra com base na mensagem do usuário
    msg_lower = user_msg.lower()
    if "pokemon" in msg_lower:
        reply = historias_extras[3]
    elif "sonic" in msg_lower:
        reply = historias_extras[4]
    elif "resident evil" in msg_lower:
        reply = historias_extras[5]
    elif "league of legends" in msg_lower or "lol" in msg_lower:
        reply = historias_extras[6]
    elif "história" in msg_lower or "conta" in msg_lower or "story" in msg_lower:
        reply = random.choice(historias_extras[:8])
    elif "dica" in msg_lower or "sugestão" in msg_lower or "recommend" in msg_lower:
        reply = historias_extras[9]
    elif "olá" in msg_lower or "oi" in msg_lower or "hello" in msg_lower:
        reply = historias_extras[8]
    elif "quem é você" in msg_lower or "quem é voce" in msg_lower:
        reply = historias_extras[10]
    # Se não cair em nenhum caso, mantém o comportamento original

    return jsonify({"reply": reply})

@app.route("/")
def index():
    logged_user = None
    friend_requests = []
    if "user" in session:
        logged_user = usuarios.find_one({"email": session["user"]})
        if logged_user and logged_user.get("friend_requests"):
            friend_requests = list(usuarios.find({"user_id": {"$in": logged_user["friend_requests"]}}))
    return render_template(
        "index.html",
        logged_user=logged_user,
        friend_requests=friend_requests,
        # ...outros contextos...
    )

@app.route('/friends/<user_id>')
def friends(user_id):
    user = usuarios.find_one({'user_id': user_id})
    amigos = [usuarios.find_one({'user_id': fid}) for fid in user.get('friends', [])]
    return render_template('friends.html', user=user, amigos=amigos)

@app.route('/delete_comment/<profile_id>/<int:comment_index>', methods=['POST'])
def delete_comment(profile_id, comment_index):
    if "user" not in session:
        flash('Você precisa estar logado para apagar comentários.', 'error')
        return redirect('/login')

    user = usuarios.find_one({"email": session["user"]})
    profile = usuarios.find_one({"user_id": profile_id})
    if not profile:
        flash('Perfil não encontrado.', 'error')
        return redirect(request.referrer or '/')

    # Só permite apagar o próprio comentário ou se for dono do perfil
    comments = profile.get("comments", [])
    if comment_index < 0 or comment_index >= len(comments):
        flash('Comentário não encontrado.', 'error')
        return redirect(request.referrer or '/')

    comment = comments[comment_index]
    if comment.get("author_id") != user["user_id"] and profile_id != user["user_id"]:
        flash('Você não tem permissão para apagar este comentário.', 'error')
        return redirect(request.referrer or '/')

    # Remove o comentário pelo índice
    usuarios.update_one(
        {"user_id": profile_id},
        {"$unset": {f"comments.{comment_index}": 1}}
    )
    usuarios.update_one(
        {"user_id": profile_id},
        {"$pull": {"comments": None}}
    )
    flash('Comentário apagado com sucesso!', 'success')
    return redirect(f'/profile/{profile_id}')

@app.route('/check_username')
def check_username():
    username = request.args.get('username', '').strip().lower()
    exists = usuarios.find_one({"username": username})
    suggestion = None
    if exists:
        suggestion = f"{username}{random.randint(100,999)}"
    return jsonify({
        "available": not bool(exists),
        "suggestion": suggestion
    })

@app.route('/check_email')
def check_email():
    email = request.args.get('email', '').strip().lower()
    exists = usuarios.find_one({"email": email})
    return jsonify({
        "available": not bool(exists)
    })

@app.route("/api/favorites")
def api_favorites():
    if "user" not in session:
        return jsonify([])
    user = usuarios.find_one({"email": session["user"]})
    favs = user.get("favorites", [])
    # Corrigido: suporta dict e str
    return jsonify([
        f["game_id"] if isinstance(f, dict) and "game_id" in f else f
        for f in favs
        if (isinstance(f, dict) and "game_id" in f) or isinstance(f, str)
    ])

@app.route('/imagem/<img_id>')
def imagem(img_id):
    try:
        gridout = fs.get(ObjectId(img_id))
        return app.response_class(gridout.read(), mimetype=gridout.content_type)
    except Exception:
        abort(404)

if __name__ == "__main__":
    socketio.run(app, debug=True)
