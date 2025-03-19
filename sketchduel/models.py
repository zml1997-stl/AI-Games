from app import db

class SketchDuelRoom(db.Model):
    __tablename__ = 'sketchduel_rooms'
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(10), unique=True, nullable=False)
    player1_username = db.Column(db.String(50), nullable=True)  # Player 1 (host)
    player2_username = db.Column(db.String(50), nullable=True)  # Player 2
    current_drawer_username = db.Column(db.String(50), nullable=True)
    score_p1 = db.Column(db.Integer, default=0)
    score_p2 = db.Column(db.Integer, default=0)
    last_activity = db.Column(db.DateTime, default=db.func.now())
    status = db.Column(db.String(20), default='waiting')  # Added status field

class SketchDuelGameState(db.Model):
    __tablename__ = 'sketchduel_game_states'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('sketchduel_rooms.id'), nullable=False)
    prompt = db.Column(db.String(255), nullable=False)
    is_drawing_phase = db.Column(db.Boolean, default=True)
    time_left = db.Column(db.Integer, default=60)