from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
import os
import google.generativeai as genai
from dotenv import load_dotenv
import secrets
import json
import random
import string
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
import logging
from models import db, migrate, Game, Player, Topic, Question, Answer, Rating
from sqlalchemy import create_engine, func
from sqlalchemy.exc import SQLAlchemyError
from tenacity import retry, stop_after_attempt, wait_fixed
import timeout_decorator
import threading

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

RANDOM_TOPICS = ["World history", "Ancient civilizations", "US presidents", "World War II", "The Renaissance", "Science", "Famous scientists", "Space exploration", "Medical breakthroughs", "Astronomy", "Geography", "World capitals", "Famous landmarks", "Natural wonders", "Countries and cultures", "Sports", "Olympic history", "World sports tournaments", "Famous athletes", "Sports records", "Pop culture", "Movies", "Famous movie quotes", "Television", "Iconic TV shows", "Music", "Classical composers", "Pop music hits", "Musical instruments", "Broadway musicals", "Literature", "Classic literature", "Famous authors", "Mythology", "Fairytales and folklore", "Technology", "Inventions that changed the world", "Video game history", "Internet culture", "Famous inventors", "Art", "Famous paintings", "Art movements", "Architecture", "Fashion trends", "Food and cuisine", "Famous chefs", "World cuisines", "Holiday traditions", "Christmas traditions", "Animals", "Animal kingdom", "Dinosaurs", "Endangered species", "Superheroes", "Historical figures", "Famous explorers", "Women in history", "Civil rights movements", "Cold War", "TikTok trends", "AI in social media", "Short-form video", "Influencer marketing", "Social commerce", "Viral memes", "Live shopping events", "Gen Z culture", "Social media challenges", "Creator economy"]

PLAYER_EMOJIS = ["🚗", "🐶", "🎩", "🚀", "🦄", "🍕", "🐙", "🐢", "🤖", "🧙‍♂️", "🦁", "✈️", "🧀", "⚽", "🎬", "🐘", "⛵", "🎲", "🦇", "🍔"]

active_timers = {}
random_click_counters = {}
recent_random_topics = {}
unread_messages = {}

# Initialize Flask app
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

if 'DATABASE_URL' in os.environ:
    url = os.environ['DATABASE_URL'].replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_size': 5, 'max_overflow': 10}
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trivia.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate.init_app(app, db)

with app.app_context():
    try:
        engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
        with engine.connect() as connection:
            logger.info("Database connection established successfully")
        db.create_all()
    except SQLAlchemyError as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise

socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True, ping_timeout=60)

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found in environment variables.")
    raise ValueError("GEMINI_API_KEY is required")
genai.configure(api_key=GEMINI_API_KEY)

def generate_game_id():
    while True:
        game_id = ''.join(random.choices(string.ascii_uppercase, k=4))
        if not Game.query.filter_by(id=game_id).first():
            return game_id

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def update_game_activity(game_id):
    try:
        game = Game.query.filter_by(id=game_id).first()
        if game:
            game.last_activity = datetime.utcnow()
            db.session.commit()
            logger.debug(f"Updated last_activity for game {game_id}")
    except Exception as e:
        logger.error(f"Error updating last_activity for game {game_id}: {str(e)}")
        db.session.rollback()
        raise

def get_or_create_topic(topic_name):
    normalized_name = topic_name.lower().strip()
    topic = Topic.query.filter_by(normalized_name=normalized_name).first()
    if not topic:
        topic = Topic(normalized_name=normalized_name)
        db.session.add(topic)
        db.session.commit()
    return topic

def get_player_top_topics(game_id, username, limit=3):
    player = Player.query.filter_by(game_id=game_id, username=username).first()
    if not player:
        logger.debug(f"No player found for {username} in game {game_id}")
        return "Enter a topic or click Random Topic"
    top_topics = (db.session.query(Topic.normalized_name, func.count(Rating.id).label('like_count'))
                  .join(Rating, Rating.topic_id == Topic.id)
                  .filter(Rating.game_id == game_id, Rating.player_id == player.id, Rating.rating == 1)
                  .group_by(Topic.normalized_name)
                  .order_by(func.count(Rating.id).desc())
                  .limit(limit)
                  .all())
    result = ", ".join([row.normalized_name for row in top_topics]) if top_topics else "Enter a topic or click Random Topic"
    logger.debug(f"Top liked topics for {username} in game {game_id}: {result}")
    return result

def suggest_random_topic(game_id, username=None):
    try:
        if game_id not in recent_random_topics:
            recent_random_topics[game_id] = []
        if game_id not in random_click_counters:
            random_click_counters[game_id] = {}
        if username and username not in random_click_counters[game_id]:
            random_click_counters[game_id][username] = 0
        last_question = db.session.query(Question.topic_id).filter(Question.game_id == game_id).order_by(Question.id.desc()).first()
        last_topic = Topic.query.get(last_question.topic_id).normalized_name if last_question else None
        player = Player.query.filter_by(game_id=game_id, username=username).first()
        if not player:
            logger.debug(f"No player found for {username} in game {game_id}, using fallback topics")
            candidate_topics = [t.lower().strip() for t in RANDOM_TOPICS if t.lower().strip() not in recent_random_topics[game_id]]
            topic = random.choice(candidate_topics or RANDOM_TOPICS)
            if username:
                random_click_counters[game_id][username] += 1
            recent_random_topics[game_id].append(topic)
            if len(recent_random_topics[game_id]) > 3:
                recent_random_topics[game_id].pop(0)
            logger.debug(f"Game {game_id}: Suggested random topic '{topic}' for {username or 'unknown'}")
            return topic
        liked_topics = db.session.query(Topic.normalized_name).join(Rating, Rating.topic_id == Topic.id).filter(Rating.game_id == game_id, Rating.player_id == player.id, Rating.rating == 1).group_by(Topic.normalized_name).all()
        disliked_topics = db.session.query(Topic.normalized_name).join(Rating, Rating.topic_id == Topic.id).filter(Rating.game_id == game_id, Rating.player_id == player.id, Rating.rating == 0).group_by(Topic.normalized_name).all()
        liked_topic_names = [t.normalized_name for t in liked_topics]
        disliked_topic_names = [t.normalized_name for t in disliked_topics]
        logger.debug(f"Game {game_id}: Liked topics for {username}: {liked_topic_names}")
        logger.debug(f"Game {game_id}: Disliked topics for {username}: {disliked_topic_names}")
        logger.debug(f"Game {game_id}: Random click count for {username}: {random_click_counters[game_id][username]}")
        candidate_topics = [t.lower().strip() for t in RANDOM_TOPICS if t.lower().strip() not in disliked_topic_names]
        candidate_topics = [t for t in candidate_topics if t not in recent_random_topics[game_id] and t != (last_topic or "")]
        click_count = random_click_counters[game_id][username]
        use_liked = click_count > 0 and click_count % 5 == 0 and liked_topic_names and random.random() < 0.6
        if use_liked:
            liked_candidates = [t for t in liked_topic_names if t not in recent_random_topics[game_id] and t != (last_topic or "")]
            if liked_candidates:
                topic = random.choice(liked_candidates)
                logger.debug(f"Game {game_id}: Selected liked topic '{topic}' for {username} on click {click_count}")
            else:
                topic = random.choice(candidate_topics or RANDOM_TOPICS)
                logger.debug(f"Game {game_id}: No available liked topics, using random '{topic}' for {username}")
        else:
            topic = random.choice(candidate_topics or RANDOM_TOPICS)
            logger.debug(f"Game {game_id}: Selected random topic '{topic}' for {username}")
        random_click_counters[game_id][username] += 1
        recent_random_topics[game_id].append(topic)
        if len(recent_random_topics[game_id]) > 3:
            recent_random_topics[game_id].pop(0)
        return topic
    except Exception as e:
        logger.error(f"Error fetching random topic for game {game_id}, user {username}: {str(e)}")
        db.session.rollback()
        topic = random.choice([t.lower().strip() for t in RANDOM_TOPICS])
        if username:
            random_click_counters[game_id][username] += 1
        recent_random_topics[game_id].append(topic)
        if len(recent_random_topics[game_id]) > 3:
            recent_random_topics[game_id].pop(0)
        return topic

@timeout_decorator.timeout(10, timeout_exception=TimeoutError)
def get_trivia_question(topic, game_id):
    try:
        prior_questions = Question.query.filter_by(game_id=game_id).all()
        model = genai.GenerativeModel('gemini-2.0-flash')
        prior_questions_list = [f"- Question: {q.question_text} (Answer: {q.answer_text})" for q in prior_questions]
        prior_questions_str = "\n".join(prior_questions_list[:10]) if prior_questions_list else "None"
        prompt = f"""
        As an expert in crafting engaging and addictive trivia questions, your task is to generate a trivia question about "{topic}" that is both entertaining and informative. The question should spark curiosity, surprise players, and have a single, definitive answer.  
        
        ### **Requirements:**  
        - **Engaging & Fun:** Make the question intriguing, playful, and moderately challenging—enough to keep players engaged without causing frustration. Use humor, unexpected twists, or surprising facts to make it stand out.  
        - **Clear & Concise:** Players have only 30 seconds to read and answer, so keep wording simple, direct, and easy to grasp at a glance.  
        - **Relevant & Fresh:** Focus on modern times unless the topic specifically relates to a historical event or period. Avoid overused trivia clichés.  
        - **Factually Accurate & Unambiguous:** The question must be precise, well-researched, and have only one correct answer. No vague or misleading wording.  
        - **No Direct Hints:** The correct answer (or synonyms) must not appear in the question itself.  
        - **Completely Unique (Critical):** The question and answer MUST be entirely distinct from any previous ones in this game. Avoid repeating themes, keywords, or related answers (e.g., if a past answer was "Einstein," do not use "relativity" or another famous physicist). Prior questions and answers:  
          {prior_questions_str}  
        - **Multiple Choice Options:** Provide four answer choices—one correct, three incorrect but believable. Ensure distractors are reasonable and distinct from answers.  
        - **Interesting Explanation:** Include a 1-2 sentence explanation with an intriguing fact to keep players engaged.  
          
        ### **Response Format (JSON):**  
        ```json  
        {{  
          "question": "string",  
          "answer": "string",  
          "options": ["string", "string", "string", "string"],  
          "explanation": "string"  
        }}
        """
        for attempt in range(8):
            try:
                response = model.generate_content(prompt)
                cleaned_text = response.text.strip().replace('json', '').replace('```', '').strip()
                try:
                    question_data = json.loads(cleaned_text)
                except json.JSONDecodeError as e:
                    logger.error(f"Attempt {attempt + 1}/8: JSON parsing failed for topic {topic}: {str(e)}. Raw response: {cleaned_text}")
                    if attempt == 7:
                        raise
                    continue
                required_fields = ["question", "answer", "options", "explanation"]
                missing_fields = [field for field in required_fields if field not in question_data or not question_data[field]]
                if missing_fields:
                    logger.error(f"Attempt {attempt + 1}/8: Missing fields {missing_fields} in response for topic {topic}: {question_data}")
                    if attempt == 7:
                        raise ValueError(f"Invalid response format: missing {missing_fields}")
                    continue
                if not isinstance(question_data["options"], list) or len(set(question_data["options"])) != 4:
                    logger.error(f"Attempt {attempt + 1}/8: Invalid options format for topic {topic}: {question_data['options']}")
                    if attempt == 7:
                        raise ValueError("Options must be a list of 4 unique items")
                    continue
                is_similar = False
                for q in prior_questions:
                    if (q.question_text.lower() == question_data['question'].lower() or 
                        q.answer_text.lower() == question_data['answer'].lower() or 
                        q.answer_text.lower() in question_data['answer'].lower() or 
                        question_data['answer'].lower() in q.answer_text.lower()):
                        logger.warning(f"Attempt {attempt + 1}/8: Similarity detected for topic {topic}: {question_data['question']}")
                        is_similar = True
                        break
                if is_similar:
                    if attempt == 7:
                        raise ValueError("Unable to generate a unique question after 8 attempts")
                    continue
                random.shuffle(question_data["options"])
                question_data["is_fallback"] = False
                logger.debug(f"Game {game_id}: Generated unique question '{question_data['question']}' for topic '{topic}' on attempt {attempt + 1}")
                return question_data
            except Exception as e:
                logger.error(f"Attempt {attempt + 1}/8 failed for topic {topic}: {str(e)}")
                if attempt == 7:
                    raise
    except Exception as e:
        logger.error(f"Failed to generate unique question for topic {topic} after 8 attempts: {str(e)}")
        raise ValueError(f"Could not generate a unique question for '{topic}'. Please try a different topic.")

def get_next_active_player(game_id):
    game = Game.query.filter_by(id=game_id).first()
    if not game:
        return None
    active_players = Player.query.filter_by(game_id=game_id, disconnected=False).order_by(Player.id).all()
    if not active_players:
        return None
    current_index = game.current_player_index
    num_players = len(active_players)
    for _ in range(num_players):
        current_index = (current_index + 1) % num_players
        next_player = active_players[current_index]
        if not next_player.disconnected:
            game.current_player_index = current_index
            db.session.commit()
            update_game_activity(game_id)
            return next_player
    return None

def process_round_results(game_id):
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game or not game.current_question:
            logger.debug(f"Game {game_id}: No game or question to process")
            return
        current_question_id = game.current_question['question_id']
        active_players = Player.query.filter_by(game_id=game_id, disconnected=False).all()
        correct_answer = game.current_question['answer']
        is_fallback = game.current_question.get('is_fallback', False)
        correct_players = []
        if not is_fallback:
            correct_players = [p for p in active_players if Answer.query.filter_by(game_id=game_id, player_id=p.id, question_id=current_question_id).first() and Answer.query.filter_by(game_id=game_id, player_id=p.id, question_id=current_question_id).first().answer == correct_answer]
            for p in correct_players:
                p.score += 1
        db.session.commit()
        max_score = max([p.score for p in Player.query.filter_by(game_id=game_id).all()] + [0])
        current_question = Question.query.filter_by(id=current_question_id).first()
        logger.debug(f"Game {game_id}: Processed results for question_id {current_question_id}, max_score: {max_score}")
        if max_score >= 15:
            socketio.emit('game_ended', {'scores': {p.username: p.score for p in Player.query.filter_by(game_id=game_id).all()}, 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game_id).all()}}, room=game_id)
        else:
            next_player = get_next_active_player(game_id)
            if next_player:
                socketio.emit('round_results', {'correct_answer': correct_answer, 'explanation': game.current_question['explanation'], 'player_answers': {p.username: Answer.query.filter_by(game_id=game_id, player_id=p.id, question_id=current_question_id).first().answer if Answer.query.filter_by(game_id=game_id, player_id=p.id, question_id=current_question_id).first() else None for p in Player.query.filter_by(game_id=game_id).all()}, 'correct_players': [p.username for p in correct_players], 'next_player': next_player.username, 'scores': {p.username: p.score for p in Player.query.filter_by(game_id=game_id).all()}, 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game_id).all()}, 'question_id': current_question.id, 'topic_id': current_question.topic_id, 'is_fallback': is_fallback}, room=game_id)
                socketio.emit('request_feedback', {'topic_id': current_question.topic_id}, room=game_id)
                game.current_question = None
                db.session.commit()
                logger.debug(f"Game {game_id}: Emitted round_results, cleared current_question")
                update_game_activity(game_id)

def question_timer(game_id):
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game or game.status != 'in_progress' or not game.current_question:
            logger.debug(f"Game {game_id}: Timer aborted - invalid state")
            if game_id in active_timers:
                del active_timers[game_id]
            return
        logger.debug(f"Game {game_id}: 30s timer expired for question_id {game.current_question['question_id']}")
        active_players = Player.query.filter_by(game_id=game_id, disconnected=False).all()
        current_question_id = game.current_question['question_id']
        for player in active_players:
            if not Answer.query.filter_by(game_id=game_id, player_id=player.id, question_id=current_question_id).first():
                new_answer = Answer(game_id=game_id, player_id=player.id, question_id=current_question_id, answer=None)
                db.session.add(new_answer)
        db.session.commit()
        process_round_results(game_id)
        if game_id in active_timers:
            del active_timers[game_id]
            logger.debug(f"Game {game_id}: Timer completed and removed")

def cleanup_inactive_games():
    while True:
        try:
            with app.app_context():
                now = datetime.utcnow()
                inactive_threshold = now - timedelta(minutes=2)
                inactive_games = Game.query.filter(Game.last_activity < inactive_threshold).all()
                for game in inactive_games:
                    if game.id in active_timers:
                        active_timers[game.id].cancel()
                        del active_timers[game.id]
                    db.session.delete(game)
                    db.session.commit()
                    logger.info(f"Cleaned up inactive game {game.id}")
                    if game.id in recent_random_topics:
                        del recent_random_topics[game.id]
                    if game.id in random_click_counters:
                        del random_click_counters[game.id]
                    if game.id in unread_messages:
                        del unread_messages[game.id]
        except Exception as e:
            logger.error(f"Error in cleanup_inactive_games: {str(e)}")
            db.session.rollback()
        socketio.sleep(60)

socketio.start_background_task(cleanup_inactive_games)

# Import SketchDuel app
from sketchduel import app as sketchduel_app

# Register SketchDuel blueprint
app.register_blueprint(sketchduel_app, url_prefix='/sketchduel')

# Existing Trivia Routes
@app.route('/')
def welcome():
    game_id = session.get('game_id')
    if game_id:
        update_game_activity(game_id)
    return render_template('welcome.html')

@app.route('/play')
def play():
    game_id = session.get('game_id')
    if game_id:
        update_game_activity(game_id)
    return render_template('index.html')

@app.route('/create_game', methods=['POST'])
@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def create_game():
    username = request.form.get('username')
    if not username:
        return render_template('index.html', error="Username is required")
    game_id = generate_game_id()
    try:
        with app.app_context():
            new_game = Game(id=game_id, host=username, status='waiting')
            db.session.add(new_game)
            new_player = Player(game_id=game_id, username=username, score=0, emoji=random.choice(PLAYER_EMOJIS), disconnected=False)
            db.session.add(new_player)
            db.session.commit()
            session['game_id'] = game_id
            session['username'] = username
            session.permanent = True
            update_game_activity(game_id)
            logger.info(f"Game {game_id} created by {username}")
            return redirect(url_for('game', game_id=game_id))
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error creating game: {str(e)}")
        return render_template('index.html', error="An error occurred while creating the game.")

@app.route('/join_game', methods=['POST'])
@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def join_game():
    username = request.form.get('username')
    game_id = request.form.get('game_id')
    if not username or not game_id:
        return render_template('index.html', error="Username and Game ID are required")
    try:
        with app.app_context():
            game = Game.query.filter_by(id=game_id).first()
            if not game:
                return render_template('index.html', error="Game not found")
            if game.status != 'waiting' or Player.query.filter_by(game_id=game_id).count() >= 10:
                return render_template('index.html', error="Game already in progress or full")
            existing_player = Player.query.filter_by(game_id=game_id, username=username).first()
            if existing_player:
                existing_player.disconnected = False
                db.session.commit()
            else:
                available_emojis = [e for e in PLAYER_EMOJIS if e not in [p.emoji for p in Player.query.filter_by(game_id=game_id).all()]]
                new_player = Player(game_id=game_id, username=username, score=0, emoji=random.choice(available_emojis) if available_emojis else random.choice(PLAYER_EMOJIS), disconnected=False)
                db.session.add(new_player)
                db.session.commit()
            session['game_id'] = game_id
            session['username'] = username
            session.permanent = True
            update_game_activity(game_id)
            logger.info(f"Player {username} joined game {game_id}")
            return redirect(url_for('game', game_id=game_id))
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error joining game: {str(e)}")
        return render_template('index.html', error="An error occurred while joining the game.")

@app.route('/game/<game_id>')
def game(game_id):
    try:
        with app.app_context():
            game = Game.query.filter_by(id=game_id).first()
            if not game:
                session.pop('game_id', None)
                session.pop('username', None)
                return redirect(url_for('welcome'))
            username = session.get('username')
            if not username or not Player.query.filter_by(game_id=game_id, username=username).first():
                session.pop('game_id', None)
                session.pop('username', None)
                return redirect(url_for('welcome'))
            update_game_activity(game_id)
            return render_template('game.html', game_id=game_id, username=username, is_host=(username == game.host))
    except Exception as e:
        logger.error(f"Error in game route for game {game_id}: {str(e)}")
        return redirect(url_for('welcome'))

@app.route('/final_scoreboard/<game_id>')
def final_scoreboard(game_id):
    try:
        with app.app_context():
            game = Game.query.filter_by(id=game_id).first()
            if not game:
                return redirect(url_for('welcome'))
            players = Player.query.filter_by(game_id=game_id).all()
            player_scores = {p.username: p.score for p in players}
            player_emojis = {p.username: p.emoji for p in players}
            update_game_activity(game_id)
            return render_template('final_scoreboard.html', game_id=game_id, scores=player_scores, player_emojis=player_emojis)
    except Exception as e:
        logger.error(f"Error in final_scoreboard for game {game_id}: {str(e)}")
        return redirect(url_for('welcome'))

@app.route('/reset_game/<game_id>', methods=['POST'])
@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def reset_game(game_id):
    try:
        with app.app_context():
            session_game_id = session.get('game_id')
            session_username = session.get('username')
            if not session_game_id or not session_username or session_game_id != game_id:
                logger.warning(f"Unauthorized reset attempt for game {game_id} from session {session_game_id}")
                return jsonify({'error': 'Unauthorized: Invalid session'}), 403
            game = Game.query.filter_by(id=game_id).first()
            if not game:
                logger.error(f"Game {game_id} not found for reset")
                return jsonify({'error': 'Game not found'}), 404
            logger.info(f"Resetting game {game_id} by {session_username}")
            if game_id in active_timers:
                active_timers[game_id].cancel()
                del active_timers[game_id]
                logger.debug(f"Cancelled timer for game {game_id} on reset")
            game.status = 'waiting'
            game.current_player_index = 0
            game.current_question = None
            game.question_start_time = None
            players = Player.query.filter_by(game_id=game_id).all()
            for player in players:
                player.score = 0
                player.disconnected = False
            db.session.commit()
            socketio.emit('game_reset', {'players': [p.username for p in players], 'scores': {p.username: p.score for p in players}, 'player_emojis': {p.username: p.emoji for p in players}}, room=game_id)
            update_game_activity(game_id)
            logger.info(f"Game {game_id} successfully reset")
            return jsonify({'success': 'Game reset successfully'}), 200
    except SQLAlchemyError as e:
        logger.error(f"Database error resetting game {game_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Database error occurred'}), 500
    except Exception as e:
        logger.error(f"Unexpected error resetting game {game_id}: {str(e)}")
        return jsonify({'error': 'Unexpected server error'}), 500

@socketio.on('connect')
def handle_connect():
    logger.debug(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username')
    if not username:
        return
    with app.app_context():
        for game in Game.query.all():
            player = Player.query.filter_by(game_id=game.id, username=username).first()
            if player:
                player.disconnected = True
                db.session.commit()
                socketio.emit('player_disconnected', {'username': username}, room=game.id)
                socketio.emit('player_left', {'username': username, 'players': [p.username for p in Player.query.filter_by(game_id=game.id).all()], 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game.id).all()}}, room=game.id)
                if game.status == 'in_progress':
                    active_players = Player.query.filter_by(game_id=game.id, disconnected=False).count()
                    if active_players == 0:
                        game.status = 'waiting'
                        if game.id in active_timers:
                            active_timers[game.id].cancel()
                            del active_timers[game.id]
                        db.session.commit()
                        socketio.emit('game_paused', {'message': 'All players disconnected'}, room=game.id)
                    elif Player.query.filter_by(game_id=game.id).offset(game.current_player_index).first().username == username:
                        next_player = get_next_active_player(game.id)
                        if next_player:
                            socketio.emit('turn_skipped', {'disconnected_player': username, 'next_player': next_player.username}, room=game.id)
            update_game_activity(game.id)

@socketio.on('join_game_room')
def handle_join_game_room(data):
    game_id = data.get('game_id')
    username = data.get('username')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        player = Player.query.filter_by(game_id=game_id, username=username).first()
        if player:
            player.disconnected = False
            player.sid = request.sid
            db.session.commit()
            join_room(game_id)
            players = Player.query.filter_by(game_id=game_id).all()
            current_player = Player.query.filter_by(game_id=game_id).offset(game.current_player_index).first() if game.status == 'in_progress' and Player.query.filter_by(game_id=game_id).count() > game.current_player_index else None
            socketio.emit('player_rejoined', {'username': username, 'players': [p.username for p in players], 'scores': {p.username: p.score for p in players}, 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game_id).all()}, 'status': game.status, 'current_player': current_player.username if current_player else None, 'current_question': game.current_question}, room=game_id)
        elif game.status == 'waiting' and Player.query.filter_by(game_id=game_id).count() < 10:
            available_emojis = [e for e in PLAYER_EMOJIS if e not in [p.emoji for p in Player.query.filter_by(game_id=game_id).all()]]
            new_player = Player(game_id=game_id, username=username, score=0, emoji=random.choice(available_emojis) if available_emojis else random.choice(PLAYER_EMOJIS), disconnected=False, sid=request.sid)
            db.session.add(new_player)
            db.session.commit()
            join_room(game_id)
            players = Player.query.filter_by(game_id=game_id).all()
            socketio.emit('player_joined', {'username': username, 'players': [p.username for p in players], 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game_id).all()}}, room=game_id)
        else:
            socketio.emit('error', {'message': 'Game is full or already started'}, to=request.sid)
        if game_id not in unread_messages:
            unread_messages[game_id] = {}
        if username not in unread_messages[game_id]:
            unread_messages[game_id][username] = 0
        socketio.emit('update_unread_count', {'count': unread_messages[game_id][username]}, to=request.sid)
        update_game_activity(game_id)

@socketio.on('start_game')
def handle_start_game(data):
    game_id = data.get('game_id')
    username = data.get('username')
    with app.app_context():
        game = Game.query.filter_by(id=game_id, host=username, status='waiting').first()
        if not game:
            socketio.emit('error', {'message': 'Game not found or not host'}, room=game_id)
            return
        game.status = 'in_progress'
        game.current_player_index = 0
        db.session.commit()
        players = Player.query.filter_by(game_id=game_id).all()
        current_player = players[game.current_player_index]
        logger.debug(f"Game {game_id}: Started by {username}, current_player={current_player.username}")
        socketio.emit('game_started', {'current_player': current_player.username, 'players': [p.username for p in players], 'scores': {p.username: p.score for p in players}, 'player_emojis': {p.username: p.emoji for p in Player.query.filter_by(game_id=game_id).all()}}, room=game_id)
        update_game_activity(game_id)

@socketio.on('request_player_top_topics')
def handle_request_player_top_topics(data):
    game_id = data.get('game_id')
    username = data.get('username')
    placeholder_text = get_player_top_topics(game_id, username)
    logger.debug(f"Game {game_id}: Sending top topics placeholder '{placeholder_text}' to {username}")
    socketio.emit('player_top_topics', {'placeholder': placeholder_text}, to=request.sid)

@socketio.on('select_topic')
def handle_select_topic(data):
    game_id = data.get('game_id')
    username = data.get('username')
    topic = data.get('topic', '').strip().lower()
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game or game.status != 'in_progress':
            socketio.emit('error', {'message': 'Game not in progress'}, to=request.sid)
            return
        active_players = Player.query.filter_by(game_id=game_id, disconnected=False).order_by(Player.id).all()
        if not active_players:
            socketio.emit('error', {'message': 'No active players'}, to=request.sid)
            return
        current_player = active_players[game.current_player_index % len(active_players)]
        if current_player.disconnected:
            current_player = get_next_active_player(game_id)
            if not current_player:
                socketio.emit('error', {'message': 'No active players available'}, to=request.sid)
                return
        if current_player.username != username:
            socketio.emit('error', {'message': 'Not your turn'}, to=request.sid)
            return
        if not topic:
            topic = suggest_random_topic(game_id, username)
        if game.current_question:
            logger.debug(f"Game {game_id}: Clearing stale current_question before new topic")
            game.current_question = None
            db.session.commit()
        try:
            topic_obj = get_or_create_topic(topic)
            question_data = get_trivia_question(topic, game_id)
            new_question = Question(game_id=game_id, topic_id=topic_obj.id, question_text=question_data['question'], answer_text=question_data['answer'])
            db.session.add(new_question)
            db.session.flush()
            game.current_question = question_data
            game.current_question['question_id'] = new_question.id
            game.question_start_time = datetime.utcnow()
            db.session.commit()
            socketio.emit('question_ready', {'question': question_data['question'], 'options': question_data['options'], 'topic': topic, 'question_id': new_question.id}, room=game_id)
            logger.debug(f"Game {game_id}: Emitted question_ready with question_id {new_question.id}")
            if game_id in active_timers:
                active_timers[game_id].cancel()
                logger.debug(f"Game {game_id}: Cancelled existing timer")
            timer = threading.Timer(30.0, question_timer, args=(game_id,))
            active_timers[game_id] = timer
            timer.start()
            logger.debug(f"Game {game_id}: Started 30s timer for question_id {new_question.id}")
            update_game_activity(game_id)
        except ValueError as e:
            logger.error(f"Game {game_id}: Failed to generate question for '{topic}': {str(e)}")
            socketio.emit('error', {'message': str(e)}, to=request.sid)
            db.session.rollback()
        except Exception as e:
            logger.error(f"Game {game_id}: Unexpected error generating question for '{topic}': {str(e)}")
            socketio.emit('error', {'message': f"Unexpected error generating question for '{topic}'. Please try again."}, to=request.sid)
            db.session.rollback()

@socketio.on('submit_answer')
def handle_submit_answer(data):
    game_id = data.get('game_id')
    username = data.get('username')
    answer = data.get('answer')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        player = Player.query.filter_by(game_id=game_id, username=username).first()
        if not game or not player or game.status != 'in_progress' or not game.current_question:
            logger.debug(f"Game {game_id}: Invalid submit_answer attempt by {username}")
            socketio.emit('error', {'message': 'Invalid game state'}, to=request.sid)
            return
        current_question_id = game.current_question['question_id']
        time_elapsed = datetime.utcnow() - game.question_start_time
        logger.debug(f"Game {game_id}: Answer submitted by {username}, time elapsed: {time_elapsed.total_seconds()}s")
        if time_elapsed.total_seconds() > 30:
            logger.debug(f"Game {game_id}: Time expired for {username}, setting answer to None")
            answer = None
        elif answer in ['A', 'B', 'C', 'D']:
            option_index = ord(answer) - ord('A')
            answer = game.current_question['options'][option_index]
        else:
            logger.debug(f"Game {game_id}: Invalid answer format from {username}: {answer}")
            answer = None
        existing_answer = Answer.query.filter_by(game_id=game_id, player_id=player.id, question_id=current_question_id).first()
        if existing_answer:
            existing_answer.answer = answer
        else:
            new_answer = Answer(game_id=game_id, player_id=player.id, question_id=current_question_id, answer=answer)
            db.session.add(new_answer)
        db.session.commit()
        logger.debug(f"Game {game_id}: Recorded answer '{answer}' for {username} on question_id {current_question_id}")
        socketio.emit('player_answered', {'username': username}, room=game_id)
        active_players = Player.query.filter_by(game_id=game_id, disconnected=False).all()
        answers_submitted = Answer.query.filter_by(game_id=game_id, question_id=current_question_id).count()
        total_players = Player.query.filter_by(game_id=game_id).count()
        logger.debug(f"Game {game_id}: Active players: {[p.username for p in active_players]}, Total: {len(active_players)}")
        logger.debug(f"Game {game_id}: Answers submitted: {answers_submitted}, Total players: {total_players}")
        if answers_submitted >= len(active_players) and answers_submitted >= total_players:
            logger.debug(f"Game {game_id}: All {len(active_players)} active players answered out of {total_players} total, processing results")
            if game_id in active_timers:
                active_timers[game_id].cancel()
                del active_timers[game_id]
                logger.debug(f"Game {game_id}: Timer cancelled due to all answers submitted")
            process_round_results(game_id)

@socketio.on('submit_feedback')
def handle_feedback(data):
    game_id = data.get('game_id')
    topic_id = data.get('topic_id')
    username = data.get('username')
    rating = data.get('rating')
    with app.app_context():
        player = Player.query.filter_by(username=username, game_id=game_id).first()
        topic = Topic.query.get(topic_id)
        if not player or not topic:
            logger.error(f"Invalid player {username} or topic {topic_id} in game {game_id}")
            socketio.emit('error', {'message': 'Invalid player or topic'}, to=request.sid)
            return
        if not isinstance(rating, bool):
            logger.error(f"Invalid rating value: {rating} for {username} in game {game_id}")
            socketio.emit('error', {'message': 'Invalid rating'}, to=request.sid)
            return
        try:
            rating_value = 1 if rating else 0
            existing_rating = Rating.query.filter_by(game_id=game_id, player_id=player.id, topic_id=topic_id).first()
            if existing_rating:
                existing_rating.rating = rating_value
            else:
                new_rating = Rating(game_id=game_id, player_id=player.id, topic_id=topic_id, rating=rating_value)
                db.session.add(new_rating)
            db.session.commit()
            logger.debug(f"Player {username} rated topic {topic.normalized_name} as {'Like' if rating_value else 'Dislike'} in game {game_id}")
        except SQLAlchemyError as e:
            logger.error(f"Failed to save rating for {username} on topic {topic_id} in game {game_id}: {str(e)}")
            db.session.rollback()
            socketio.emit('error', {'message': 'Failed to save rating'}, to=request.sid)

@socketio.on('send_chat_message')
def handle_chat_message(data):
    game_id = data.get('game_id')
    username = data.get('username')
    message = data.get('message')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        player = Player.query.filter_by(game_id=game_id, username=username).first()
        if not player or player.disconnected:
            socketio.emit('error', {'message': 'Player not in game or disconnected'}, to=request.sid)
            return
        if game_id not in unread_messages:
            unread_messages[game_id] = {}
        players = Player.query.filter_by(game_id=game_id).all()
        for p in players:
            if p.username != username and not p.disconnected:
                if p.username not in unread_messages[game_id]:
                    unread_messages[game_id][p.username] = 0
                unread_messages[game_id][p.username] += 1
                logger.debug(f"Game {game_id}: Unread count for {p.username} increased to {unread_messages[game_id][p.username]}")
        socketio.emit('chat_message', {'username': username, 'message': message}, room=game_id)
        for p in players:
            if p.username != username and not p.disconnected:
                socketio.emit('update_unread_count', {'count': unread_messages[game_id][p.username]}, to=p.sid if hasattr(p, 'sid') else None)
        logger.debug(f"Game {game_id}: Chat message from {username}: {message}")
        update_game_activity(game_id)

@socketio.on('reset_unread_count')
def handle_reset_unread_count(data):
    game_id = data.get('game_id')
    username = data.get('username')
    with app.app_context():
        if game_id in unread_messages and username in unread_messages[game_id]:
            unread_messages[game_id][username] = 0
            socketio.emit('update_unread_count', {'count': 0}, to=request.sid)
            logger.debug(f"Game {game_id}: Unread count reset to 0 for {username}")

@socketio.on('voice_offer')
def handle_voice_offer(data):
    game_id = data.get('game_id')
    from_username = data.get('from')
    to_username = data.get('to')
    offer = data.get('offer')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        to_player = Player.query.filter_by(game_id=game_id, username=to_username).first()
        if not to_player or to_player.disconnected or not to_player.sid:
            logger.info(f"Game {game_id}: Cannot send offer to {to_username} - disconnected or no SID")
            return
        logger.info(f"Game {game_id}: Received voice_offer from {from_username} to {to_username}")
        socketio.emit('voice_offer', {'from': from_username, 'offer': offer}, to=to_player.sid)
        logger.info(f"Game {game_id}: Relayed voice_offer from {from_username} to {to_username} (SID: {to_player.sid})")

@socketio.on('voice_answer')
def handle_voice_answer(data):
    game_id = data.get('game_id')
    from_username = data.get('from')
    to_username = data.get('to')
    answer = data.get('answer')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        to_player = Player.query.filter_by(game_id=game_id, username=to_username).first()
        if not to_player or to_player.disconnected or not to_player.sid:
            logger.info(f"Game {game_id}: Cannot send answer to {to_username} - disconnected or no SID")
            return
        logger.info(f"Game {game_id}: Received voice_answer from {from_username} to {to_username}")
        socketio.emit('voice_answer', {'from': from_username, 'answer': answer}, to=to_player.sid)
        logger.info(f"Game {game_id}: Relayed voice_answer from {from_username} to {to_username} (SID: {to_player.sid})")

@socketio.on('voice_candidate')
def handle_voice_candidate(data):
    game_id = data.get('game_id')
    from_username = data.get('from')
    to_username = data.get('to')
    candidate = data.get('candidate')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        to_player = Player.query.filter_by(game_id=game_id, username=to_username).first()
        if not to_player or to_player.disconnected or not to_player.sid:
            logger.info(f"Game {game_id}: Cannot send ICE candidate to {to_username} - disconnected or no SID")
            return
        logger.info(f"Game {game_id}: Received voice_candidate from {from_username} to {to_username}")
        socketio.emit('voice_candidate', {'from': from_username, 'candidate': candidate}, to=to_player.sid)
        logger.info(f"Game {game_id}: Relayed voice_candidate from {from_username} to {to_username} (SID: {to_player.sid})")

@socketio.on('speaking_status')
def handle_speaking_status(data):
    game_id = data.get('game_id')
    username = data.get('username')
    speaking = data.get('speaking')
    with app.app_context():
        game = Game.query.filter_by(id=game_id).first()
        if not game:
            socketio.emit('error', {'message': 'Game not found'}, to=request.sid)
            return
        logger.info(f"Game {game_id}: {username} is {'speaking' if speaking else 'not speaking'}")
        socketio.emit('speaking_status', {'username': username, 'speaking': speaking}, room=game_id)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)