from flask import Blueprint, render_template, request, redirect, url_for, session
from app import db, socketio, genai, GEMINI_API_KEY
from .models import SketchDuelRoom, SketchDuelGameState
import random
import string
import json
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize SketchDuel blueprint
sketchduel_app = Blueprint('sketchduel', __name__, template_folder='templates', static_folder='static')

def generate_room_code():
    while True:
        room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not SketchDuelRoom.query.filter_by(room_code=room_code).first():
            return room_code

def generate_drawing_prompt():
    prompts = [
        "a cat", "a tree", "a bicycle", "a house", "a dog", "a car", "a bird",
        "a flower", "a fish", "a sun", "a cloud", "a boat", "a hat", "a shoe"
    ]
    return random.choice(prompts)

def cleanup_inactive_rooms():
    """Delete rooms inactive for more than 30 minutes."""
    thirty_minutes_ago = datetime.utcnow() - timedelta(minutes=30)
    inactive_rooms = SketchDuelRoom.query.filter(SketchDuelRoom.last_activity < thirty_minutes_ago).all()
    for room in inactive_rooms:
        SketchDuelGameState.query.filter_by(room_id=room.id).delete()
        db.session.delete(room)
    db.session.commit()

def update_room_activity(room_code):
    """Update the last_activity timestamp for a room."""
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if room:
        room.last_activity = datetime.utcnow()
        db.session.commit()
        logger.debug(f"Updated last_activity for room {room_code}")

@sketchduel_app.route('/lobby', methods=['GET', 'POST'])
def lobby():
    cleanup_inactive_rooms()
    if request.method == 'POST':
        username = request.form.get('username')
        if not username or len(username) > 50:
            return render_template('lobby.html', error="Please enter a valid username (max 50 characters).")
        # Create a new room
        room_code = generate_room_code()
        new_room = SketchDuelRoom(
            room_code=room_code,
            player1_username=username,  # Host
            last_activity=datetime.utcnow(),
            status='waiting'  # Add status field to track game state
        )
        db.session.add(new_room)
        db.session.commit()
        # Store session data
        session['username'] = username
        session['room_code'] = room_code
        session.permanent = True
        logger.info(f"Room {room_code} created by {username}")
        return redirect(url_for('sketchduel.game', room_code=room_code))
    return render_template('lobby.html')

@sketchduel_app.route('/join/<room_code>', methods=['POST'])
def join_room_route(room_code):
    cleanup_inactive_rooms()
    username = request.form.get('username')
    if not username or len(username) > 50:
        return render_template('lobby.html', error="Please enter a valid username (max 50 characters).")
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room or room.status != 'waiting':
        return render_template('lobby.html', error="Room not found or game already started")
    if room.player2_username is not None:
        return render_template('lobby.html', error="Room is full (only 2 players allowed)")
    room.player2_username = username
    room.last_activity = datetime.utcnow()
    db.session.commit()
    session['username'] = username
    session['room_code'] = room_code
    session.permanent = True
    logger.info(f"Player {username} joined room {room_code}")
    return redirect(url_for('sketchduel.game', room_code=room_code))

@sketchduel_app.route('/game/<room_code>')
def game(room_code):
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room or (session.get('username') not in [room.player1_username, room.player2_username]):
        session.pop('room_code', None)
        session.pop('username', None)
        return redirect(url_for('sketchduel.lobby'))
    update_room_activity(room_code)
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not game_state and room.status == 'in_progress':
        prompt = generate_drawing_prompt()
        game_state = SketchDuelGameState(room_id=room.id, prompt=prompt)
        db.session.add(game_state)
        db.session.commit()
    return render_template('game.html', room_code=room_code, username=session.get('username'), is_host=(session.get('username') == room.player1_username))

@sketchduel_app.route('/scoreboard/<room_code>')
def scoreboard(room_code):
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room:
        return redirect(url_for('sketchduel.lobby'))
    return render_template('scoreboard.html', room_code=room_code, score_p1=room.score_p1, score_p2=room.score_p2)

# SocketIO Events
@socketio.on('connect', namespace='/sketchduel')
def handle_sketchduel_connect():
    logger.debug(f"Client connected: {request.sid}")

@socketio.on('disconnect', namespace='/sketchduel')
def handle_sketchduel_disconnect():
    username = session.get('username')
    room_code = session.get('room_code')
    if not username or not room_code:
        return
    with app.app_context():
        room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
        if room:
            # Mark player as disconnected (we'll use a flag in the model)
            if room.player1_username == username:
                room.player1_username = None  # Clear the player slot
            elif room.player2_username == username:
                room.player2_username = None
            db.session.commit()
            socketio.emit('player_disconnected', {'username': username}, room=room_code, namespace='/sketchduel')
            socketio.emit('player_left', {
                'username': username,
                'players': [p for p in [room.player1_username, room.player2_username] if p]
            }, room=room_code, namespace='/sketchduel')
            if room.status == 'in_progress' and not (room.player1_username and room.player2_username):
                room.status = 'waiting'
                db.session.commit()
                socketio.emit('game_paused', {'message': 'All players disconnected'}, room=room_code, namespace='/sketchduel')
            update_room_activity(room_code)

@socketio.on('join_game_room', namespace='/sketchduel')
def handle_join_game_room(data):
    room_code = data.get('room_code')
    username = data.get('username')
    with app.app_context():
        room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
        if not room:
            socketio.emit('error', {'message': 'Room not found'}, to=request.sid, namespace='/sketchduel')
            return
        if username not in [room.player1_username, room.player2_username]:
            socketio.emit('error', {'message': 'You are not a player in this room'}, to=request.sid, namespace='/sketchduel')
            return
        join_room(room_code)
        players = [p for p in [room.player1_username, room.player2_username] if p]
        current_drawer = room.current_drawer_username if room.status == 'in_progress' else None
        socketio.emit('player_rejoined', {
            'username': username,
            'players': players,
            'status': room.status,
            'current_drawer': current_drawer
        }, room=room_code, namespace='/sketchduel')
        logger.debug(f"Player {username} joined room {room_code}")
        update_room_activity(room_code)

@socketio.on('start_game', namespace='/sketchduel')
def handle_start_game(data):
    room_code = data.get('room_code')
    username = data.get('username')
    with app.app_context():
        room = SketchDuelRoom.query.filter_by(room_code=room_code, player1_username=username).first()
        if not room:
            socketio.emit('error', {'message': 'Room not found or not host'}, room=room_code, namespace='/sketchduel')
            return
        if room.status != 'waiting':
            socketio.emit('error', {'message': 'Game already started'}, room=room_code, namespace='/sketchduel')
            return
        if not room.player2_username:
            socketio.emit('error', {'message': 'Waiting for another player to join'}, room=room_code, namespace='/sketchduel')
            return
        room.status = 'in_progress'
        db.session.commit()
        players = [room.player1_username, room.player2_username]
        socketio.emit('game_started', {
            'current_drawer': room.player1_username,  # Start with Player 1
            'players': players
        }, room=room_code, namespace='/sketchduel')
        logger.debug(f"Room {room_code}: Game started by {username}")
        update_room_activity(room_code)

@socketio.on('start_drawing', namespace='/sketchduel')
def handle_start_drawing(data):
    room_code = data.get('room_code')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room or room.status != 'in_progress':
        socketio.emit('error', {'message': 'Room not found or game not in progress'}, room=room_code, namespace='/sketchduel')
        return
    update_room_activity(room_code)
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not game_state:
        prompt = generate_drawing_prompt()
        game_state = SketchDuelGameState(room_id=room.id, prompt=prompt)
        db.session.add(game_state)
        db.session.commit()
    game_state.is_drawing_phase = True
    game_state.time_left = 60
    room.current_drawer_username = room.player1_username if random.choice([True, False]) else room.player2_username
    db.session.commit()
    socketio.emit('drawing_started', {
        'prompt': game_state.prompt,
        'drawer_username': room.current_drawer_username,
        'time_left': game_state.time_left
    }, room=room_code, namespace='/sketchduel')

@socketio.on('update_drawing', namespace='/sketchduel')
def handle_update_drawing(data):
    room_code = data.get('room_code')
    drawing_data = data.get('drawing_data')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if room:
        update_room_activity(room_code)
        socketio.emit('drawing_update', {'drawing_data': drawing_data}, room=room_code, namespace='/sketchduel', include_self=False)

@socketio.on('submit_guess', namespace='/sketchduel')
def handle_submit_guess(data):
    room_code = data.get('room_code')
    guess = data.get('guess')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not room or not game_state:
        socketio.emit('error', {'message': 'Invalid game state'}, room=room_code, namespace='/sketchduel')
        return
    update_room_activity(room_code)
    game_state.is_drawing_phase = False
    game_state.time_left = 30
    db.session.commit()
    if guess.lower() == game_state.prompt.lower():
        if room.current_drawer_username == room.player1_username:
            room.score_p1 += 1
        else:
            room.score_p2 += 1
        db.session.commit()
    socketio.emit('guess_submitted', {
        'guess': guess,
        'correct': guess.lower() == game_state.prompt.lower(),
        'score_p1': room.score_p1,
        'score_p2': room.score_p2
    }, room=room_code, namespace='/sketchduel')
    if room.score_p1 >= 5 or room.score_p2 >= 5:
        socketio.emit('game_ended', {'room_code': room_code}, room=room_code, namespace='/sketchduel')

@socketio.on('update_timer', namespace='/sketchduel')
def handle_update_timer(data):
    room_code = data.get('room_code')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room:
        return
    update_room_activity(room_code)
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if game_state and game_state.time_left > 0:
        game_state.time_left -= 1
        db.session.commit()
        socketio.emit('timer_updated', {'time_left': game_state.time_left}, room=room_code, namespace='/sketchduel')
        if game_state.time_left == 0:
            if game_state.is_drawing_phase:
                game_state.is_drawing_phase = False
                game_state.time_left = 30
                db.session.commit()
                socketio.emit('switch_to_guessing', {'message': 'Time to guess!'}, room=room_code, namespace='/sketchduel')
            else:
                room.current_drawer_username = room.player2_username if room.current_drawer_username == room.player1_username else room.player1_username
                prompt = generate_drawing_prompt()
                game_state.prompt = prompt
                game_state.is_drawing_phase = True
                game_state.time_left = 60
                db.session.commit()
                socketio.emit('new_round', {
                    'prompt': prompt,
                    'drawer_username': room.current_drawer_username,
                    'time_left': game_state.time_left
                }, room=room_code, namespace='/sketchduel')
    if room.score_p1 >= 5 or room.score_p2 >= 5:
        socketio.emit('game_ended', {'room_code': room_code}, room=room_code, namespace='/sketchduel')

@socketio.on('game_ended', namespace='/sketchduel')
def handle_game_ended(data):
    room_code = data.get('room_code')
    socketio.emit('redirect', {'url': url_for('sketchduel.scoreboard', room_code=room_code)}, room=room_code, namespace='/sketchduel')