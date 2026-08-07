"""Flask extension instances, initialized by the application factory."""

from flask_jwt_extended import JWTManager
from flask_pymongo import PyMongo

mongo = PyMongo()
jwt = JWTManager()
