from app import db

class SketchDuelRoom(db.Model):
    __tablename__ = 'sketchduel_rooms'
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(10), unique=True, nullable=False)
    player1_username = db.Column(db.String(50), nullable=True)  # Player 1's username
    player2_username = db.Column(db.String(50), nullable=True)  # Player 2's username
    current_drawer_username = db.Column(db.String(50), nullable=True)  # Current drawer's username
    score_p1 = db.Column(db.Integer, default=0)  # Player 1's score
    score_p2 = db.Column(db.Integer, default=0)  # Player 2's score
    last_activity = db.Column(db.DateTime, default=db.func.now())  # Track last activity for inactivity cleanup

class SketchDuelGameState(db.Model):
    __tablename__ = 'sketchduel_game_states'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('sketchduel_rooms.id'), nullable=False)
    prompt = db.Column(db.String(255), nullable=False)  # Current drawing prompt
    is_drawing_phase = db.Column(db.Boolean, default=True)  # True for drawing, False for guessing
    time_left = db.Column(db.Integer, default=60)  # Time left in the current phase (seconds)