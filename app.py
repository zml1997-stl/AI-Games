from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
import random
import string
import time
from datetime import datetime
from models import db, Game, Player, Round, Guess  # Import from models.py

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a secure key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sketchduel.db'  # SQLite for simplicity; adjust as needed
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)  # Initialize the database
socketio = SocketIO(app, cors_allowed_origins="*")

# Drawing prompts (could be moved to a database table later)
DRAWING_PROMPTS = [
    "cat", "house", "tree", "car", "dog", "sun", "moon", "star", "flower", "boat",
    "apple", "pizza", "guitar", "bird", "fish", "mountain", "cloud", "chair", "hat", "rocket"
]

def generate_game_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def get_drawing_prompt():
    return random.choice(DRAWING_PROMPTS)

@app.route('/')
def index():
    return render_template('index.html', error=None)

@app.route('/create_game', methods=['POST'])
def create_game():
    player_name = request.form['player_name'].strip()
    if not player_name:
        return render_template('index.html', error="Player name cannot be empty")
    
    game_id = generate_game_id()
    while Game.query.get(game_id):
        game_id = generate_game_id()
    
    game = Game(id=game_id, host=player_name)
    player = Player(game_id=game_id, name=player_name, score=0)
    db.session.add(game)
    db.session.add(player)
    db.session.commit()
    
    session['player_name'] = player_name
    session['game_id'] = game_id
    return redirect(url_for('welcome', game_id=game_id))

@app.route('/join_game', methods=['POST'])
def join_game():
    player_name = request.form['player_name'].strip()
    game_id = request.form['game_id'].upper().strip()
    
    if not player_name or not game_id:
        return render_template('index.html', error="Player name and Game ID are required")
    
    game = Game.query.get(game_id)
    if not game:
        return render_template('index.html', error="Game ID not found")
    
    if game.started:
        return render_template('index.html', error="Game has already started")
    
    if Player.query.filter_by(game_id=game_id, name=player_name).first():
        return render_template('index.html', error="Name already taken in this game")
    
    player = Player(game_id=game_id, name=player_name, score=0)
    db.session.add(player)
    db.session.commit()
    
    session['player_name'] = player_name
    session['game_id'] = game_id
    
    players = [p.name for p in Player.query.filter_by(game_id=game_id).all()]
    scores = {p.name: p.score for p in Player.query.filter_by(game_id=game_id).all()}
    socketio.emit('player_joined', {'players': players, 'scores': scores}, room=game_id)
    
    return redirect(url_for('welcome', game_id=game_id))

@app.route('/welcome/<game_id>')
def welcome(game_id):
    if 'player_name' not in session or session['game_id'] != game_id:
        return redirect(url_for('index'))
    
    game = Game.query.get_or_404(game_id)
    players = [p.name for p in Player.query.filter_by(game_id=game_id).all()]
    return render_template('welcome.html',
                         game_id=game_id,
                         players=players,
                         host=game.host,
                         is_host=session['player_name'] == game.host,
                         player_emojis={player: '🎨' for player in players})

@app.route('/game/<game_id>')
def game(game_id):
    if 'player_name' not in session or session['game_id'] != game_id:
        return redirect(url_for('index'))
    
    game = Game.query.get_or_404(game_id)
    players = Player.query.filter_by(game_id=game_id).all()
    current_round = Round.query.filter_by(game_id=game_id, round_number=game.round_number).first()
    
    is_drawer = current_round and current_round.drawer == session['player_name']
    prompt = current_round.prompt if is_drawer and current_round else None
    
    return render_template('game.html',
                         game_id=game_id,
                         players=[p.name for p in players],
                         scores={p.name: p.score for p in players},
                         player_name=session['player_name'],
                         is_drawer=is_drawer,
                         prompt=prompt,
                         player_emojis={p.name: '🎨' for p in players})

@app.route('/final_scoreboard/<game_id>')
def final_scoreboard(game_id):
    game = Game.query.get_or_404(game_id)
    players = Player.query.filter_by(game_id=game_id).all()
    return render_template('final_scoreboard.html',
                         game_id=game_id,
                         final_scores={p.name: p.score for p in players},
                         player_emojis={p.name: '🎨' for p in players})

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json()
    game_id = data.get('game_id')
    rating = int(data.get('rating'))
    # Store feedback in a table if desired; for now, just return a response
    return jsonify({'message': 'Thanks for your feedback!'})

@socketio.on('start_game')
def start_game(data):
    game_id = data['game_id']
    game = Game.query.get_or_404(game_id)
    if game.started:
        return
    
    game.started = True
    game.round_number = 1
    db.session.commit()
    start_new_round(game_id)

def start_new_round(game_id):
    game = Game.query.get(game_id)
    players = Player.query.filter_by(game_id=game_id).all()
    drawer = random.choice([p.name for p in players])
    prompt = get_drawing_prompt()
    
    new_round = Round(
        game_id=game_id,
        round_number=game.round_number,
        drawer=drawer,
        prompt=prompt,
        start_time=datetime.utcnow()
    )
    db.session.add(new_round)
    db.session.commit()
    
    emit('game_started', {
        'drawer': drawer,
        'prompt': prompt,
        'round_duration': 60
    }, room=game_id)
    
    socketio.start_background_task(end_round_task, game_id)

def end_round_task(game_id):
    game = Game.query.get(game_id)
    current_round = Round.query.filter_by(game_id=game_id, round_number=game.round_number).first()
    
    while time.time() < (time.mktime(current_round.start_time.timetuple()) + 60):
        socketio.sleep(1)
    
    current_round.end_time = datetime.utcnow()
    db.session.commit()
    
    game.round_number += 1
    db.session.commit()
    
    players = [p.name for p in Player.query.filter_by(game_id=game_id).all()]
    scores = {p.name: p.score for p in Player.query.filter_by(game_id=game_id).all()}
    
    if game.round_number <= 5:  # 5 rounds total
        emit('round_ended', {
            'prompt': current_round.prompt,
            'players': players,
            'scores': scores,
            'next_round_in': 5
        }, room=game_id)
        socketio.sleep(5)
        start_new_round(game_id)
    else:
        emit('game_ended', {}, room=game_id)
        # Clean up database records if desired
        # db.session.delete(game)
        # db.session.commit()

@socketio.on('draw')
def handle_draw(data):
    game_id = data['game_id']
    game = Game.query.get(game_id)
    current_round = Round.query.filter_by(game_id=game_id, round_number=game.round_number).first()
    if game and current_round.drawer == session.get('player_name'):
        emit('draw_update', {
            'x': data['x'],
            'y': data['y'],
            'color': data['color'],
            'erase': data['erase']
        }, room=game_id, include_self=False)

@socketio.on('clear_canvas')
def handle_clear_canvas(data):
    game_id = data['game_id']
    game = Game.query.get(game_id)
    current_round = Round.query.filter_by(game_id=game_id, round_number=game.round_number).first()
    if game and current_round.drawer == session.get('player_name'):
        emit('canvas_cleared', {}, room=game_id, include_self=False)

@socketio.on('submit_guess')
def handle_guess(data):
    game_id = data['game_id']
    player_name = data['player_name']
    guess = data['guess'].lower().strip()
    
    game = Game.query.get(game_id)
    current_round = Round.query.filter_by(game_id=game_id, round_number=game.round_number).first()
    if not game or not current_round or player_name == current_round.drawer:
        return
    
    new_guess = Guess(
        round_id=current_round.id,
        player_name=player_name,
        guess_text=guess,
        timestamp=datetime.utcnow(),
        correct=(guess == current_round.prompt.lower())
    )
    db.session.add(new_guess)
    db.session.commit()
    
    emit('guess_made', {'player_name': player_name, 'guess': guess}, room=game_id)
    
    if new_guess.correct:
        time_left = max(0, (time.mktime(current_round.start_time.timetuple()) + 60) - time.time())
        score = int(50 + (time_left / 60) * 50)  # Base 50 + up to 50 bonus for speed
        drawer_bonus = 25
        
        guesser = Player.query.filter_by(game_id=game_id, name=player_name).first()
        drawer = Player.query.filter_by(game_id=game_id, name=current_round.drawer).first()
        guesser.score += score
        drawer.score += drawer_bonus
        current_round.end_time = datetime.utcnow()
        db.session.commit()
        
        players = [p.name for p in Player.query.filter_by(game_id=game_id).all()]
        scores = {p.name: p.score for p in Player.query.filter_by(game_id=game_id).all()}
        
        emit('correct_guess', {
            'player_name': player_name,
            'prompt': current_round.prompt,
            'players': players,
            'scores': scores,
            'next_round_in': 5
        }, room=game_id)
        
        game.round_number += 1
        db.session.commit()
        
        if game.round_number <= 5:
            socketio.sleep(5)
            start_new_round(game_id)
        else:
            emit('game_ended', {}, room=game_id)
            # Optional: db.session.delete(game); db.session.commit()

@socketio.on('chat_message')
def handle_chat(data):
    game_id = data['game_id']
    emit('chat_update', {
        'player_name': data['player_name'],
        'message': data['message']
    }, room=game_id)

@socketio.on('connect')
def handle_connect():
    game_id = session.get('game_id')
    if game_id and Game.query.get(game_id):
        join_room(game_id)

@socketio.on('disconnect')
def handle_disconnect():
    game_id = session.get('game_id')
    player_name = session.get('player_name')
    if not game_id or not player_name:
        return
    
    game = Game.query.get(game_id)
    if game:
        player = Player.query.filter_by(game_id=game_id, name=player_name).first()
        if player:
            db.session.delete(player)
            db.session.commit()
            players = [p.name for p in Player.query.filter_by(game_id=game_id).all()]
            scores = {p.name: p.score for p in Player.query.filter_by(game_id=game_id).all()}
            emit('player_joined', {'players': players, 'scores': scores}, room=game_id)
            if not players:
                db.session.delete(game)
                db.session.commit()

if __name__ == '__main__':
    socketio.run(app, debug=True)