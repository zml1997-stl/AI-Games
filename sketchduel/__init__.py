from flask import Blueprint
from app import db, socketio  # Absolute import from root app.py

# Initialize SketchDuel blueprint
sketchduel_app = Blueprint('sketchduel', __name__, template_folder='templates', static_folder='../../static')

# Import routes (this ensures routes from app.py are registered)
from .app import *  # Import all routes defined in sketchduel/app.py

# Optional: Any additional initialization code can go here