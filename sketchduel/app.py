from flask import Blueprint, render_template, request, redirect, url_for, session
from app import db, socketio, genai, GEMINI_API_KEY
from .models import SketchDuelRoom, SketchDuelGameState
import random
import string
import json
from datetime import datetime, timedelta

# Initialize SketchDuel blueprint
sketchduel_app = Blueprint('sketchduel', __name__, template_folder='templates', static_folder='../../static')

def generate_room_code():
    while True:
        room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not SketchDuelRoom.query.filter_by(room_code=room_code).first():
            return room_code

def generate_drawing_prompt():
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = """
    As an expert in creative prompts, generate a simple, clear, and engaging drawing prompt suitable for a two-player sketching game. The prompt should:
    - Be a single object, animal, or simple scene (e.g., "a cat", "a tree", "a bicycle").
    - Avoid complex or abstract concepts that are hard to draw in 60 seconds.
    - Be unique and not repetitive with common objects.

    ### Response Format (JSON):
    ```json
    {
      "prompt": "string"
    }
    ### """
    response = model.generate_content(prompt)
    cleaned_text = response.text.strip().replace('json', '').replace('```', '').strip()
    try:
        prompt_data = json.loads(cleaned_text)
        if 'prompt' not in prompt_data or not prompt_data['prompt']:
            raise ValueError("Invalid prompt data")
        return prompt_data['prompt']
    except (json.JSONDecodeError, ValueError) as e:
        return "a random object"  # Fallback prompt
    # Note: The above function is commented out from "### Response Format (JSON):" onward for testing; remove this comment and the ### marker in the final version

# Temporary replacement for generate_drawing_prompt using a predefined list
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

@sketchduel_app.route('/lobby', methods=['GET', 'POST'])
def lobby():
    cleanup_inactive_rooms()  # Clean up inactive rooms on each lobby access
    if request.method == 'POST':
        username = request.form.get('username')
        if not username or len(username) > 50:
            return render_template('sketchduel/lobby.html', error="Please enter a valid username (max 50 characters).")
        session['username'] = username
        room_code = generate_room_code()
        new_room = SketchDuelRoom(room_code=room_code, player1_username=username, last_activity=datetime.utcnow())
        db.session.add(new_room)
        db.session.commit()
        session['room_code'] = room_code
        return redirect(url_for('sketchduel.game', room_code=room_code))
    return render_template('sketchduel/lobby.html')

@sketchduel_app.route('/join/<room_code>', methods=['POST'])
def join_room_route(room_code):
    cleanup_inactive_rooms()  # Clean up inactive rooms on each join attempt
    username = request.form.get('username')
    if not username or len(username) > 50:
        return render_template('sketchduel/lobby.html', error="Please enter a valid username (max 50 characters).")
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room or room.player2_username is not None:
        return render_template('sketchduel/lobby.html', error="Room not found or full")
    room.player2_username = username
    room.last_activity = datetime.utcnow()
    db.session.commit()
    session['username'] = username
    session['room_code'] = room_code
    return redirect(url_for('sketchduel.game', room_code=room_code))

@sketchduel_app.route('/game/<room_code>')
def game(room_code):
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room or (session.get('username') not in [room.player1_username, room.player2_username]):
        return redirect(url_for('sketchduel.lobby'))
    room.last_activity = datetime.utcnow()  # Update last activity
    db.session.commit()
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not game_state:
        prompt = generate_drawing_prompt()
        game_state = SketchDuelGameState(room_id=room.id, prompt=prompt)
        db.session.add(game_state)
        db.session.commit()
    return render_template('sketchduel/game.html', room_code=room_code)

@sketchduel_app.route('/scoreboard/<room_code>')
def scoreboard(room_code):
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room:
        return redirect(url_for('sketchduel.lobby'))
    return render_template('sketchduel/scoreboard.html', room_code=room_code, score_p1=room.score_p1, score_p2=room.score_p2)

@socketio.on('connect', namespace='/sketchduel')
def handle_sketchduel_connect():
    room_code = session.get('room_code')
    if room_code:
        join_room(room_code)
        emit('connected', {'message': 'Connected to SketchDuel room'}, room=room_code)

@socketio.on('start_drawing', namespace='/sketchduel')
def handle_start_drawing(data):
    room_code = data.get('room_code')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room:
        emit('error', {'message': 'Room not found'}, room=room_code)
        return
    room.last_activity = datetime.utcnow()  # Update last activity
    db.session.commit()
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not game_state:
        emit('error', {'message': 'Game state not initialized'}, room=room_code)
        return
    game_state.is_drawing_phase = True
    game_state.time_left = 60
    room.current_drawer_username = room.player1_username if random.choice([True, False]) else room.player2_username
    db.session.commit()
    emit('drawing_started', {'prompt': game_state.prompt, 'drawer_username': room.current_drawer_username, 'time_left': game_state.time_left}, room=room_code)

@socketio.on('update_drawing', namespace='/sketchduel')
def handle_update_drawing(data):
    room_code = data.get('room_code')
    drawing_data = data.get('drawing_data')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if room:
        room.last_activity = datetime.utcnow()  # Update last activity
        db.session.commit()
    emit('drawing_update', {'drawing_data': drawing_data}, room=room_code, include_self=False)

@socketio.on('submit_guess', namespace='/sketchduel')
def handle_submit_guess(data):
    room_code = data.get('room_code')
    guess = data.get('guess')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if not room or not game_state:
        emit('error', {'message': 'Invalid game state'}, room=room_code)
        return
    room.last_activity = datetime.utcnow()  # Update last activity
    game_state.is_drawing_phase = False
    game_state.time_left = 30
    db.session.commit()
    if guess.lower() == game_state.prompt.lower():
        if room.current_drawer_username == room.player1_username:
            room.score_p1 += 1
        else:
            room.score_p2 += 1
        db.session.commit()
    emit('guess_submitted', {'guess': guess, 'correct': guess.lower() == game_state.prompt.lower(), 'score_p1': room.score_p1, 'score_p2': room.score_p2}, room=room_code)
    # Check win condition (first to 5 points)
    if room.score_p1 >= 5 or room.score_p2 >= 5:
        emit('game_ended', {'room_code': room_code}, room=room_code)

@socketio.on('update_timer', namespace='/sketchduel')
def handle_update_timer(data):
    room_code = data.get('room_code')
    room = SketchDuelRoom.query.filter_by(room_code=room_code).first()
    if not room:
        return
    room.last_activity = datetime.utcnow()  # Update last activity
    game_state = SketchDuelGameState.query.filter_by(room_id=room.id).first()
    if game_state and game_state.time_left > 0:
        game_state.time_left -= 1
        db.session.commit()
        emit('timer_updated', {'time_left': game_state.time_left}, room=room_code)
        if game_state.time_left == 0:
            if game_state.is_drawing_phase:
                game_state.is_drawing_phase = False
                game_state.time_left = 30
                db.session.commit()
                emit('switch_to_guessing', {'message': 'Time to guess!'}, room=room_code)
            else:
                room.current_drawer_username = room.player2_username if room.current_drawer_username == room.player1_username else room.player1_username
                prompt = generate_drawing_prompt()
                game_state.prompt = prompt
                game_state.is_drawing_phase = True
                game_state.time_left = 60
                db.session.commit()
                emit('new_round', {'prompt': prompt, 'drawer_username': room.current_drawer_username, 'time_left': game_state.time_left}, room=room_code)
    # Check win condition after each timer update
    if room.score_p1 >= 5 or room.score_p2 >= 5:
        emit('game_ended', {'room_code': room_code}, room=room_code)

@socketio.on('game_ended', namespace='/sketchduel')
def handle_game_ended(data):
    room_code = data.get('room_code')
    emit('redirect', {'url': url_for('sketchduel.scoreboard', room_code=room_code)}, room=room_code)