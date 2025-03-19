from app import app, db
from models import *  # Import all trivia models
from sketchduel.models import *  # Import all SketchDuel models

with app.app_context():
    db.create_all()
    print("All tables created successfully.")
exit()