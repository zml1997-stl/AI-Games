from app import app, db
from models import Game, Player, Topic, Question, Answer, Rating  # Import trivia models from root models.py
from sketchduel.models import SketchDuelRoom, SketchDuelGameState  # Import SketchDuel models from sketchduel/models.py

with app.app_context():
    db.drop_all()  # Optional: Drop existing tables (use with caution in production)
    db.create_all()  # Create all tables defined in the models
    print("Tables created successfully!")