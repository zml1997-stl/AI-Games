from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Game(db.Model):
    __tablename__ = 'games'
    id = db.Column(db.String(6), primary_key=True)  # Game ID (e.g., ABC123)
    host = db.Column(db.String(50), nullable=False)  # Host player name
    started = db.Column(db.Boolean, default=False)  # Whether the game has started
    round_number = db.Column(db.Integer, default=0)  # Current round number
    created_at = db.Column(db.DateTime, default=db.func.now())  # Creation timestamp
    
    players = db.relationship('Player', backref='game', lazy=True)  # One-to-many with players
    rounds = db.relationship('Round', backref='game', lazy=True)   # One-to-many with rounds

class Player(db.Model):
    __tablename__ = 'players'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_id = db.Column(db.String(6), db.ForeignKey('games.id'), nullable=False)  # Foreign key to Game
    name = db.Column(db.String(50), nullable=False)  # Player name
    score = db.Column(db.Integer, default=0)  # Total score
    
    __table_args__ = (db.UniqueConstraint('game_id', 'name', name='unique_player_per_game'),)

class Round(db.Model):
    __tablename__ = 'rounds'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_id = db.Column(db.String(6), db.ForeignKey('games.id'), nullable=False)  # Foreign key to Game
    round_number = db.Column(db.Integer, nullable=False)  # Round number (1-5)
    drawer = db.Column(db.String(50), nullable=False)  # Player name of the Drawer
    prompt = db.Column(db.String(100), nullable=False)  # Drawing prompt
    start_time = db.Column(db.DateTime, default=db.func.now())  # Round start time
    end_time = db.Column(db.DateTime, nullable=True)  # Round end time
    
    guesses = db.relationship('Guess', backref='round', lazy=True)  # One-to-many with guesses

class Guess(db.Model):
    __tablename__ = 'guesses'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)  # Foreign key to Round
    player_name = db.Column(db.String(50), nullable=False)  # Player who made the guess
    guess_text = db.Column(db.String(100), nullable=False)  # Guess text
    timestamp = db.Column(db.DateTime, default=db.func.now())  # When the guess was made
    correct = db.Column(db.Boolean, default=False)  # Whether the guess was correct

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()