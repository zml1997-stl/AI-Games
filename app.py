from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import uuid
import google.generativeai as genai
from dotenv import load_dotenv
import secrets
import json
import difflib
import re
from flask_socketio import SocketIO, emit, join_room, leave_room
import string
import random
import re
import time

def generate_game_id():
    # Loop to avoid collisions in the unlikely event the ID already exists.
    while True:
        game_id = ''.join(random.choices(string.ascii_uppercase, k=4))
        if game_id not in games:
            return game_id
            
# Load environment variables
load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
socketio = SocketIO(app, cors_allowed_origins="*")

# Game state storage
games = {}

# List of random topics for the random topic feature
RANDOM_TOPICS = [
    "Science", "History", "Geography", "Movies", "Music", "Technology", 
    "Sports", "Art", "Literature", "Food", "Animals", "Space", 
    "Video Games", "TV Shows", "Famous People", "Inventions", 
    "World Records", "Natural Wonders", "Ancient Civilizations", "Ocean Life"
]

# Helper functions for fuzzy matching
def normalize_text(text):
    # Lowercase, trim whitespace, and remove punctuation.
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    return text

def is_close_enough(user_answer, correct_answer, threshold=0.8):
    # Normalize both strings.
    user_norm = normalize_text(user_answer)
    correct_norm = normalize_text(correct_answer)
    
    # If both answers are numeric, compare as floats.
    try:
        if float(user_norm) == float(correct_norm):
            return True
    except ValueError:
        pass

    # Use difflib for fuzzy matching.
    ratio = difflib.SequenceMatcher(None, user_norm, correct_norm).ratio()
    return ratio >= threshold

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_game', methods=['POST'])
def create_game():
    username = request.form.get('username')
    if not username:
        return redirect(url_for('index'))
    
    game_id = generate_game_id()
    games[game_id] = {
        'host': username,
        'players': [username],
        'disconnected': set(),
        'status': 'waiting',
        'current_player_index': 0,
        'current_question': None,
        'answers': {},
        'scores': {username: 0},
        'round_number': 0,
        'total_rounds': 15,
        'question_timer': None
    }
    
    session['game_id'] = game_id
    session['username'] = username
    
    return redirect(url_for('game', game_id=game_id))

@app.route('/join_game', methods=['POST'])
def join_game():
    username = request.form.get('username')
    game_id = request.form.get('game_id')
    
    if not username or not game_id:
        return redirect(url_for('index'))
    
    if game_id not in games:
        return "Game not found", 404
    
    if games[game_id]['status'] != 'waiting':
        return "Game already in progress", 403
    
    if len(games[game_id]['players']) >= 10:
        return "Game is full", 403
    
    if username not in games[game_id]['players']:
        games[game_id]['players'].append(username)
        games[game_id]['scores'][username] = 0
    
    session['game_id'] = game_id
    session['username'] = username
    
    return redirect(url_for('game', game_id=game_id))

@app.route('/leave_game')
def leave_game():
    game_id = session.get('game_id')
    username = session.get('username')
    
    if game_id and game_id in games and username:
        # Handle the player leaving
        if username in games[game_id]['players']:
            if len(games[game_id]['players']) <= 1 or username == games[game_id]['host']:
                # If last player or host leaves, end the game
                games.pop(game_id, None)
            else:
                # Remove the player from the game
                games[game_id]['players'].remove(username)
                # Handle current player index if needed
                current_index = games[game_id]['current_player_index']
                if current_index >= len(games[game_id]['players']):
                    games[game_id]['current_player_index'] = 0
                
                # Notify other players
                socketio.emit('player_left', {'players': games[game_id]['players']}, to=game_id)
    
    # Clear session
    session.pop('game_id', None)
    session.pop('username', None)
    
    return redirect(url_for('index'))

@app.route('/game/<game_id>')
def game(game_id):
    if game_id not in games:
        return redirect(url_for('index'))
    
    username = session.get('username')
    if not username or username not in games[game_id]['players']:
        return redirect(url_for('index'))
    
    return render_template('game.html', game_id=game_id, username=username, is_host=(username == games[game_id]['host']))

def get_trivia_question(topic):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')  # Use 'gemini-pro'

        prompt = f"""
        Create a trivia question about {topic}. 
        Return your response in the following JSON format:
        {{
            "question": "The trivia question",
            "answer": "The correct answer",
            "explanation": "A brief explanation of the answer"
        }}
        The question should be specific and have a clear, unambiguous answer. Do NOT include any other text besides the JSON.
        """

        response = model.generate_content(prompt)

        # Parse the response text as JSON immediately.
        try:
            # Clean up response.text
            cleaned_text = response.text.replace('`json', '').replace('`', '').strip()
            result = json.loads(cleaned_text)
            return result
        except json.JSONDecodeError as e:
            print(f"JSONDecodeError: {e}")
            print(f"Raw response text: {response.text}")
            return {
                "question": f"What is a notable fact about {topic}?",  # Fallback question
                "answer": "Unable to generate answer",
                "explanation": "There was an error parsing the AI response (JSONDecodeError)."
            }

    except Exception as e:
        print(f"Error generating question: {str(e)}")
        return {
            "question": f"What is a notable fact about {topic}?",  # Fallback question
            "answer": "Unable to generate answer",
            "explanation": "There was an error with the AI service (General Exception)."
        }

@socketio.on('connect')
def handle_connect():
    print("Client connected")

@socketio.on('join_game_room')
def handle_join_game_room(data):
    game_id = data.get('game_id')
    username = data.get('username')
    
    if game_id in games and username in games[game_id]['players']:
        # Remove from disconnected set if the user is rejoining.
        games[game_id].setdefault('disconnected', set()).discard(username)
        join_room(game_id)
        emit('player_joined', {'username': username, 'players': games[game_id]['players']}, to=game_id)

@socketio.on('start_game')
def handle_start_game(data):
    game_id = data.get('game_id')
    username = data.get('username')
    
    if game_id in games and username == games[game_id]['host'] and games[game_id]['status'] == 'waiting':
        games[game_id]['status'] = 'in_progress'
        games[game_id]['round_number'] = 1  # Initialize round counter
        current_player = games[game_id]['players'][games[game_id]['current_player_index']]
        
        emit('game_started', {
            'current_player': current_player,
            'players': games[game_id]['players'],
            'scores': games[game_id]['scores'],
            'round_number': games[game_id]['round_number'],
            'total_rounds': games[game_id]['total_rounds']
        }, to=game_id)

# Helper function to normalize the answer
def normalize_answer(answer):
    return re.sub(r'\s+', ' ', answer.strip().lower())

@socketio.on('select_random_topic')
def handle_select_random_topic(data):
    game_id = data.get('game_id')
    username = data.get('username')
    
    if (game_id in games and 
        username in games[game_id]['players'] and 
        games[game_id]['status'] == 'in_progress' and
        games[game_id]['players'][games[game_id]['current_player_index']] == username):
        
        random_topic = random.choice(RANDOM_TOPICS)
        # Use the existing topic selection handler with the random topic
        handle_select_topic({
            'game_id': game_id,
            'username': username,
            'topic': random_topic
        })

# Check for previously asked questions and answers in a more detailed way
@socketio.on('select_topic')
def handle_select_topic(data):
    game_id = data.get('game_id')
    username = data.get('username')
    topic = data.get('topic')

    if (game_id in games and 
        username in games[game_id]['players'] and 
        games[game_id]['status'] == 'in_progress' and
        games[game_id]['players'][games[game_id]['current_player_index']] == username):

        # Ensure the game has a set to track asked questions and answers
        if 'questions_asked' not in games[game_id]:
            games[game_id]['questions_asked'] = []

        max_attempts = 5  # Limit retries to avoid infinite loops

        for _ in range(max_attempts):
            question_data = get_trivia_question(topic)
            question_text = question_data['question']
            answer_text = question_data['answer']

            normalized_answer = normalize_answer(answer_text)

            # Check for duplicates using normalized answer and question content
            duplicate_found = False
            for prev_question, prev_answer in games[game_id]['questions_asked']:
                normalized_prev_answer = normalize_answer(prev_answer)

                # If both the question and answer are similar, skip this question
                if normalized_answer == normalized_prev_answer or question_text == prev_question:
                    duplicate_found = True
                    break

            if not duplicate_found:
                games[game_id]['questions_asked'].append((question_text, answer_text))
                games[game_id]['current_question'] = question_data
                games[game_id]['answers'] = {}
                
                # Set up the timer for the question
                games[game_id]['question_timer'] = time.time() + 30  # 30 seconds from now

                emit('question_ready', {
                    'question': question_data['question'],
                    'topic': topic,
                    'timer_duration': 30  # Send the timer duration to the client
                }, to=game_id)
                
                # Start the countdown timer on the server
                socketio.start_background_task(question_timer_countdown, game_id)
                return  # Stop retrying if a unique question-answer pair is found

        # If all attempts result in duplicates, notify the players
        emit('error', {'message': "Couldn't generate a unique question. Try another topic."}, to=game_id)

def question_timer_countdown(game_id):
    if game_id not in games:
        return
    
    end_time = games[game_id]['question_timer']
    
    while time.time() < end_time and game_id in games:
        # Sleep for a short time to avoid excessive CPU usage
        socketio.sleep(1)
        
        # If all players have answered, we don't need to wait
        if game_id in games and len(games[game_id]['answers']) == len(games[game_id]['players']):
            break
    
    # Time's up or all players answered
    if game_id in games:
        # Add "Time's up" for players who didn't answer
        for player in games[game_id]['players']:
            if player not in games[game_id]['answers']:
                games[game_id]['answers'][player] = "No answer submitted"
        
        # Proceed with showing results
        process_round_results(game_id)

def process_round_results(game_id):
    if game_id not in games or not games[game_id]['current_question']:
        return
    
    correct_answer = games[game_id]['current_question']['answer']
    correct_players = []
    
    for player, player_answer in games[game_id]['answers'].items():
        # Skip players who didn't answer
        if player_answer == "No answer submitted":
            continue
            
        # Use fuzzy matching to determine if the answer is close enough.
        if is_close_enough(player_answer, correct_answer):
            correct_players.append(player)
            games[game_id]['scores'][player] += 1
    
    # Increment round counter
    games[game_id]['round_number'] += 1
    
    # Check if the game is over (15 rounds)
    game_over = games[game_id]['round_number'] > games[game_id]['total_rounds']
    
    if game_over:
        # Find the winner
        winner = max(games[game_id]['scores'].items(), key=lambda x: x[1])[0]
        
        # Send game over event
        socketio.emit('game_over', {
            'winner': winner,
            'scores': games[game_id]['scores']
        }, to=game_id)
        
        # Update game status
        games[game_id]['status'] = 'completed'
    else:
        # Update to the next player's turn
        games[game_id]['current_player_index'] = (games[game_id]['current_player_index'] + 1) % len(games[game_id]['players'])
        next_player = games[game_id]['players'][games[game_id]['current_player_index']]
        
        socketio.emit('round_results', {
            'correct_answer': correct_answer,
            'explanation': games[game_id]['current_question']['explanation'],
            'player_answers': games[game_id]['answers'],
            'correct_players': correct_players,
            'next_player': next_player,
            'scores': games[game_id]['scores'],
            'round_number': games[game_id]['round_number'],
            'total_rounds': games[game_id]['total_rounds'],
            'game_over': game_over
        }, to=game_id)

@socketio.on('submit_answer')
def handle_submit_answer(data):
    game_id = data.get('game_id')
    username = data.get('username')
    answer = data.get('answer')
    
    if (game_id in games and 
        username in games[game_id]['players'] and 
        games[game_id]['status'] == 'in_progress' and
        games[game_id]['current_question']):
        
        games[game_id]['answers'][username] = answer
        emit('player_answered', {'username': username}, to=game_id)
        
        # Check if all players have answered
        if len(games[game_id]['answers']) == len(games[game_id]['players']):
            # If all players answered, process results immediately
            process_round_results(game_id)

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username')
    game_id = session.get('game_id')
    
    # Instead of removing the user, mark them as disconnected.
    if game_id and game_id in games and username:
        if username in games[game_id]['players']:
            games[game_id].setdefault('disconnected', set()).add(username)
            emit('player_disconnected', {'username': username}, to=game_id)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
