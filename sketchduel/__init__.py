from flask import Blueprint

# Initialize SketchDuel blueprint
sketchduel_app = Blueprint('sketchduel', __name__, template_folder='templates', static_folder='../../static')

# Import the app and models after blueprint definition to avoid circular imports
from . import app  # Import the SketchDuel app routes
from . import models  # Import the SketchDuel models

# Ensure the blueprint uses the same database and SocketIO instances from the root app
from .. import db, socketio