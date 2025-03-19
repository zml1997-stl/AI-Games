from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import time
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a secure key
socketio = SocketIO(app, cors_allowed_origins="*")

# Game state storage (in-memory for simplicity; use a database in production)
games = {}
feedback = {}

# Drawing prompts (expand this list as needed)
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
    while game_id in games:
        game_id = generate_game_id()
    
    games[game_id] = {
        'players': [player_name],
        'scores': {player_name: 0},
        'host': player_name,
        'started': False,
        'round': 0,
        'drawer': None,
        'prompt': None,
        'guesses': [],
        'round_end_time': None
    }
    session['player_name'] = player_name
    session['game_id'] = game_id
    return redirect(url_for('welcome', game_id=game_id))

@app.route('/join_game', methods=['POST'])
def join_game():
    player_name = request.form['player_name'].strip()
    game_id = request.form['game_id'].upper().strip()
    
    if not player_name or not game_id:
        return render_template('index.html', error="Player name and Game ID are required")
    
    if game_id not in games:
        return render_template('index.html', error="Game ID not found")
    
    if games[game_id]['started']:
        return render_template('index.html', error="Game has already started")
    
    if player_name in games[game_id]['players']:
        return render_template('index.html', error="Name already taken in this game")
    
    games[game_id]['players'].append(player_name)
    games[game_id]['scores'][player_name] = 0
    session['player_name'] = player_name
    session['game_id'] = game_id
    
    socketio.emit('player_joined', {
        'players': games[game_id]['players'],
        'scores': games[game_id]['scores']
    }, room=game_id)
    
    return redirect(url_for('welcome', game_id=game_id))

@app.route('/welcome/<game_id>')
def welcome(game_id):
    if 'player_name' not in session or session['game_id'] != game_id or game_id not in games:
        return redirect(url_for('index'))
    
    game = games[game_id]
    return render_template('welcome.html',
                         game_id=game_id,
                         players=game['players'],
                         host=game['host'],
                         is_host=session['player_name'] == game['host'],
                         player_emojis={player: '🎨' for player in game['players']})

@app.route('/game/<game_id>')
def game(game_id):
    if 'player_name' not in session or session['game_id'] != game_id or game_id not in games:
        return redirect(url_for('index'))
    
    game = games[game_id]
    is_drawer = game['drawer'] == session['player_name']
    return render_template('game.html',
                         game_id=game_id,
                         players=game['players'],
                         scores=game['scores'],
                         player_name=session['player_name'],
                         is_drawer=is_drawer,
                         prompt=game['prompt'] if is_drawer else None,
                         player_emojis={player: '🎨' for player in game['players']})

@app.route('/final_scoreboard/<game_id>')
def final_scoreboard(game_id):
    if game_id not in games:
        return redirect(url_for('index'))
    
    game = games[game_id]
    return render_template('final_scoreboard.html',
                         game_id=game_id,
                         final_scores=game['scores'],
                         player_emojis={player: '🎨' for player in game['players']})

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json()
    game_id = data.get('game_id')
    rating = int(data.get('rating'))
    
    if game_id not in feedback:
        feedback[game_id] = []
    feedback[game_id].append(rating)
    
    return jsonify({'message': 'Thanks for your feedback!'})

@socketio.on('start_game')
def start_game(data):
    game_id = data['game_id']
    if game_id not in games or games[game_id]['started']:
        return
    
    games[game_id]['started'] = True
    games[game_id]['round'] = 1
    start_new_round(game_id)

def start_new_round(game_id):
    game = games[game_id]
    game['drawer'] = random.choice(game['players'])
    game['prompt'] = get_drawing_prompt()
    game['guesses'] = []
    game['round_end_time'] = time.time() + 60  # 60-second rounds
    
    emit('game_started', {
        'drawer': game['drawer'],
        'prompt': game['prompt'],
        'round_duration': 60
    }, room=game_id)
    
    socketio.start_background_task(end_round_task, game_id)

def end_round_task(game_id):
    while time.time() < games[game_id]['round_end_time']:
        socketio.sleep(1)
    
    game = games[game_id]
    game['round'] += 1
    
    if game['round'] <= 5:  # 5 rounds total
        emit('round_ended', {
            'prompt': game['prompt'],
            'players': game['players'],
            'scores': game['scores'],
            'next_round_in': 5
        }, room=game_id)
        socketio.sleep(5)
        start_new_round(game_id)
    else:
        emit('game_ended', {}, room=game_id)
        del games[game_id]

@socketio.on('draw')
def handle_draw(data):
    game_id = data['game_id']
    if game_id in games and games[game_id]['drawer'] == session.get('player_name'):
        emit('draw_update', {
            'x': data['x'],
            'y': data['y'],
            'color': data['color'],
            'erase': data['erase']
        }, room=game_id, include_self=False)

@socketio.on('clear_canvas')
def handle_clear_canvas(data):
    game_id = data['game_id']
    if game_id in games and games[game_id]['drawer'] == session.get('player_name'):
        emit('canvas_cleared', {}, room=game_id, include_self=False)

@socketio.on('submit_guess')
def handle_guess(data):
    game_id = data['game_id']
    player_name = data['player_name']
    guess = data['guess'].lower().strip()
    
    if game_id not in games or player_name == games[game_id]['drawer']:
        return
    
    game = games[game_id]
    emit('guess_made', {'player_name': player_name, 'guess': guess}, room=game_id)
    
    if guess == game['prompt'].lower():
        time_left = max(0, game['round_end_time'] - time.time())
        score = int(50 + (time_left / 60) * 50)  # Base 50 + up to 50 bonus for speed
        game['scores'][player_name] = game['scores'].get(player_name, 0) + score
        game['scores'][game['drawer']] = game['scores'].get(game['drawer'], 0) + 25  # Drawer bonus
        
        emit('correct_guess', {
            'player_name': player_name,
            'prompt': game['prompt'],
            'players': game['players'],
            'scores': game['scores'],
            'next_round_in': 5
        }, room=game_id)
        
        game['round'] += 1
        if game['round'] <= 5:
            socketio.sleep(5)
            start_new_round(game_id)
        else:
            emit('game_ended', {}, room=game_id)
            del games[game_id]

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
    if game_id and game_id in games:
        join_room(game_id)

@socketio.on('disconnect')
def handle_disconnect():
    game_id = session.get('game_id')
    player_name = session.get('player_name')
    if game_id in games and player_name in games[game_id]['players']:
        games[game_id]['players'].remove(player_name)
        del games[game_id]['scores'][player_name]
        emit('player_joined', {
            'players': games[game_id]['players'],
            'scores': games[game_id]['scores']
        }, room=game_id)
        if not games[game_id]['players']:
            del games[game_id]

if __name__ == '__main__':
    socketio.run(app, debug=True)